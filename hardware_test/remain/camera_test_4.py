#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monitor and capture two externally triggered YLX-2UQ2 stereo cameras.

Each 2UQ2 video node produces one side-by-side frame.  Two video nodes are
opened concurrently and each frame is split into left/right images, yielding
four images for every common external-trigger event.
"""

import argparse
import csv
from collections import deque
from pathlib import Path
import queue
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
            f"Failed to set {device} {control}={value}:\n"
            f"{result.stderr.strip()}"
        )

    verify = subprocess.run(
        ["v4l2-ctl", "-d", device, f"--get-ctrl={control}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if verify.returncode == 0:
        print(f"[V4L2 {device}] {verify.stdout.strip()}")
    else:
        print(
            f"[V4L2 {device}] Set succeeded, verify failed: "
            f"{verify.stderr.strip()}"
        )


def open_camera(device: str, width: int, height: int, fps: float):
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open camera {device}")

    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"[{device}] Requested MJPG {width}x{height} @ {fps:g} fps")
    print(
        f"[{device}] Actual    MJPG {actual_width}x{actual_height} "
        f"@ {actual_fps:.2f} fps"
    )

    if actual_width != width or actual_height != height:
        cap.release()
        raise RuntimeError(
            f"{device} rejected requested format: requested={width}x{height}, "
            f"actual={actual_width}x{actual_height}"
        )
    if actual_width <= 0 or actual_width % 2:
        cap.release()
        raise RuntimeError(
            f"{device} side-by-side width must be positive and even"
        )
    return cap


class CameraReader(threading.Thread):
    def __init__(self, name: str, cap: cv2.VideoCapture, queue_size: int = 16):
        super().__init__(daemon=True)
        self.name = name
        self.cap = cap
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latest_frame = None
        self.latest_received_ns = 0
        self.frame_count = 0
        self.read_errors = 0
        self.dropped_frames = 0
        self.timestamps_ns = deque(maxlen=256)
        self.frames = queue.Queue(maxsize=queue_size)

    def run(self) -> None:
        print(f"[{self.name}] Stream started; cap.read() is active")
        while not self.stop_event.is_set():
            try:
                ok, frame = self.cap.read()
            except cv2.error:
                if self.stop_event.is_set():
                    break
                with self.lock:
                    self.read_errors += 1
                time.sleep(0.001)
                continue

            received_ns = time.monotonic_ns()
            if not ok or frame is None:
                with self.lock:
                    self.read_errors += 1
                time.sleep(0.001)
                continue

            with self.lock:
                self.latest_frame = frame
                self.latest_received_ns = received_ns
                self.frame_count += 1
                camera_count = self.frame_count
                self.timestamps_ns.append(received_ns)

            item = (camera_count, received_ns, frame)
            try:
                self.frames.put_nowait(item)
            except queue.Full:
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass
                with self.lock:
                    self.dropped_frames += 1
                self.frames.put_nowait(item)

    def reset_after_arm(self) -> int:
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                break
        with self.lock:
            self.timestamps_ns.clear()
            return self.frame_count

    def get_frame_nowait(self):
        try:
            return self.frames.get_nowait()
        except queue.Empty:
            return None

    def snapshot(self):
        with self.lock:
            frame = None
            if self.latest_frame is not None:
                frame = self.latest_frame.copy()
            return {
                "frame": frame,
                "frame_count": self.frame_count,
                "read_errors": self.read_errors,
                "dropped_frames": self.dropped_frames,
                "received_ns": self.latest_received_ns,
                "timestamps_ns": list(self.timestamps_ns),
            }

    def stop(self) -> None:
        self.stop_event.set()


def calculate_frequency(timestamps_ns, now_ns, window_s):
    cutoff_ns = now_ns - int(window_s * 1_000_000_000)
    recent = [value for value in timestamps_ns if value >= cutoff_ns]
    if len(recent) < 2:
        return None, None, None, len(recent)

    intervals_ms = [
        (current - previous) / 1_000_000.0
        for previous, current in zip(recent, recent[1:])
        if current > previous
    ]
    if not intervals_ms:
        return None, None, None, len(recent)

    period_ms = statistics.median(intervals_ms)
    frequency_hz = 1000.0 / period_ms if period_ms > 0 else None
    jitter_ms = statistics.pstdev(intervals_ms) if len(intervals_ms) > 1 else 0.0
    return frequency_hz, period_ms, jitter_ms, len(recent)


def trigger_state(snapshot, expected_hz, tolerance, window_s):
    now_ns = time.monotonic_ns()
    measured_hz, period_ms, jitter_ms, recent_frames = calculate_frequency(
        snapshot["timestamps_ns"], now_ns, window_s
    )
    last_age_s = None
    if snapshot["timestamps_ns"]:
        last_age_s = (now_ns - snapshot["timestamps_ns"][-1]) / 1e9

    if last_age_s is None or last_age_s > 2.5 / expected_hz:
        state = "NO NEW TRIGGER"
    elif measured_hz is None:
        state = "WAITING FOR PWM"
    elif abs(measured_hz - expected_hz) / expected_hz <= tolerance:
        state = "TRIGGER PASS"
    else:
        state = "FREQUENCY MISMATCH"
    return state, measured_hz, period_ms, jitter_ms, recent_frames


def split_stereo(frame, swap=False):
    if frame is None or frame.ndim != 3 or frame.shape[1] % 2:
        raise RuntimeError(f"Invalid side-by-side frame shape: {None if frame is None else frame.shape}")
    half = frame.shape[1] // 2
    first = frame[:, :half]
    second = frame[:, half:]
    return (second, first) if swap else (first, second)


def save_quad(output_dir, index, item_a, item_b, swap_a, swap_b):
    count_a, time_a_ns, frame_a = item_a
    count_b, time_b_ns, frame_b = item_b
    a_left, a_right = split_stereo(frame_a, swap_a)
    b_left, b_right = split_stereo(frame_b, swap_b)

    paths = {
        "a_left": output_dir / f"a_left_{index:06d}.jpg",
        "a_right": output_dir / f"a_right_{index:06d}.jpg",
        "b_left": output_dir / f"b_left_{index:06d}.jpg",
        "b_right": output_dir / f"b_right_{index:06d}.jpg",
    }
    images = {
        "a_left": a_left,
        "a_right": a_right,
        "b_left": b_left,
        "b_right": b_right,
    }
    for name, path in paths.items():
        if not cv2.imwrite(str(path), images[name]):
            raise RuntimeError(f"Failed to save {path}")

    return {
        "pair_index": index,
        "camera_count_a": count_a,
        "camera_count_b": count_b,
        "received_a_ns": time_a_ns,
        "received_b_ns": time_b_ns,
        "usb_receive_delta_us": abs(time_a_ns - time_b_ns) / 1000.0,
        **{name: str(path) for name, path in paths.items()},
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Monitor two triggered 2UQ2 devices and save four images per event"
    )
    parser.add_argument("--device-a", default="/dev/video0")
    parser.add_argument("--device-b", default="/dev/video2")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=float, default=210.0)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--arm-delay", type=float, default=1.0)
    parser.add_argument("--control", default="backlight_compensation")
    parser.add_argument("--value", type=int, default=1)
    parser.add_argument("--expected-trigger-hz", type=float, default=5.0)
    parser.add_argument("--frequency-tolerance", type=float, default=0.20)
    parser.add_argument("--status-interval", type=float, default=1.0)
    parser.add_argument("--measurement-window", type=float, default=5.0)
    parser.add_argument("--save-frames", type=int, default=20)
    parser.add_argument("--output", default="quad_2uq2_capture")
    parser.add_argument("--swap-a", action="store_true")
    parser.add_argument("--swap-b", action="store_true")
    parser.add_argument("--no-preview", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.device_a == args.device_b:
        raise ValueError("--device-a and --device-b must be different")
    if args.expected_trigger_hz <= 0:
        raise ValueError("--expected-trigger-hz must be greater than zero")
    if args.save_frames < 0:
        raise ValueError("--save-frames cannot be negative")

    output_dir = Path(args.output).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    cap_a = None
    cap_b = None
    reader_a = None
    reader_b = None
    records = []
    try:
        cap_a = open_camera(args.device_a, args.width, args.height, args.fps)
        cap_b = open_camera(args.device_b, args.width, args.height, args.fps)
        reader_a = CameraReader("Camera A", cap_a)
        reader_b = CameraReader("Camera B", cap_b)
        reader_a.start()
        reader_b.start()

        # Preserve the verified order: start both streams first, then set BLS.
        time.sleep(args.delay)
        set_v4l2_control(args.device_a, args.control, args.value)
        set_v4l2_control(args.device_b, args.control, args.value)
        time.sleep(args.arm_delay)
        base_a = reader_a.reset_after_arm()
        base_b = reader_b.reset_after_arm()
        print(f"[Main] BLS set; baselines A={base_a}, B={base_b}")
        print("[Main] ARMED: start the common PWM trigger now")

        previous_a = base_a
        previous_b = base_b
        last_status = time.monotonic()
        saved = 0
        pending_a = None
        pending_b = None
        preview_enabled = not args.no_preview

        while True:
            if pending_a is None:
                pending_a = reader_a.get_frame_nowait()
            if pending_b is None:
                pending_b = reader_b.get_frame_nowait()

            if pending_a is not None and pending_b is not None:
                if args.save_frames > 0 and saved < args.save_frames:
                    record = save_quad(
                        output_dir,
                        saved,
                        pending_a,
                        pending_b,
                        args.swap_a,
                        args.swap_b,
                    )
                    records.append(record)
                    saved += 1
                    print(
                        f"[Save] pair={saved}/{args.save_frames} "
                        f"USB receive delta={record['usb_receive_delta_us']:.1f} us"
                    )
                pending_a = None
                pending_b = None
                if args.save_frames > 0 and saved >= args.save_frames:
                    print("[Main] Requested four-image sets saved")
                    break

            now = time.monotonic()
            snap_a = reader_a.snapshot()
            snap_b = reader_b.snapshot()
            if now - last_status >= args.status_interval:
                elapsed = now - last_status
                fps_a = (snap_a["frame_count"] - previous_a) / elapsed
                fps_b = (snap_b["frame_count"] - previous_b) / elapsed
                state_a = trigger_state(
                    snap_a,
                    args.expected_trigger_hz,
                    args.frequency_tolerance,
                    args.measurement_window,
                )
                state_b = trigger_state(
                    snap_b,
                    args.expected_trigger_hz,
                    args.frequency_tolerance,
                    args.measurement_window,
                )

                def show(name, state, received_fps, snapshot, base):
                    status, hz, period, jitter, recent = state
                    hz_text = "N/A" if hz is None else f"{hz:.3f}Hz"
                    period_text = "N/A" if period is None else f"{period:.3f}ms"
                    jitter_text = "N/A" if jitter is None else f"{jitter:.3f}ms"
                    print(
                        f"[{name}] state={status}, measured={hz_text}, "
                        f"frames/s={received_fps:.2f}, period={period_text}, "
                        f"jitter={jitter_text}, recent={recent}, "
                        f"total={snapshot['frame_count'] - base}, "
                        f"errors={snapshot['read_errors']}, "
                        f"queue_drops={snapshot['dropped_frames']}"
                    )

                show("A", state_a, fps_a, snap_a, base_a)
                show("B", state_b, fps_b, snap_b, base_b)
                if snap_a["received_ns"] and snap_b["received_ns"]:
                    print(
                        "[Sync] Latest USB receive delta="
                        f"{abs(snap_a['received_ns'] - snap_b['received_ns']) / 1e6:.3f} ms "
                        "(diagnostic only, not exposure skew)"
                    )
                previous_a = snap_a["frame_count"]
                previous_b = snap_b["frame_count"]
                last_status = now

            if preview_enabled and snap_a["frame"] is not None and snap_b["frame"] is not None:
                try:
                    preview = cv2.vconcat([snap_a["frame"], snap_b["frame"]])
                    cv2.imshow("Two YLX-2UQ2 Trigger Monitor", preview)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (27, ord("q")):
                        break
                except cv2.error as exc:
                    preview_enabled = False
                    cv2.destroyAllWindows()
                    print(f"[Preview] GUI unavailable; terminal mode: {exc}")
            else:
                time.sleep(0.005)

    except KeyboardInterrupt:
        print("\n[Main] Interrupted")
    finally:
        if records:
            csv_path = output_dir / "capture_pairs.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=list(records[0].keys()))
                writer.writeheader()
                writer.writerows(records)
            print(f"[Main] Pair metadata saved to {csv_path.resolve()}")

        if reader_a is not None:
            reader_a.stop()
        if reader_b is not None:
            reader_b.stop()
        if cap_a is not None:
            cap_a.release()
        if cap_b is not None:
            cap_b.release()
        if reader_a is not None:
            reader_a.join(timeout=1.0)
        if reader_b is not None:
            reader_b.join(timeout=1.0)
        cv2.destroyAllWindows()
        print("[Main] Resources released")


if __name__ == "__main__":
    main()
