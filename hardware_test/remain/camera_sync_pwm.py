#!/usr/bin/env python3
"""Generate a hardware PWM frame-trigger signal on LubanCat."""

import argparse
import signal
import time

from periphery import PWM


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a periodic camera trigger using a Linux PWM device."
    )
    parser.add_argument("--pwmchip", type=int, required=True)
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--frequency", type=float, default=5.0)
    parser.add_argument("--pulse-us", type=float, default=1000.0)
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Run time in seconds; use 0 to run until Ctrl+C.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.frequency <= 0:
        raise ValueError("frequency must be greater than zero")
    if args.pulse_us <= 0:
        raise ValueError("pulse-us must be greater than zero")

    period_us = 1_000_000.0 / args.frequency
    duty_cycle = args.pulse_us / period_us
    if not 0.0 < duty_cycle < 1.0:
        raise ValueError(
            f"pulse-us must be less than one period ({period_us:.3f} us)"
        )

    stop = False

    def request_stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    pwm = PWM(args.pwmchip, args.channel)
    try:
        pwm.frequency = args.frequency
        pwm.duty_cycle = duty_cycle
        pwm.enable()

        print(
            f"PWM enabled: pwmchip{args.pwmchip}/pwm{args.channel}, "
            f"{args.frequency:g} Hz, pulse={args.pulse_us:g} us, "
            f"duty={duty_cycle * 100:.4f}%"
        )
        print("Stop with Ctrl+C.")

        started = time.monotonic()
        while not stop:
            if args.duration > 0 and time.monotonic() - started >= args.duration:
                break
            time.sleep(0.05)
    finally:
        try:
            pwm.disable()
        finally:
            pwm.close()
        print("PWM disabled.")


if __name__ == "__main__":
    main()
