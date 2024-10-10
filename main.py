import network
import utime as time
from machine import Pin, ADC, PWM, reset, freq
import esp32
import ujson
import ntptime
import uasyncio as asyncio
import urequests as requests
import gc

# Global variables
micropython_to_timestamp: int = 3155673600 - 2208988800  # 1970-2000
micropython_to_localtime: int = None
soil_sensor: ADC = None
wlan: network.WLAN = network.WLAN(network.STA_IF)
config: dict = None
valve_status: int = 0
schedule_status: int = 0
irrigation_factor: float = 1.0
heartbeat_pin_id: int = 15
wifi_setup_mode = False
# id: str = ':'.join([f"{b:02X}" for b in wlan.config('mac')[3:]]) FIXME: memory allocation failed, no idea why

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
        return None


async def connect_wifi() -> None:
    global irrigation_factor

    try:
        if not config['options']['wifi']['ssid']:
            return
        wlan.active(True)
        wlan.config(pm=wlan.PM_NONE) # disable power management
        print(f'@{time.time()} wifi connecting.', end='')
        wlan.connect(config['options']['wifi']['ssid'], config['options']['wifi']['password'])
        for i in range(15):
            if wlan.isconnected():
                break
            await asyncio.sleep(1)
            print('.', end='')
        if wlan.isconnected():
            print(f"connected, ip = {wlan.ifconfig()[0]}")
            return
        wlan.disconnect()
    except Exception as e:
        print(f"Error keeping wifi connected: {e}")
        if irrigation_factor > 0:
            irrigation_factor = -10
        else:
            irrigation_factor -=1
    wlan.active(False)
    print('network connection failed, retrying in 10 seconds')

async def keep_wifi_connected():

    while True:
        while wlan.isconnected():
            await asyncio.sleep(10)
        await asyncio.sleep(10)


# Time functions
def get_local_timestamp() -> int:
    return time.time()+micropython_to_localtime

