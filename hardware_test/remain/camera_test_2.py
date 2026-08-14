#!/usr/bin/env python3
"""Monitor YLX-2UQ2 manual-trigger frames and estimate trigger frequency."""

import argparse
from collections import deque
from pathlib import Path
import statistics
import subprocess
import threading
import time

import cv2


def set_v4l2_control(device: str, control: str, value: int) -> None:
    result = subprocess.run(
        ["v4l2-ctl", "-d", device, f"--set-ctrl={control}={value}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to set {control}={value}:\n{result.stderr.strip()}"
        )

    verify = subprocess.run(
        ["v4l2-ctl", "-d", device, f"--get-ctrl={control}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if verify.returncode == 0:
        print(f"[V4L2] {verify.stdout.strip()}")
    else:
        print(f"[V4L2] Set succeeded, verify failed: {verify.stderr.strip()}")


class CameraReader(threading.Thread):
    def __init__(self, cap: cv2.VideoCapture):
        super().__init__(daemon=True)
        self.cap = cap
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latest_frame = None
        self.latest_received_ns = 0
        self.frame_count = 0
        self.read_errors = 0
        self.timestamps_ns = deque(maxlen=256)

    def run(self) -> None:
        print("[Camera] Stream started; cap.read() is active")
        while not self.stop_event.is_set():
            try:
                ok, frame = self.cap.read()
            except cv2.error:
                if self.stop_event.is_set():
                    break
                self.read_errors += 1
                time.sleep(0.001)
                continue
            received_ns = time.monotonic_ns()
            if not ok or frame is None:
                self.read_errors += 1
                time.sleep(0.001)
                continue

            with self.lock:
                self.latest_frame = frame
                self.latest_received_ns = received_ns
                self.frame_count += 1
                self.timestamps_ns.append(received_ns)

    def reset_trigger_statistics(self) -> int:
        with self.lock:
            self.timestamps_ns.clear()
            return self.frame_count

    def snapshot(self):
        with self.lock:
            frame = None
            if self.latest_frame is not None:
                frame = self.latest_frame.copy()
            return {
                "frame": frame,
                "frame_count": self.frame_count,
                "read_errors": self.read_errors,
                "received_ns": self.latest_received_ns,
                "timestamps_ns": list(self.timestamps_ns),
            }

    def stop(self) -> None:
        self.stop_event.set()


def calculate_frequency(timestamps_ns, now_ns, window_s):
    cutoff_ns = now_ns - int(window_s * 1_000_000_000)
    recent_timestamps = [value for value in timestamps_ns if value >= cutoff_ns]
    if len(recent_timestamps) < 2:
        return None, None, None, len(recent_timestamps)

    intervals_ms = [
        (current - previous) / 1_000_000.0
        for previous, current in zip(recent_timestamps, recent_timestamps[1:])
        if current > previous
    ]
    if not intervals_ms:
        return None, None, None, len(recent_timestamps)

    period_ms = statistics.median(intervals_ms)
    frequency_hz = 1000.0 / period_ms if period_ms > 0 else None
    jitter_ms = statistics.pstdev(intervals_ms) if len(intervals_ms) > 1 else 0.0
    return frequency_hz, period_ms, jitter_ms, len(recent_timestamps)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start stream, set BLS, monitor trigger rate, and preview frames"
    )
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=float, default=210)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument(
        "--arm-delay",
        type=float,
        default=1.0,
        help="seconds to discard buffered frames after setting BLS",
    )
    parser.add_argument("--control", default="backlight_compensation")
    parser.add_argument("--value", type=int, default=1)
    parser.add_argument("--expected-trigger-hz", type=float, default=5.0)
    parser.add_argument("--frequency-tolerance", type=float, default=0.20)
    parser.add_argument("--status-interval", type=float, default=1.0)
    parser.add_argument("--measurement-window", type=float, default=5.0)
    parser.add_argument("--output", default="camera_test_snapshots")
    parser.add_argument(
        "--save-frames",
        type=int,
        default=0,
        help="automatically save this many new frames after BLS is set",
    )
    parser.add_argument(
        "--exit-after-save",
        action="store_true",
        help="exit after --save-frames images have been saved",
    )
    parser.add_argument("--no-preview", action="store_true")
    return parser.parse_args()


def trigger_state(measured_hz, expected_hz, tolerance):
    if measured_hz is None:
        return "WAITING FOR PWM", (0, 255, 255)
    relative_error = abs(measured_hz - expected_hz) / expected_hz
    if relative_error <= tolerance:
        return "TRIGGER PASS", (0, 255, 0)
    return "FREQUENCY MISMATCH", (0, 0, 255)


def add_overlay(frame, lines, state_color):
    output = frame.copy()
    overlay = output.copy()
    panel_height = 20 + len(lines) * 25
    cv2.rectangle(overlay, (0, 0), (output.shape[1], panel_height), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, output, 0.45, 0, output)
    for index, line in enumerate(lines):
        color = state_color if index == 0 else (255, 255, 255)
        cv2.putText(
            output,
            line,
            (10, 25 + index * 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )
    return output


def main() -> None:
    args = parse_args()
    if args.expected_trigger_hz <= 0:
        raise ValueError("--expected-trigger-hz must be greater than zero")

    cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera {args.device}")

    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"[Camera] Requested {args.width}x{args.height} @ {args.fps:g} fps")
    print(f"[Camera] Actual    {actual_width}x{actual_height} @ {actual_fps:.2f} fps")

    output_dir = Path(args.output).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    reader = CameraReader(cap)
    reader.start()

    try:
        time.sleep(args.delay)
        set_v4l2_control(args.device, args.control, args.value)
        time.sleep(args.arm_delay)
        trigger_base_count = reader.reset_trigger_statistics()
        print(f"[Main] BLS set; baseline frame count={trigger_base_count}")
        print("[Main] Start PWM in another terminal")
        print("[Main] Preview keys: s=save image, q/Esc=quit")

        previous_count = trigger_base_count
        last_status_time = time.monotonic()
        overlay_lines = ["WAITING FOR PWM"]
        overlay_color = (0, 255, 255)
        preview_enabled = not args.no_preview
        saved_frames = 0
        last_saved_camera_count = trigger_base_count

        while True:
            snapshot = reader.snapshot()
            frame = snapshot["frame"]
            now = time.monotonic()

            if (
                args.save_frames > 0
                and saved_frames < args.save_frames
                and frame is not None
                and snapshot["frame_count"] > last_saved_camera_count
            ):
                filename = output_dir / f"trigger_{saved_frames:06d}.jpg"
                if not cv2.imwrite(str(filename), frame):
                    raise RuntimeError(f"Failed to save {filename}")
                saved_frames += 1
                last_saved_camera_count = snapshot["frame_count"]
                print(
                    f"[Image] Saved {filename} "
                    f"({saved_frames}/{args.save_frames})"
                )
                if args.exit_after_save and saved_frames >= args.save_frames:
                    print("[Main] Requested frames saved")
                    break

            if now - last_status_time >= args.status_interval:
                elapsed = now - last_status_time
                new_frames = snapshot["frame_count"] - previous_count
                received_fps = new_frames / elapsed
                now_ns = time.monotonic_ns()
                measured_hz, period_ms, jitter_ms, recent_frames = calculate_frequency(
                    snapshot["timestamps_ns"], now_ns, args.measurement_window
                )
                state, overlay_color = trigger_state(
                    measured_hz,
                    args.expected_trigger_hz,
                    args.frequency_tolerance,
                )
                last_frame_age_s = None
                if snapshot["timestamps_ns"]:
                    last_frame_age_s = (
                        now_ns - snapshot["timestamps_ns"][-1]
                    ) / 1_000_000_000.0
                stale_after_s = 2.5 / args.expected_trigger_hz
                if last_frame_age_s is None or last_frame_age_s > stale_after_s:
                    state = "NO NEW TRIGGER"
                    overlay_color = (0, 255, 255)

                frequency_text = "N/A" if measured_hz is None else f"{measured_hz:.3f} Hz"
                period_text = "N/A" if period_ms is None else f"{period_ms:.3f} ms"
                jitter_text = "N/A" if jitter_ms is None else f"{jitter_ms:.3f} ms"
                image_text = "No image"
                if frame is not None:
                    image_text = (
                        f"Image: {frame.shape[1]}x{frame.shape[0]}, "
                        f"channels={frame.shape[2]}, mean={frame.mean():.1f}"
                    )

                overlay_lines = [
                    state,
                    f"Expected: {args.expected_trigger_hz:.3f} Hz",
                    f"Measured: {frequency_text}, period: {period_text}",
                    f"Frames/s: {received_fps:.2f}, jitter: {jitter_text}",
                    f"Recent frames ({args.measurement_window:g}s): {recent_frames}",
                    f"Frames after BLS: {snapshot['frame_count'] - trigger_base_count}",
                    f"Read errors: {snapshot['read_errors']}",
                    image_text,
                ]
                print(
                    f"[Trigger] state={state}, expected={args.expected_trigger_hz:.3f}Hz, "
                    f"measured={frequency_text}, frames/s={received_fps:.2f}, "
                    f"period={period_text}, jitter={jitter_text}, "
                    f"recent_frames={recent_frames}, "
                    f"total={snapshot['frame_count'] - trigger_base_count}, "
                    f"errors={snapshot['read_errors']}"
                )
                print(f"[Image] {image_text}")
                previous_count = snapshot["frame_count"]
                last_status_time = now

            key = -1
            if preview_enabled and frame is not None:
                preview = add_overlay(frame, overlay_lines, overlay_color)
                try:
                    cv2.imshow("YLX-2UQ2 PWM Trigger Monitor", preview)
                    key = cv2.waitKey(1) & 0xFF
                except cv2.error as exc:
                    preview_enabled = False
                    cv2.destroyAllWindows()
                    print(
                        "[Preview] GUI unavailable; continuing in terminal-only "
                        f"mode: {exc}"
                    )
            else:
                time.sleep(0.01)

            if key in (27, ord("q")):
                break
            if key == ord("s") and frame is not None:
                filename = output_dir / f"snapshot_{time.time_ns()}.jpg"
                if cv2.imwrite(str(filename), frame):
                    print(f"[Image] Saved {filename}")
                else:
                    print(f"[Image] Failed to save {filename}")
    except KeyboardInterrupt:
        print("\n[Main] Interrupted")
    finally:
        reader.stop()
        cap.release()
        reader.join(timeout=1.0)
        cv2.destroyAllWindows()
        print("[Main] Resources released")


if __name__ == "__main__":
    main()
