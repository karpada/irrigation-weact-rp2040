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
    
    if request.startswith('GET /zones'):
        response = ujson.dumps({zone_id: {"name": zone["name"], "pin": zone["pin"].id()} for zone_id, zone in zones.items()})
    elif request.startswith('POST /zones'):
        body = parse_request_body(request)
        if body and 'name' in body and 'pin' in body:
            new_zone = {
                "name": body['name'],
                "pin": Pin(body['pin'], Pin.OUT)
            }
            zone_id = str(max([int(id) for id in zones.keys()] + [0]) + 1)
            zones[zone_id] = new_zone
            save_data('zones.json', {zone_id: {"name": zone["name"], "pin": zone["pin"].id()} for zone_id, zone in zones.items()})
            response = ujson.dumps({zone_id: {"name": new_zone["name"], "pin": new_zone["pin"].id()}})
        else:
            response = "Bad Request"
    elif request.startswith('GET /schedules'):
        response = ujson.dumps(irrigation_schedules)
    elif request.startswith('POST /schedules'):
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
            
            # Schedule the new irrigation task
            hour, minute = map(int, body['start_time'].split(':'))
            asyncio.create_task(schedule_irrigation(schedule_id, hour, minute, body['zone_id'], body['duration_ms']))
            
            response = ujson.dumps({schedule_id: new_schedule})
        else:
            response = "Bad Request"
    elif request.startswith('GET /sensor'):
        moisture = read_soil_moisture()
        response = ujson.dumps({"soil_moisture": moisture})
    elif request.startswith('GET /time'):
        current_time_ms: int = time.time() * 1000  # Convert to milliseconds
        response = ujson.dumps({"time_ms": current_time_ms})
    else:
        response = "Not Found"

    writer.write('HTTP/1.0 200 OK\r\nContent-type: application/json\r\n\r\n')
    writer.write(response)
    await writer.drain()
    writer.close()
    await writer.wait_closed()

async def schedule_irrigation(schedule_id, hour, minute, zone_id, duration_ms):
    while True:
        now = time.localtime()
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
        asyncio.create_task(schedule_irrigation(schedule_id, hour, minute, schedule['zone_id'], schedule['duration_ms']))
    
    server = await asyncio.start_server(handle_request, "0.0.0.0", 80)
    print('Server listening on port 80')
    await server.wait_closed()

async def periodic_ntp_sync():
    while True:
        await asyncio.sleep(24 * 60 * 60)  # 24 hours
        await sync_ntp()

if __name__ == "__main__":
    asyncio.run(main())