def weekday(timestamp: int) -> int:
    # weekday is 0-6 for Mon-Sun.
    return ((timestamp or get_local_timestamp()) // 86400 + 3) % 7

async def sync_ntp() -> bool:
    try:
        ntptime.settime()
        print(f'@{time.time()} NTP synced, UTC time={time.time()+micropython_to_timestamp} Local time(GMT{config['options']['settings']['timezone_offset']:+})={time.time()+micropython_to_localtime}')
        return True
    except:
        print(f'@{time.time()} Error syncing time, current UTC timestamp={time.time()+micropython_to_timestamp}')
        return False

async def periodic_ntp_sync():
    while True:
        await asyncio.sleep(24 * 60 * 60)  # 24 hours, assuming we are already synced
        while not await sync_ntp():
            await asyncio.sleep(10) # 10 seconds

# Watering control functions
def control_watering(zone_id: int, start: bool) -> None:
    if zone_id < 0 or zone_id >= len(config["zones"]):
        print(f"Zone {zone_id} not found")
        return
    zone = config["zones"][zone_id]
    pin_id = zone['on_pin'] if start else zone['off_pin']
    print(f"Zones[{zone_id}]='{zone['name']}' (off_pin={zone['off_pin']}, on_pin={zone['on_pin']}) will be set {start} using pin_id=({pin_id})")
    if pin_id < 0:
        print("NOP pin_id<0")
        return

    pin = Pin(pin_id, Pin.OUT)
    if zone['on_pin'] == zone['off_pin']:
        pin.value(1 if start else 0)
    else:
        # pulse on two pins
        pin.value(1)
        time.sleep(0.060)
        pin.value(0)

    Pin(pin_id, Pin.IN)


async def apply_valves(new_status: int) -> None:
    global valve_status

    if new_status == valve_status:
        return

    print(f"@{time.time()} apply_valves({new_status:08b}), valve_status={valve_status:08b}")

    if relay_pin:=config['options']['settings']['relay_pin'] >= 0:
        relay_pin = Pin(relay_pin, Pin.OUT)
        relay_pin.value(1)
        await asyncio.sleep(0.100) # wait for H-Bridges to power up
    else:
        relay_pin = None

    for i in range(len(config['zones'])):
        if (valve_status^new_status)>>i & 1:
            control_watering(i, new_status>>i & 1)
            await asyncio.sleep(0.050) # wait to settle down
    valve_status = new_status

    if relay_pin:
        relay_pin.value(0)
        relay_pin.mode(Pin.IN)

######################
# Irrigation scheduler
######################
async def schedule_irrigation():
    global schedule_status
    global irrigation_factor
    irrigation_factor_expiration: int = 0

    await asyncio.sleep(5)
    while True:
        if heartbeat_pin_id > 0:
            Pin(heartbeat_pin_id, Pin.OUT).on()

        local_timestamp = get_local_timestamp()

        if config['options']['irrigation_factor']['override'] >= 0:
            irrigation_factor = config['options']['irrigation_factor']['override']
        elif local_timestamp > irrigation_factor_expiration:
            irrigation_factor = 1

        valve_desired = 0
        new_schedule_status = 0
        for i, s in enumerate(config["schedules"]):
            # print(f"@{time.time()} checking schedule={s}")
            if not config['options']['settings']['enable_irrigation_schedule']:
                continue

            if not s['enabled']:
                continue

            if s['expiry'] and local_timestamp > s['expiry']:
                continue

            # TODO: check week days
            # weekday_start = weekday(local_timestamp+sec_till_start) + 6 % 7
            # if ~s['day_mask'] & (1 << weekday()):
            #     continue
            # FIXME %86400 assumes the schedule is within a day, this isn't true for non daily schedules

            sec_till_start = (86400 + s['start_sec'] - local_timestamp % 86400) % 86400
            duration_sec = round(s['duration_sec'])
            sec_till_end = (sec_till_start + duration_sec) % 86400
            if sec_till_end >= sec_till_start:
                # we are not inside the schedule
                continue

            if None != soil_sensor and config['options']['irrigation_factor']['reference_schedule_id'] == i and irrigation_factor_expiration <= local_timestamp + sec_till_end:
                soil_moisture = read_soil_moisture_milli()
                # it's the reference_schedule_id and irrigation_factor is about to expire, we might need to adjust the irrigation factor
                if schedule_status & (1 << i):
                    # reference_schedule_id is active, check if we should stop
                    if soil_moisture >= config['options']['irrigation_factor']['soil_moisture_wet']:
                        irrigation_factor = (local_timestamp - s['start_sec']) % 86400 / s['duration_sec']
                        irrigation_factor_expiration = local_timestamp + sec_till_start + duration_sec
                else:
                    # reference_schedule_id is about to start, is it dry enough?
                    if soil_moisture >= config['options']['irrigation_factor']['soil_moisture_dry']:
                        irrigation_factor = 0
                        irrigation_factor_expiration = local_timestamp + sec_till_start + duration_sec

            if s['enable_irrigation_factor']:
                duration_sec *= irrigation_factor
                # check if we are still inside the schedule (updated duration)
                sec_till_end = (sec_till_start + duration_sec) % 86400
                if sec_till_end >= sec_till_start:
                    continue

            # we should irrigate, set the valve status
            valve_desired |= (1 << s['zone_id'])
            new_schedule_status |= (1 << i)
            # print(f"@{time.time()} valve_desired={valve_desired:08b} for schedule={s}")

        # print(f"@{time.time()} valve_desired={valve_desired:08b}")
        if valve_desired > 0:
            valve_desired |= 1

        await apply_valves(valve_desired)
        schedule_status = new_schedule_status
        if heartbeat_pin_id > 0:
            Pin(heartbeat_pin_id, Pin.IN)
        await asyncio.sleep(2)

#########################
# Configuration functions
#########################
def apply_config(new_config: dict) -> None:
    global config
    global micropython_to_localtime
    global soil_sensor
    global heartbeat_pin_id

    normalized_config = {"zones": [], "schedules": [], "options": {}}
    for zone_data in new_config.get('zones', []):
        normalized_config['zones'].append({
            "name": str(zone_data['name']),
            "on_pin": int(zone_data['on_pin']),
            "off_pin": int(zone_data['off_pin']),
        })
    for schedule_data in new_config.get('schedules', []):
        normalized_config['schedules'].append({
            "zone_id": int(schedule_data['zone_id']),
            "start_sec": int(schedule_data['start_sec']),
            "duration_sec": int(schedule_data['duration_sec']),
            "enable_irrigation_factor": bool(schedule_data['enable_irrigation_factor']),
            "enabled": bool(schedule_data['enabled']),
            "expiry": int(schedule_data.get('expiry', 0)),
        })
    bo = new_config.get('options', {})
    for key in ['wifi', 'irrigation_factor', 'monitoring', 'soil_moisture_sensor', 'settings']:
        bo.setdefault(key, {})
    normalized_config['options'] = {
        "wifi": {
            "ssid": str(bo['wifi'].get('ssid', '')),
            "password": str(bo['wifi'].get('password', '')),
        },
        "irrigation_factor": {
            "override": float(bo['irrigation_factor'].get('override', -1)),
            "reference_schedule_id": int(bo['irrigation_factor'].get('reference_schedule_id', -1)),
            "soil_moisture_dry": int(bo['irrigation_factor'].get('soil_moisture_dry', 300)),
            "soil_moisture_wet": int(bo['irrigation_factor'].get('soil_moisture_wet', 700)),
        },
        "monitoring": {
            "thingsspeak_apikey": str(bo['monitoring'].get('thingsspeak_apikey', '')),
            "send_interval_sec": int(bo['monitoring'].get('send_interval_sec', 300)),
        },
        "soil_moisture_sensor": {
            "adc_pin_id": int(bo['soil_moisture_sensor'].get('adc_pin_id', 12)),
            "power_pin_id": int(bo['soil_moisture_sensor'].get('power_pin_id', 13)),
            "high_is_dry": bool(bo['soil_moisture_sensor'].get('high_is_dry', True)),
        },
        "settings": {
            "enable_irrigation_schedule": bool(bo['settings'].get('enable_irrigation_schedule', True)),
            "timezone_offset": float(bo['settings'].get('timezone_offset', -7)),
            "relay_pin": int(bo['settings'].get('relay_pin', 14)),
            "heartbeat_pin_id": int(bo['settings'].get('heartbeat_pin_id', 15)),
        },
    }

    reference_schedule_id = normalized_config['options']['irrigation_factor']['reference_schedule_id']
    if reference_schedule_id >= 0:
        normalized_config['schedules'][reference_schedule_id]['enable_irrigation_factor'] = True
    # print(f"apply_config({new_config})\n    normalized_config={normalized_config}")

    # if zones changed, turn off all valves
    if config and config.get('zones', []) != normalized_config['zones']:
        apply_valves(0)

    config = normalized_config

    micropython_to_localtime = micropython_to_timestamp + round(config['options']['settings']['timezone_offset'] * 3600)

    soil_moisture_pin_id = config['options']['soil_moisture_sensor']['adc_pin_id']
    soil_sensor = ADC(soil_moisture_pin_id, atten=ADC.ATTN_11DB) if soil_moisture_pin_id >= 0 else None

    heartbeat_pin_id = config['options']['settings']['heartbeat_pin_id']

def read_soil_moisture_milli() -> int:
    if None  == soil_sensor:
        return None
    c = config['options']['soil_moisture_sensor']
    if c['power_pin_id'] >= 0:
        Pin(c['power_pin_id'], Pin.OUT).value(1)
    # https://docs.micropython.org/en/latest/esp32/quickref.html#adc-analog-to-digital-conversion
    time.sleep(0.010)
    milli_moist = int(soil_sensor.read() // 8.191)
    if c['power_pin_id'] >= 0:
        Pin(c['power_pin_id'], Pin.IN)
    return 1000-milli_moist if c['high_is_dry'] else milli_moist

#############
# HTTP server
#############
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
        print(f'served {time.ticks_ms() - start_time}ms')
    except Exception as e:
        print(f"Error serving [{filename}]: {e}")

async def read_http_headers(reader) -> dict:
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
        500: "Server Error"
    }
    return status_messages.get(status_code, "Unknown")

################
# handle_request
################
async def handle_request(reader, writer):
    content_type = 'application/json'
    status_code = 200
    filename = None

    try:
        method, path, _ = (await reader.readline()).decode().strip().split(' ')
        path, query_params = path.split('?') if '?' in path else (path, None)
        query_params = dict([param.replace('+', ' ').split('=') for param in query_params.split('&')]) if query_params else {}

        headers = await read_http_headers(reader)
        content_length = int(headers.get('content-length', '0'))

        print(f"@{time.time()} Request: {method:4} {path:14} query_params={query_params}, (content_length={content_length})")  #     headers={headers}")

        body = ujson.loads((await reader.read(content_length)).decode()) if content_length > 0 else None

        if method == 'GET' and path == '/':
            filename = 'setup.html' if wifi_setup_mode else 'index.html'
            content_type = 'text/html'
        elif method == 'GET' and path == '/favicon.ico':
            content_type = 'image/svg+xml'
            response = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path d="M50 5 C30 5 5 35 5 60 C5 85 25 95 50 95 C75 95 95 85 95 60 C95 35 70 5 50 5Z" fill="#4FC3F7" stroke="#29B6F6" stroke-width="2"/><ellipse cx="30" cy="35" rx="10" ry="15" fill="#81D4FA" transform="rotate(-35 30 35)"/></svg>'
        elif method == 'GET' and path == '/config':
            # curl example: curl http://[ESP32_IP]/config
            response = ujson.dumps(config)
        elif method == 'POST' and path == '/config':
            # restore backup: jq . irrigation-config.json | curl -H "Content-Type: application/json" -X POST --data-binary @- http://192.168.68.ESP/config
            print(f"applying new config = {body}")
            apply_config(body)
            response = ujson.dumps(config)
            save_data('config.json', config)
        elif method == 'GET' and path == '/status':
            # tt = time.gmtime()
            response = ujson.dumps({
                "local_timestamp": get_local_timestamp(),
                "soil_moisture": read_soil_moisture_milli(),
                "gc.mem_alloc": gc.mem_alloc(),
                "valve_status": f"{valve_status:08b}",
                "schedule_status": f"{schedule_status:08b}",
                "mcu_temperature": esp32.mcu_temperature(),
                "irrigation_factor": irrigation_factor,
            })
        elif wifi_setup_mode and method == 'GET' and path == '/setup':
                print(f"Setup: query_params={query_params}")
                save_data('config.json', {"options": { "wifi": query_params }})
                print("Restarting...")
                await asyncio.sleep(0.1)
                if heartbeat_pin_id > 0:
                    Pin(heartbeat_pin_id, Pin.IN)
                reset()

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

async def send_metrics():
    global irrigation_factor

    while True:
        try:
            # TODO: add micropython.mem_info()
            if 'thingsspeak_apikey' in config['options']['monitoring']:
                requests.get(f"http://api.thingspeak.com/update?api_key={config['options']['monitoring']['thingsspeak_apikey']}&field1={read_soil_moisture_milli()}&field2={gc.mem_alloc()}&field3={valve_status}&field4={irrigation_factor}&field5={esp32.mcu_temperature()}", timeout=10).close()
            await asyncio.sleep(config['options']['monitoring']['send_interval_sec'])
        except Exception as e:
            if irrigation_factor > 0:
                irrigation_factor = -1_000_000_000
            else:
                irrigation_factor -=1000
            await asyncio.sleep(config['options']['monitoring']['send_interval_sec'])

async def wait_for_wifi_setup(wait_time: int) -> None:
    global wifi_setup_mode

    for _ in range(round(wait_time*10)):
        await asyncio.sleep(0.1)
        if 0 == Pin(0, Pin.IN, Pin.PULL_UP).value():
            wifi_setup_mode = True
            break
    if wifi_setup_mode:
        if heartbeat_pin_id > 0:
            PWM(Pin(heartbeat_pin_id), freq=5, duty_u16=32768)
        ap = network.WLAN(network.AP_IF)
        ap.active(True)
        ap.config(essid='irrigation-esp32')
        server = await asyncio.start_server(handle_request, "0.0.0.0", 80)
        print('Server listening on port 80')
        await server.wait_closed()

async def main():
    global valve_status

    freq(80_000_000)
    await wait_for_wifi_setup(1)

    apply_config(load_data('config.json') or {})
    # force all valves off
    valve_status = (1<<len(config['zones']))-1
    await apply_valves(0)

    await connect_wifi()
    await sync_ntp()
    # if not wlan.isconnected():
        # we can go to wifi setup mode
        # print("WiFi connection failed on startup, starting irrigation scheduler, will retry reconnecting in background")
    asyncio.create_task(keep_wifi_connected())
    asyncio.create_task(periodic_ntp_sync())
    asyncio.create_task(send_metrics())
    asyncio.create_task(schedule_irrigation())

    server = await asyncio.start_server(handle_request, "0.0.0.0", 80)
    print('Server listening on port 80')
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
