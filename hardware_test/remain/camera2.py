#!/usr/bin/env python3

import argparse
import subprocess
import threading
import time

import cv2


def set_v4l2_control(device: str, control: str, value: int) -> None:
    """通过 v4l2-ctl 设置摄像头控制参数。"""
    command = [
        "v4l2-ctl",
        "-d",
        device,
        f"--set-ctrl={control}={value}",
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"设置 {control}={value} 失败：\n"
            f"{result.stderr.strip()}"
        )

    # 读取并验证当前值
    verify = subprocess.run(
        [
            "v4l2-ctl",
            "-d",
            device,
            f"--get-ctrl={control}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if verify.returncode == 0:
        print(f"[V4L2] {verify.stdout.strip()}")
    else:
        print(f"[V4L2] 已执行设置，但读取验证失败：{verify.stderr.strip()}")


class CameraReader(threading.Thread):
    """持续读取摄像头，保存最新一帧。"""

    def __init__(self, cap: cv2.VideoCapture):
        super().__init__(daemon=True)
        self.cap = cap
        self.stop_event = threading.Event()
        self.lock = threading.Lock()

        self.latest_frame = None
        self.frame_count = 0
        self.read_errors = 0

    def run(self) -> None:
        print("[Camera] 开始拉流，正在执行 cap.read()")

        while not self.stop_event.is_set():
            ret, frame = self.cap.read()

            if not ret:
                self.read_errors += 1
                time.sleep(0.001)
                continue

            with self.lock:
                self.latest_frame = frame
                self.frame_count += 1

    def get_latest_frame(self):
        with self.lock:
            if self.latest_frame is None:
                return None
            return self.latest_frame.copy()

    def stop(self) -> None:
        self.stop_event.set()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="启动摄像头拉流后设置 backlight_compensation=1"
    )
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=float, default=210)
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="开始拉流后等待多少秒设置 BLS",
    )
    parser.add_argument(
        "--no-preview",
        action="store_true",
        help="不显示预览窗口",
    )
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2)

    if not cap.isOpened():
        raise RuntimeError(f"无法打开摄像头：{args.device}")

    # 设置 MJPEG、分辨率和帧率
    cap.set(
        cv2.CAP_PROP_FOURCC,
        cv2.VideoWriter_fourcc(*"MJPG"),
    )
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)

    # 尽量减少缓存延迟，部分 V4L2 后端可能不支持
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    print(f"[Camera] 后端：{cap.getBackendName()}")
    print(
        "[Camera] 实际参数："
        f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
        f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} @ "
        f"{cap.get(cv2.CAP_PROP_FPS):.2f} FPS"
    )

    # 先启动读取线程。cap.read() 会触发底层 VIDIOC_STREAMON。
    reader = CameraReader(cap)
    reader.start()

    try:
        print(f"[Main] 等待 {args.delay} 秒，让摄像头进入拉流状态")
        time.sleep(args.delay)

        # 拉流启动后，再设置 BLS=1
        print("[Main] 设置 backlight_compensation=1")
        set_v4l2_control(
            args.device,
            "backlight_compensation",
            1,
        )

        print("[Main] BLS 设置完成，按 q 退出")

        last_count = 0
        last_time = time.monotonic()
        frame_id = 0
        while True:
            frame = reader.get_latest_frame()

            if frame is not None and not args.no_preview:
                cv2.imshow("Camera", frame)

            now = time.monotonic()
            if now - last_time >= 1.0:
                current_count = reader.frame_count
                measured_fps = (current_count - last_count) / (now - last_time)

                print(
                    f"\r[Camera] 帧数={current_count}, "
                    f"测量帧率={measured_fps:.1f} FPS, "
                    f"读取失败={reader.read_errors}",
                    end="",
                    flush=True,
                )
                
                if last_count != current_count:
                    cv2.imwrite(f"camera2/image_{frame_id}.jpg", frame)
                    frame_id += 1


                last_count = current_count
                last_time = now

                

            if not args.no_preview:
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            else:
                time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n[Main] 收到 Ctrl+C")

    finally:
        print("\n[Main] 正在关闭摄像头")
        reader.stop()
        cap.release()
        reader.join(timeout=1.0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()