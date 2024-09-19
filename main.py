import network
import socket
import utime as time
from machine import Pin, ADC
import ujson
import ntptime
import uasyncio as asyncio

# Wi-Fi connection
ssid: str = 'Pita'
password: str = '***REMOVED***'

# Pins
soil_sensor: ADC = ADC(0)

# Connect to WLAN
wlan: network.WLAN = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(ssid, password)

# Wait for connect or fail
async def connect_wifi():
    max_wait: int = 10
    while max_wait > 0:
        if wlan.isconnected():
            break
        max_wait -= 1
        print('waiting for connection...')
        await asyncio.sleep(1)

    if not wlan.isconnected():
        raise RuntimeError('network connection failed')
    else:
        print('connected')
        status: tuple = wlan.ifconfig()
        print('ip = ' + status[0])

# Sync time with NTP
async def sync_ntp() -> None:
    try:
        ntptime.settime()
        print("Time synced with NTP server")
    except:
        print("Error syncing time")

# Persistent storage functions
def save_data(filename: str, data: dict) -> None:
    with open(filename, 'w') as f:
        ujson.dump(data, f)

def load_data(filename: str) -> dict:
    try:
        with open(filename, 'r') as f:
            return ujson.load(f)
    except:
        return {}

# Load or initialize zones and schedules
zones: dict = load_data('zones.json')
irrigation_schedules: dict = load_data('schedules.json')

# Initialize pins for zones
def initialize_zone_pins():
    for zone_id, zone in zones.items():
        zone['on_pin'] = Pin(zone['on_pin'], Pin.OUT)
        zone['off_pin'] = Pin(zone['off_pin'], Pin.OUT)

initialize_zone_pins()

# Helper functions
def control_watering(zone_id: str, start: bool) -> None:
    if zone_id not in zones:
        print(f"Zone {zone_id} not found")
        return
    zone = zones[zone_id]
    print(f"{'Started' if start else 'Stopped'} watering zone {zone_id}")
    pin = zone['on_pin'] if start else zone['off_pin']
    pin.value(1)
    time.sleep(0.06)
    pin.value(0)

def read_soil_moisture() -> int:
    return soil_sensor.read()

def parse_request_body(request: str) -> dict:
    json_start = request.find('\r\n\r\n') + 4
    if json_start == 3:  # If '\r\n\r\n' is not found
        return None
    try:
        return ujson.loads(request[json_start:])
    except:
        return None

async def handle_request(reader, writer):
    global zones, irrigation_schedules
    
    request = await reader.read(1024)
    request = request.decode()

    content_type = 'application/json'
    if request.startswith('GET / HTTP'):
        # Serve the HTML file for the root route
        with open('index.html', 'r') as f:
            response = f.read()
        content_type = 'text/html'
    elif request.startswith('GET /zones'):
        # curl example: curl http://[ESP32_IP]/zones
        response = ujson.dumps({zone_id: {"name": zone["name"], "on_pin": zone["on_pin"].id(), "off_pin": zone["off_pin"].id()} for zone_id, zone in zones.items()})
    elif request.startswith('POST /zones'):
        # curl example: curl -X POST -H "Content-Type: application/json" -d '{"1": {"name":"Front Lawn", "on_pin":12, "off_pin":13}, "2": {"name":"Back Yard", "on_pin":14, "off_pin":15}}' http://[ESP32_IP]/zones
        body = parse_request_body(request)
        if body and isinstance(body, dict):
            new_zones = {}
            for zone_id, zone_data in body.items():
                if 'name' in zone_data and 'on_pin' in zone_data and 'off_pin' in zone_data:
                    new_zones[zone_id] = {
                        "name": zone_data['name'],
                        "on_pin": zone_data['on_pin'],
                        "off_pin": zone_data['off_pin']
                    }
            zones = new_zones
            save_data('zones.json', zones)
            initialize_zone_pins()
            response = ujson.dumps(zones)
        else:
            response = "Bad Request"
    elif request.startswith('GET /schedules'):
        # curl example: curl http://[ESP32_IP]/schedules
        response = ujson.dumps(irrigation_schedules)
    elif request.startswith('POST /schedules'):
        # curl example: curl -X POST -H "Content-Type: application/json" -d '{"1": {"zone_id":"1", "start_time":"06:00", "duration_ms":300000, "expiry":1735689600}, "2": {"zone_id":"2", "start_time":"18:00", "duration_ms":600000, "expiry":1735689600}}' http://[ESP32_IP]/schedules
        body = parse_request_body(request)
        if body and isinstance(body, dict):
            new_schedules = {}
            for schedule_id, schedule_data in body.items():
                if all(key in schedule_data for key in ['zone_id', 'start_time', 'duration_ms', 'expiry']):
                    new_schedules[schedule_id] = schedule_data
            irrigation_schedules = new_schedules
            save_data('schedules.json', irrigation_schedules)
            
            # Cancel all existing irrigation tasks and schedule new ones
            for task in asyncio.all_tasks():
                if task.get_name().startswith('irrigation_'):
                    task.cancel()
            
            for schedule_id, schedule in irrigation_schedules.items():
                hour, minute = map(int, schedule['start_time'].split(':'))
                asyncio.create_task(schedule_irrigation(schedule_id, hour, minute, schedule['zone_id'], schedule['duration_ms'], schedule['expiry']), name=f'irrigation_{schedule_id}')
            
            response = ujson.dumps(irrigation_schedules)
        else:
            response = "Bad Request"
    elif request.startswith('GET /sensor'):
        # curl example: curl http://[ESP32_IP]/sensor
        moisture = read_soil_moisture()
        response = ujson.dumps({"soil_moisture": moisture})
    elif request.startswith('GET /time'):
        # curl example: curl http://[ESP32_IP]/time
        current_time_ms: int = time.time() * 1000  # Convert to milliseconds
        response = ujson.dumps({"time_ms": current_time_ms})
    else:
        response = "Not Found"

    writer.write(f'HTTP/1.0 200 OK\r\nContent-type: {content_type}\r\n\r\n')
    writer.write(response)
    await writer.drain()
    writer.close()
    await writer.wait_closed()

async def schedule_irrigation(schedule_id, hour, minute, zone_id, duration_ms, expiry):
    while True:
        now = time.localtime()
        if time.time() > expiry:
            print(f"Schedule {schedule_id} has expired")
            del irrigation_schedules[schedule_id]
            save_data('schedules.json', irrigation_schedules)
            break
        seconds_until = ((hour - now[3]) * 3600 + (minute - now[4]) * 60 - now[5]) % 86400
        await asyncio.sleep(seconds_until)
        control_watering(zone_id, True)
        await asyncio.sleep(duration_ms / 1000)
        control_watering(zone_id, False)

async def main():
    await connect_wifi()
    await sync_ntp()
    
    # Schedule regular NTP sync (every 24 hours)
    asyncio.create_task(periodic_ntp_sync())
    
    # Schedule existing irrigation tasks
    for schedule_id, schedule in irrigation_schedules.items():
        hour, minute = map(int, schedule['start_time'].split(':'))
        asyncio.create_task(schedule_irrigation(schedule_id, hour, minute, schedule['zone_id'], schedule['duration_ms'], schedule['expiry']), name=f'irrigation_{schedule_id}')
    
    server = await asyncio.start_server(handle_request, "0.0.0.0", 80)
    print('Server listening on port 80')
    await server.wait_closed()

async def periodic_ntp_sync():
    while True:
        await asyncio.sleep(24 * 60 * 60)  # 24 hours
        await sync_ntp()

if __name__ == "__main__":
    asyncio.run(main())
