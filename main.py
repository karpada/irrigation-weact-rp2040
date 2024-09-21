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
# ssid: str = 'FATAL_FREE_WIFI'
ssid: str = 'iPita'
password: str = 'isnocake'

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
def save_data(filename: str, data: list) -> None:
    print(f"Saving data to {filename}")
    with open(filename, 'w') as f:
        ujson.dump(data, f)

def load_data(filename: str) -> list:
    try:
        with open(filename, 'r') as f:
            return ujson.load(f)
    except:
        return []

# Load or initialize zones and schedules
zones: list = load_data('zones.json')
irrigation_schedules: list = load_data('schedules.json')

# Helper functions
def control_watering(zone_id: int, start: bool) -> None:
    if zone_id < 0 or zone_id >= len(zones):
        print(f"Zone {zone_id} not found")
        return
    zone = zones[zone_id]
    print(f"{'Started' if start else 'Stopped'} watering zone {zone_id}")
    pin_id = zone['on_pin'] if start else zone['off_pin']
    pin = Pin(pin_id, Pin.OUT)
    pin.value(1)
    time.sleep(0.06)
    pin.value(0)
    Pin(pin_id, Pin.In)

def read_soil_moisture() -> int:
    return soil_sensor.read()
    
async def serve_file(filename: str, writer) -> None:
    try:
        with open(filename, 'r') as f:
            while True:
                chunk = f.read(1024)  # Read 1KB at a time
                if not chunk:
                    break
                writer.write(chunk)
    except Exception as e:
        print(f"Error serving file [{filename}]: {e}")

async def read_headers(reader) -> dict:
    headers = {}
    while True:
        line = await reader.readline()
        if line == b'\r\n':
            break
        name, value = line.decode().strip().split(': ')
        headers[name.lower()] = value
    return headers

def get_status_message(status_code):
    status_messages = {
        200: "OK",
        400: "Bad Request",
        404: "Not Found"
    }
    return status_messages.get(status_code, "Unknown")

async def handle_request(reader, writer):
    global zones, irrigation_schedules

    method_path = (await reader.readline()).decode().strip()
    headers = await read_headers(reader)
    print(f"Handling request: {method_path}") #     headers={headers}")

    content_length = int(headers.get('content-length', '0'))
    print(f"content_length={content_length}")
    body = ujson.loads((await reader.read(content_length)).decode()) if content_length > 0 else None

    content_type = 'application/json'
    status_code = 200
    filename = None
    if method_path.startswith('GET / HTTP'):
        # Serve the HTML file for the root route
        filename = 'index.html'
        content_type = 'text/html'
    elif method_path.startswith('GET /zones'):
        # curl example: curl http://[ESP32_IP]/zones
        response = ujson.dumps(zones)
    elif method_path.startswith('POST /zones'):
        # curl example: curl -X POST -H "Content-Type: application/json" -d '[{"name":"Front Lawn", "on_pin":12, "off_pin":13}, {"name":"Back Yard", "on_pin":14, "off_pin":15}]' http://[ESP32_IP]/zones
        print(f"body = {body}, isinstance(body, list) = {isinstance(body, list)}")

        if body and isinstance(body, list):
            new_zones = []
            for zone_data in body:
                if 'name' in zone_data and 'on_pin' in zone_data and 'off_pin' in zone_data:
                    new_zones.append({
                        "name": zone_data['name'],
                        "on_pin": zone_data['on_pin'],
                        "off_pin": zone_data['off_pin']
                    })
            zones = new_zones
            save_data('zones.json', zones)
            response = ujson.dumps(zones)
        else:
            status_code = 400
    elif method_path.startswith('GET /schedules'):
        # curl example: curl http://[ESP32_IP]/schedules
        response = ujson.dumps(irrigation_schedules)
    elif method_path.startswith('POST /schedules'):
        # curl example: curl -X POST -H "Content-Type: application/json" -d '[{"zone_id":0, "start_time":"06:00", "duration_ms":300000, "expiry":1735689600}, {"zone_id":1, "start_time":"18:00", "duration_ms":600000, "expiry":1735689600}]' http://[ESP32_IP]/schedules
        if body and isinstance(body, list):
            new_schedules = []
            for schedule_data in body:
                if all(key in schedule_data for key in ['zone_id', 'start_time', 'duration_ms', 'expiry']):
                    new_schedules.append(schedule_data)
            irrigation_schedules = new_schedules
            save_data('schedules.json', irrigation_schedules)
            
            # Cancel all existing irrigation tasks and schedule new ones
            for task in asyncio.all_tasks():
                if task.get_name().startswith('irrigation_'):
                    task.cancel()
            
            for i, schedule in enumerate(irrigation_schedules):
                hour, minute = map(int, schedule['start_time'].split(':'))
                asyncio.create_task(schedule_irrigation(i, hour, minute, schedule['zone_id'], schedule['duration_ms'], schedule['expiry']), name=f'irrigation_{i}')
            
            response = ujson.dumps(irrigation_schedules)
        else:
            status_code = 400
    elif method_path.startswith('GET /sensor'):
        # curl example: curl http://[ESP32_IP]/sensor
        moisture = read_soil_moisture()
        response = ujson.dumps({"soil_moisture": moisture})
    elif method_path.startswith('GET /time'):
        # curl example: curl http://[ESP32_IP]/time
        current_time_ms: int = time.time() * 1000  # Convert to milliseconds
        response = ujson.dumps({"time_ms": current_time_ms})
    else:
        status_code = 404

    writer.write(f'HTTP/1.0 {status_code} {get_status_message(status_code)}\r\nContent-type: {content_type}\r\n\r\n')
    if filename:
        await serve_file(filename, writer)
    else:
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
    for i, schedule in enumerate(irrigation_schedules):
        hour, minute = map(int, schedule['start_time'].split(':'))
        asyncio.create_task(schedule_irrigation(i, hour, minute, schedule['zone_id'], schedule['duration_ms'], schedule['expiry']), name=f'irrigation_{i}')
    
    server = await asyncio.start_server(handle_request, "0.0.0.0", 80)
    print('Server listening on port 80')
    await server.wait_closed()

async def periodic_ntp_sync():
    while True:
        await asyncio.sleep(24 * 60 * 60)  # 24 hours
        await sync_ntp()

if __name__ == "__main__":
    asyncio.run(main())
