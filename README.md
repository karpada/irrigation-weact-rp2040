# irrigation-esp32
Design philosophy:
1. Easy to source generic components
1. Cheap - excluding the valves, cost should be under 10$

# Hardware

## Controller
This project based on [ESP32-S2](https://www.espressif.com/en/products/socs/esp32-s2) + [micropython](https://docs.micropython.org/en/latest/esp32/quickref.html)

### Considerations:
1. Built-in WiFi
1. [Lots of GPIOs](https://www.sudo.is/docs/esphome/boards/esp32s2mini/ESP32_S2_mini_pinout.jpg)
1. Easy to use - [thonny (Python IDE)](https://thonny.org/)
1. Cheap

### Previous Iterations:
1. starting with a simple Digispark solution (arduino based)
1. followed be [WeAct RP2040](https://github.com/WeActStudio/WeActStudio.RP2040CoreBoard) + micropython - this solution lacked network connectivity
1. Raspberry Pi Zero W - bit of an overkill, there's no need for a full linux system
1. [LOLIN D1 mini - ESP8266](https://www.wemos.cc/en/latest/d1/d1_mini.html) + micropython - too little memory (met limit on global strings)

## Valve / Pump

### Valves
1. DC Latching selonoid - Draws power only on state change [Bermad S-392T-2W](https://catalog.bermad.com/BERMAD%20Assets/Irrigation/Solenoids/IR-SOLENOID-S-392T-2W/IR_Accessories-Solenoid-S-392T-2W_Product-Page_English_2-2020_XSB.pdf)
### Pumps
1. DC pump - [12V DC pump](https://www.google.com/search?q=12v+dc+pump)

## Driver
Depends on the valve/pump used:
1. [Mechanical relays (Multi relay module)](https://www.google.com/search?q=Mechanical+multi+relay+module) - most versitile, compatible with AC, DC & reverse polarity
1. Solid state relays  (Multi relay module) - compatible with DC only - no reverse polarity
1. [H-Bridge L298N](https://www.hibit.dev/posts/89/how-to-use-the-l298n-motor-driver-module) - for closing by reversing polarity

## Power supply
Depends on the valve/pump used, conroller may be powered by a USB charger & cable.

## [Soil Moisture Sensor](https://www.google.com/search?q=soil+moisture+sensor) (Optional)

## Master relay (Optional)
1. saves power when waiting for next watering cycle

# Software

## IOT Backend options
1. [Thingspeak](https://thingspeak.com/) - [Video](https://www.youtube.com/watch?v=Ckf3zzCA5os)
1. [blynk](https://blynk.io/) - [Video](https://www.youtube.com/watch?v=gCUyTRL9YRA)

## References:
1. 

# Installation
1. using Thonny, copy main.py, index.html, setup.html to the ESP32
1. reset & enter wifi setup mode by pressing the button within 1 second, led will blink rapidly.
1. connect to the ESP32's wifi `irrigation-esp32` and configure the wifi settings

# TODO
1. Implement pause_hours
1. manual watering
1. fix: 
    ```
    Task exception wasn't retrieved
    future: <Task> coro= <generator object 'keep_wifi_connected' at 3f803b20>
    Traceback (most recent call last):
    File "asyncio/core.py", line 1, in run_until_complete
    File "<stdin>", line 60, in keep_wifi_connected
    File "<stdin>", line 42, in connect_wifi
    OSError: Wifi Internal Error
    ```
    Failing line (line 42, in connect_wifi) = `wlan.connect(config['options']['wifi']['ssid'], config['options']['wifi']['password'])`
1.