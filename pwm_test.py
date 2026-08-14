from periphery import PWM
import time

pwm = None

try:
    pwm = PWM(4, 0)
    pwm.frequency = 60
    pwm.duty_cycle = 0.5
    pwm.enable()

    print("PWM11 enabled: Pin 35, 100Hz, duty cycle 50%")
    print("Press Ctrl+C to stop")

    while True:
        time.sleep(1)

except KeyboardInterrupt:
    print("\nStopping PWM")

finally:
    if pwm is not None:
        pwm.disable()
        pwm.close()
        print("PWM11 disabled")
