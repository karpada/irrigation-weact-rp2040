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

local_time_lag: int = 3155673600 - 2208988800  # 1970-2000

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
    print(f"Saving data to {filename}")
    with open(filename, 'w') as f:
        ujson.dump(data, f)

def load_data(filename: str) -> dict:
    try:
        with open(filename, 'r') as f:
            return ujson.load(f)
    except:
        return {"zones": [], "schedules": [], "options": {}}

# Load or initialize config
config: dict = load_data('config.json')

# Helper functions
def control_watering(zone_id: int, start: bool) -> None:
    if zone_id < 0 or zone_id >= len(config["zones"]):
        print(f"Zone {zone_id} not found")
        return
    zone = config["zones"][zone_id]
    print(f"{'Started' if start else 'Stopped'} watering zone {zone_id}")
    pin_id = zone['on_pin'] if start else zone['off_pin']
    pin = Pin(pin_id, Pin.OUT)
    pin.value(1)
    time.sleep(0.06)
    pin.value(0)
    Pin(pin_id, Pin.IN)

def read_soil_moisture() -> int:
    return soil_sensor.read()
    
async def serve_file(filename: str, writer) -> None:
    try:
        start_time = time.ticks_ms()
        with open(filename, 'r') as f:
            while True:
                chunk = f.read(1024)  # Read 1KB at a time
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
                await asyncio.sleep(0.005) # makes serving more stable
        print(f'served html in {time.ticks_ms() - start_time}ms')
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
        404: "Not Found",
        500: "Internal Server Error"
    }
    return status_messages.get(status_code, "Unknown")

async def handle_request(reader, writer):
    global config

    content_type = 'application/json'
    status_code = 200
    filename = None

    try:
        method_path = (await reader.readline()).decode().strip()
        headers = await read_headers(reader)
        content_length = int(headers.get('content-length', '0'))

        body = ujson.loads((await reader.read(content_length)).decode()) if content_length > 0 else None

        if method_path.startswith('GET / HTTP'):
            filename = 'index.html'
            content_type = 'text/html'

        elif method_path.startswith('GET /config'):
            # curl example: curl http://[ESP32_IP]/config
            response = ujson.dumps(config)
        elif method_path.startswith('POST /config'):
            print(f"body = {body}, isinstance(body, list) = {isinstance(body, list)}")
            new_config = {"zones": [], "schedules": [], "options": {}}
            for zone_data in body['zones']:
                new_config['zones'].append({
                    "name": str(zone_data['name']),
                    "on_pin": int(zone_data['on_pin']),
                    "off_pin": int(zone_data['off_pin'])
                })
            for schedule_data in body['schedules']:
                new_config['schedules'].append({
                    "zone_id": int(schedule_data['zone_id']),
                    "start_time": schedule_data['start_time'],
                    "duration_ms": int(schedule_data['duration_ms']),
                    "enabled": schedule_data['enabled'],
                    "expiry": int(schedule_data['expiry'])
                })
            new_config['options'] = {}
            config = new_config
            refresh_irrigation_schedule()
            save_data('config.json', config)
            response = ujson.dumps(config)

        elif method_path.startswith('GET /sensor'):
            moisture = read_soil_moisture()
            response = ujson.dumps({"soil_moisture": moisture})
        elif method_path.startswith('GET /time'):
            current_time_ms: int = (time.time()+local_time_lag) * 1000  # Convert to milliseconds
            response = ujson.dumps({"time_ms": current_time_ms})
        else:
            status_code = 404
    except Exception as e:
        print(f"Error handling request: {e}")
        status_code = 500
    writer.write(f'HTTP/1.0 {status_code} {get_status_message(status_code)}\r\nContent-type: {content_type}\r\n\r\n')
    if filename:
        await serve_file(filename, writer)
    else:
        writer.write(response)
    await writer.drain()
    writer.close()
    await writer.wait_closed()

irrigation_tasks: list[asyncio.Task] = []
def refresh_irrigation_schedule():
    for task in irrigation_tasks:
        task.cancel()
    irrigation_tasks.clear()
    for i, schedule in enumerate(config["schedules"]):
        irrigation_tasks.append(asyncio.create_task(schedule_irrigation(i)))

async def schedule_irrigation(irrigation_id: int):
    print(f"@{time.time()} config['schedules'][{irrigation_id}] New task => {config['schedules'][irrigation_id]}")
    while True:
        i = config["schedules"][irrigation_id]
        if 'expiry' in i and time.time() > i['expiry']-local_time_lag:
            print(f"Schedule {irrigation_id} has expired")
            break
        hour, minute = map(int, i['start_time'].split(':'))
        now = time.gmtime()
        seconds_until = ((hour - now[3]) * 3600 + (minute - now[4]) * 60 - now[5]) % 86400
        print(f"@{time.time()} config['schedules'][{irrigation_id}] Waiting {seconds_until} seconds => {config['schedules'][irrigation_id]}")
        await asyncio.sleep(seconds_until)
        print(f"@{time.time()} config['schedules'][{irrigation_id}] Starting irrigation of zone[{i['zone_id']}] seconds => {config['schedules'][irrigation_id]}")
        if i['enabled']:
            control_watering(i['zone_id'], True)
        await asyncio.sleep(i['duration_ms'] / 1000)
        print(f"@{time.time()} config['schedules'][{irrigation_id}] Stopping irrigation of zone[{i['zone_id']}] seconds => {config['schedules'][irrigation_id]}")
        control_watering(i['zone_id'], False)

async def main():
    await connect_wifi()
    await sync_ntp()
    
    # Schedule regular NTP sync (every 24 hours)
    asyncio.create_task(periodic_ntp_sync())
    
    refresh_irrigation_schedule()

    server = await asyncio.start_server(handle_request, "0.0.0.0", 80)
    print('Server listening on port 80')
    await server.wait_closed()

async def periodic_ntp_sync():
    while True:
        await asyncio.sleep(24 * 60 * 60)  # 24 hours
        await sync_ntp()

if __name__ == "__main__":
    asyncio.run(main())