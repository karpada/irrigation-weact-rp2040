from machine import Pin
import time
#define VALVE_COUNT 1
# typedef enum {VALVE_CLOSE, VALVE_OPEN} VALVE_OP;

LED = Pin(25, Pin.OUT)
V0_OPEN = Pin(24, Pin.OUT)
V0_CLOSE = Pin(29, Pin.OUT)


# led.toggle()

while True:
    LED.on()
    V0_OPEN.on()
    V0_CLOSE.off()
    time.sleep(3)
    LED.off()
    V0_OPEN.off()
    V0_CLOSE.on()
    time.sleep(3)
