# irrigation-weact-rp2040

## Hardware

### WeAct RP2040


### H-Bridge L298N
https://www.hibit.dev/posts/89/how-to-use-the-l298n-motor-driver-module

### Bermad S-392T-2W
https://catalog.bermad.com/BERMAD%20Assets/Irrigation/Solenoids/IR-SOLENOID-S-392T-2W/IR_Accessories-Solenoid-S-392T-2W_Product-Page_English_2-2020_XSB.pdf

### Optional LOLIN D1 mini
https://www.wemos.cc/en/latest/d1/d1_mini.html

## Software
### IOT Backend options
https://blynk.io/

## Video
https://www.youtube.com/watch?v=gCUyTRL9YRA&ab_channel=TechTrendsShameer

## Arduino legacy irrigation app (based on CatWaterDigiSpark)
```c
#include <SPI.h>

#define VALVE_COUNT 1
typedef enum {VALVE_CLOSE, VALVE_OPEN} VALVE_OP;

#define LED_PIN LED_BUILTIN
#define V0_OPEN 2
#define V0_CLOSE 4
/*/
// DIGISPARK
#define LED_PIN 1
#define V0_OPEN 3
#define V0_CLOSE 5
// */
unsigned long seconds() {
  static unsigned long last_millis = -1;
  static unsigned high_millis = -1;
  const unsigned long m = millis();
  if (m < last_millis) {
    high_millis++;
  }
  last_millis = m;
  return high_millis*4294967UL + m/1000UL;
}

unsigned long ClosingTimes[VALVE_COUNT];

void setup() {
  // Open serial communications and wait for port to open:
  Serial.begin(9600);
    // initialize digital pin LED_BUILTIN as an output.
//  while (millis() < 10000 && !Serial) {
//    ; // wait for serial port to connect. Needed for native USB port only
//  }

  for(int i=0; i<VALVE_COUNT; i++) {
    ClosingTimes[i] = 1;
  }

  pinMode(LED_PIN, OUTPUT);
  pinMode(V0_OPEN, OUTPUT);
  digitalWrite(V0_OPEN, HIGH);
  pinMode(V0_CLOSE, OUTPUT);
  digitalWrite(V0_CLOSE, HIGH);

  delay(1000);
}

// pulse width is for Bermad S-392T-2W
// spec: http://www.bermad.com/Data/Uploads/IR%20Latch%20Solenoid%20S-392-2W%20Data%20Sheet.pdf
// https://catalog.bermad.com/BERMAD%20Assets/Irrigation/Solenoids/IR-SOLENOID-S-392T-2W/IR_Accessories-Solenoid-S-392T-2W_Product-Page_English_2-2020_XSB.pdf
void pulse(int pinNum) {
  //Serial.println(String("Pulse") + String(pinNum));
  digitalWrite(pinNum, HIGH);   // turn the LED on (HIGH is the voltage level)
  delay(60);                       // wait for latching
  digitalWrite(pinNum, LOW);    // turn the LED off by making the voltage LOW
  delay(20);                       // let things settle
}

void valveOp(int valveNum, VALVE_OP op) {
  Serial.println(String("Valve") + String(valveNum) + String(" op=") + String(op == VALVE_OPEN ? "OPEN" : "CLOSE"));
  if (valveNum == 0) {
    pulse(op == VALVE_OPEN ? V0_OPEN : V0_CLOSE);
//  } else if (valveNum == 1) {
//    pulse(op == VALVE_OPEN ? V1_OPEN : V1_CLOSE);
  }
}

void loop() {
  static unsigned long lastSecond;
  static unsigned long s = 0;
  lastSecond = s;
  s = seconds();

  if (s == lastSecond) {
    // nothing to do, same second
    return;
  }
  Serial.println(String("time=") + String(s));

  // first 30sec: power on, test valves 
  if (s < 30 && s%3 == 0) {
    for(int i=0; i<VALVE_COUNT; i++) {
      ClosingTimes[i] = s + 1;
      valveOp(i, VALVE_OPEN);
    }
  }

  // LED on for 1s every 3s
  //digitalWrite(DIGISPARK_LED_PIN, (s%3 == 0) ? HIGH : LOW);
  
  // honor ClosingTimes
  for(int i=0; i<VALVE_COUNT; i++) {
    if (lastSecond < ClosingTimes[i] && ClosingTimes[i] <= s) {
      valveOp(i, VALVE_CLOSE);
    }
  }

  // 2 times a day 
  if(s%43200 == 0) {
    Serial.println("Watering!");
    for(int i=0; i<VALVE_COUNT; i++) {
      ClosingTimes[i] = s + 5;
      valveOp(i, VALVE_OPEN);
    }
  }

  // Heartbeat
  digitalWrite(LED_PIN, HIGH);   // turn the LED on (HIGH is the voltage level)
  delay(100);                       // wait for a second
  digitalWrite(LED_PIN, LOW);    // turn the LED off by making the voltage LOW
//  if(Serial) Serial.println(dataString);
}
```
