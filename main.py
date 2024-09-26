import network
import utime as time
from machine import Pin, ADC
import ujson
import ntptime
import uasyncio as asyncio
import urequests as requests

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
config: dict = load_data('config.json')

local_time_lag: int = 3155673600 - 2208988800  # 1970-2000

# Pins
soil_sensor: ADC = None


# WiFi connection
wlan: network.WLAN = network.WLAN(network.STA_IF)
async def connect_wifi() -> None:
    wlan.active(True)
    wlan.connect(config['options'].get('wifi_ssid', 'Pita'), config['options'].get('wifi_password', '***REMOVED***'))
    print(f'@{time.time()} wifi connecting.', end='')
    for i in range(15):
        if wlan.isconnected():
            break
        await asyncio.sleep(1)
        print('.', end='')

    if not wlan.isconnected():
        print('network connection failed, retrying in 90 seconds')
        await asyncio.sleep(90)
    else:
        print('connected')
        status: tuple = wlan.ifconfig()
        print('ip = ' + status[0])

async def keep_wifi_connected():
    while True:
        while wlan.isconnected():
            await asyncio.sleep(5)
        print(f'@{time.time()} No wifi connection, attempting to reconnect.')
        await connect_wifi()


# Sync time with NTP
async def sync_ntp() -> bool:
    try:
        ntptime.settime()
        return True
    except:
        print("Error syncing time")
        return False

async def periodic_ntp_sync():
    while True:
        await asyncio.sleep(24 * 60 * 60)  # 24 hours
        while not await sync_ntp():
            await asyncio.sleep(30 * 60) # 30 minutes

# Watering control functions
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

async def schedule_irrigation(irrigation_id: int):
    print(f"@{time.time()} config['schedules'][{irrigation_id}] New task => {config['schedules'][irrigation_id]}")
    while True:
        i = config["schedules"][irrigation_id]
        if i['expiry'] and time.time() > i['expiry']-local_time_lag:
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

irrigation_tasks: list[asyncio.Task] = []
def apply_config(new_config: dict = None):
    global config
    global soil_sensor

    if not new_config:
        raise ValueError("cowardly refusing to apply empty config")

    config = new_config
    soil_sensor = ADC(config['options'].get('soil_moisture_pin', 0))
    for task in irrigation_tasks:
        task.cancel()
    irrigation_tasks.clear()
    for i, schedule in enumerate(config["schedules"]):
        irrigation_tasks.append(asyncio.create_task(schedule_irrigation(i)))
    save_data('config.json', config)

def read_soil_moisture() -> int:
    return soil_sensor.read() if soil_sensor else None

