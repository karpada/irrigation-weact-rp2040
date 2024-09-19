import network
import socket
import utime as time
from machine import Pin, ADC
import ujson
import ntptime
import micropython-schedule as schedule
# from typing_extensions import Dict, Any, Optional

# Wi-Fi connection
ssid: str = 'Pita'
password: str = '***REMOVED***'

#Pins
soil_sensor: ADC = ADC(0)

# Connect to WLAN
wlan: network.WLAN = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(ssid, password)

# Wait for connect or fail
max_wait: int = 10
while max_wait > 0:
    if wlan.isconnected():
        break
    max_wait -= 1
    print('waiting for connection...')
    time.sleep(1)

# Handle connection error
if not wlan.isconnected():
    raise RuntimeError('network connection failed')
else:
    print('connected')
    status: tuple = wlan.ifconfig()
    print('ip = ' + status[0])

# Sync time with NTP
def sync_ntp() -> None:
    try:
        ntptime.settime()
        print("Time synced with NTP server")
    except:
        print("Error syncing time")

# Initial NTP sync
sync_ntp()

# Schedule regular NTP sync (every 24 hours)
# schedule.every(24).hours.do(sync_ntp)

# Open socket
addr: tuple = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
s: socket.socket = socket.socket()
s.bind(addr)
s.listen(1)

print('listening on', addr)

# Persistent storage functions
def save_data(filename: str, data: Dict[str, Any]) -> None:
    with open(filename, 'w') as f:
        ujson.dump(data, f)

def load_data(filename: str) -> Dict[str, Any]:
    try:
        with open(filename, 'r') as f:
            return ujson.load(f)
    except:
        return {}

# Load or initialize zones and schedules
zones: Dict[str, Dict[str, Any]] = load_data('zones.json')
irrigation_schedules: Dict[str, Dict[str, Any]] = load_data('schedules.json')

# Initialize pins for zones
for zone_id, zone in zones.items():
    zone['pin'] = Pin(zone['pin'], Pin.OUT)

# Helper functions
def control_watering(zone_id: str, start: bool) -> None:
    zone = zones.get(zone_id)
    if zone:
        zone['pin'].value(1 if start else 0)
        time.sleep(0.1)  # Short delay to ensure the signal is received
        zone['pin'].value(0)
        print(f"{'Started' if start else 'Stopped'} watering zone {zone_id}")

def read_soil_moisture() -> int:
    return soil_sensor.read()

def parse_request_body(request: str) -> Optional[Dict[str, Any]]:
    # Find the start of the JSON data
    json_start = request.find('\r\n\r\n') + 4
    if json_start == 3:  # If '\r\n\r\n' is not found
        return None
    try:
        return ujson.loads(request[json_start:])
    except:
        return None

def handle_request(request: str) -> str:
    global zones, irrigation_schedules
    
    if request.startswith('GET /zones'):
        # curl -X GET http://[ESP32_IP_ADDRESS]/zones
        return ujson.dumps({zone_id: {"name": zone["name"], "pin": zone["pin"].id()} for zone_id, zone in zones.items()})
    elif request.startswith('POST /zones'):
        # curl -X POST http://[ESP32_IP_ADDRESS]/zones -H "Content-Type: application/json" -d '{"name": "Front Lawn", "pin": 2}'
        body = parse_request_body(request)
        if body and 'name' in body and 'pin' in body:
            new_zone = {
                "name": body['name'],
                "pin": Pin(body['pin'], Pin.OUT)
            }
            zone_id = str(max([int(id) for id in zones.keys()] + [0]) + 1)
            zones[zone_id] = new_zone
            save_data('zones.json', {zone_id: {"name": zone["name"], "pin": zone["pin"].id()} for zone_id, zone in zones.items()})
            return ujson.dumps({zone_id: {"name": new_zone["name"], "pin": new_zone["pin"].id()}})
        else:
            return "Bad Request"
    elif request.startswith('GET /schedules'):
        # curl -X GET http://[ESP32_IP_ADDRESS]/schedules
        return ujson.dumps(irrigation_schedules)
    elif request.startswith('POST /schedules'):
        # curl -X POST http://[ESP32_IP_ADDRESS]/schedules -H "Content-Type: application/json" -d '{"zone_id": "1", "start_time": "06:00", "duration_ms": 900000}'
        body = parse_request_body(request)
        if body and 'zone_id' in body and 'start_time' in body and 'duration_ms' in body:
            new_schedule = {
                "zone_id": body['zone_id'],
                "start_time": body['start_time'],
                "duration_ms": body['duration_ms']
            }
            schedule_id = str(max([int(id) for id in irrigation_schedules.keys()] + [0]) + 1)
            irrigation_schedules[schedule_id] = new_schedule
            save_data('schedules.json', irrigation_schedules)
            
            # Add the new schedule to micropython-schedule
            schedule.every().day.at(body['start_time']).do(control_watering, body['zone_id'], True).tag(f'start_{schedule_id}')
            schedule.every().day.at(body['start_time']).do(
                schedule.every(body['duration_ms'] / 1000).seconds.do(control_watering, body['zone_id'], False).tag(f'stop_{schedule_id}')
            ).tag(f'schedule_{schedule_id}')
            
            return ujson.dumps({schedule_id: new_schedule})
        else:
            return "Bad Request"
    elif request.startswith('GET /sensor'):
        # curl -X GET http://[ESP32_IP_ADDRESS]/sensor
        moisture = read_soil_moisture()
        return ujson.dumps({"soil_moisture": moisture})
    elif request.startswith('GET /time'):
        # curl -X GET http://[ESP32_IP_ADDRESS]/time
        current_time_ms: int = time.time() * 1000  # Convert to milliseconds
        return ujson.dumps({"time_ms": current_time_ms})
    else:
        return "Not Found"

# Main loop
while True:
    try:
        cl, addr = s.accept()
        print('client connected from', addr)
        request = cl.recv(1024).decode()
        response = handle_request(request)
        
        cl.send('HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n')
        cl.send(response)
        cl.close()

    except OSError as e:
        cl.close()
        print('connection closed')

    # Run scheduled tasks
#    schedule.run_pending()
