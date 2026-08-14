#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from periphery import PWM
import time

PWM_CHIP = 4
PWM_CHANNEL = 0
FREQUENCY_HZ = 5
DUTY_CYCLE = 0.5
RUN_SECONDS = 10

pwm = None

try:
    pwm = PWM(PWM_CHIP, PWM_CHANNEL)
    pwm.polarity = "normal"
    pwm.frequency = FREQUENCY_HZ
    pwm.duty_cycle = DUTY_CYCLE
    pwm.enable()

    print(
        f"PWM enabled: pwmchip{PWM_CHIP}/pwm{PWM_CHANNEL}, "
        f"{FREQUENCY_HZ} Hz, duty={DUTY_CYCLE * 100:.1f}%"
    )

    deadline = time.monotonic() + RUN_SECONDS

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        print(f"Remaining: {remaining:.1f} s")
        time.sleep(min(1.0, remaining))

    print("10 seconds reached, stopping PWM")

except KeyboardInterrupt:
    print("\nStopped by user")

finally:
    if pwm is not None:
        try:
            pwm.disable()
            print("PWM disabled")
        finally:
            pwm.close()
            print("PWM closed")