# HTTP server
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
    content_type = 'application/json'
    status_code = 200
    filename = None

    try:
        method, path, _ = (await reader.readline()).decode().strip().split(' ')
        path, query_params = path.split('?') if '?' in path else (path, None)
        query_params = dict([param.split('=') for param in query_params.split('&')]) if query_params else {}

        headers = await read_headers(reader)
        content_length = int(headers.get('content-length', '0'))

        print(f"@{time.time()} Handling request: method={method} path={path}, query_params={query_params}, (content_length={content_length})")  #     headers={headers}")

        body = ujson.loads((await reader.read(content_length)).decode()) if content_length > 0 else None

        if method == 'GET' and path == '/':
            filename = 'index.html'
            content_type = 'text/html'
        elif method == 'GET' and path == '/favicon.ico':
            content_type = 'image/svg+xml'
            response = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path d="M50 5 C30 5 5 35 5 60 C5 85 25 95 50 95 C75 95 95 85 95 60 C95 35 70 5 50 5Z" fill="#4FC3F7" stroke="#29B6F6" stroke-width="2"/><ellipse cx="30" cy="35" rx="10" ry="15" fill="#81D4FA" transform="rotate(-35 30 35)"/></svg>'
        elif method == 'GET' and path == '/pulse':
            if pin_id not in query_params:
                status_code = 400
                response = 'Missing pin_id'
            else:
                pin_id = int(query_params['pin_id'])
                pin = Pin(pin_id, Pin.OUT)
                pin.value(1)
                time.sleep(1)
                pin.value(0)
                Pin(pin_id, Pin.IN)
                response = 'Pulse sent'
        elif method == 'GET' and path == '/config':
            # curl example: curl http://[ESP32_IP]/config
            response = ujson.dumps(config)
        elif method == 'POST' and path == '/config':
            # restore backup: jq . irrigation-config.json | curl -H "Content-Type: application/json" -X POST --data-binary @- http://192.168.68.ESP/config
            print(f"body = {body}, isinstance(body, dict) = {isinstance(body, dict)}")
            new_config = {"zones": [], "schedules": [], "options": {}}
            for zone_data in body['zones']:
                new_config['zones'].append({
                    "name": str(zone_data['name']),
                    "on_pin": int(zone_data['on_pin']),
                    "off_pin": int(zone_data['off_pin']),
                })
            for schedule_data in body['schedules']:
                new_config['schedules'].append({
                    "zone_id": int(schedule_data['zone_id']),
                    "start_time": schedule_data['start_time'],
                    "duration_ms": int(schedule_data['duration_ms']),
                    "enabled": schedule_data['enabled'],
                    "expiry": int(schedule_data.get('expiry', 0)),
                })
            bo = body['options']
            for key in ['wifi', 'soil_moisture', 'monitoring', 'settings']:
                bo.setdefault(key, {})
            new_config['options'] = {
                "wifi": {
                    "ssid": str(bo['wifi'].get('ssid', 'Pita')),
                    "password": str(bo['wifi'].get('password', '***REMOVED***')),
                },
                "soil_moisture": {
                    "pin_id": int(bo['soil_moisture'].get('pin_id', -1)),
                    "threshold_low": int(bo['soil_moisture'].get('threshold_low', -1)),
                    "threshold_high": int(bo['soil_moisture'].get('threshold_high', -1)),
                },
                "monitoring": {
                    "thingsspeak_apikey": str(bo['monitoring'] .get('thingsspeak_apikey', 'RDO96UXJ98Y8OZW9')),
                },
                "settings": {
                    "pause_hours": round((bo['settings'].get('pause_hours', 0)), 1),
                },
            }
            apply_config(new_config)
            response = ujson.dumps(new_config)

        elif method == 'GET' and path == '/status':
            response = ujson.dumps({
                "time_ms": int(time.time()+local_time_lag) * 1000,
                "soil_moisture": read_soil_moisture(),
            })

        else:
            response = f"Resource not found: method={method} path={path}"
            status_code = 404

    except Exception as e:
        print(f"Error handling request: {e}")
        writer.write(f'HTTP/1.0 500 {get_status_message(500)}\r\n')
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        raise

    writer.write(f'HTTP/1.0 {status_code} {get_status_message(status_code)}\r\nContent-type: {content_type}\r\n\r\n')
    if filename:
        await serve_file(filename, writer)
    else:
        writer.write(response)
    await writer.drain()
    writer.close()
    await writer.wait_closed()

async def send_status():
    while True:
        requests.get(f"http://api.thingspeak.com/update?api_key={config['options'].get('thingsspeak_apikey', 'RDO96UXJ98Y8OZW9')}&field1={read_soil_moisture()}").close()
        await asyncio.sleep(300)

async def main():
    await connect_wifi()
    if not wlan.isconnected():
        # we can go to wifi setup mode
        print("WiFi connection failed on startup, starting irrigation scheduler, retry reconnecting in background")

    asyncio.create_task(keep_wifi_connected())
    asyncio.create_task(periodic_ntp_sync())
    asyncio.create_task(send_status())

    apply_config(config)

    server = await asyncio.start_server(handle_request, "0.0.0.0", 80)
    print('Server listening on port 80')
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
