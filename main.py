import time  # needed for ntptime and/or getting uptime
import _thread
import re
import utime
import esp32
from machine import Pin, RTC, TouchPad, I2C, SoftI2C
import network
import ntptime
import ubinascii
import webrepl
import humidistat
import anytemp
import ssd1306


from mqtt import MQTTClient

try:
    import usocket as socket
except:
    import socket

# Pins and pin configs
SDA_PIN = 21
SCL_PIN = 22
SDA_PIN_SOFT = 18
SCL_PIN_SOFT = 19
led = Pin(2,Pin.OUT)  # for onboard LED blink
DO_DISPLAY = False
DO_POWER_ON = False
GPIO_PIN = 13
TOUCH_PIN = 15
TOUCH_MAX_VALUE = 250  # 625 when not touching, 120 when touching, check less than this value

# Event timing
HUMIDITY_EVALUATION_INTERVAL_SECONDS = 60
MQTT_REPORTING_INTERVAL_SECONDS = 300

# Wifi object
wlan = network.WLAN(network.STA_IF)

# MQTT
CLIENT_ID = ubinascii.hexlify(machine.unique_id())
TOPIC_SUB = b'home/%s/metrics' % (remote_dev)
TOPIC_PUB = b'home/%s/metrics' % (dev_name)

# metric variables
message_interval = 300  # duration of deep sleep
SIGNAL = 0
TEMPERATURE_STRING = ""
HUMIDITY_VAL = 0
HUMIDITY_STRING = ""
# PRESSURE_STRING = ""
IP = ""

# Humidistat
hs = humidistat.Humidistat(GPIO_PIN)
HUMIDITY_DESIRED = 40
HUMIDITY_REMOTE = 0

# I2C
# 60 (0x3c) = ssd1306, 118 (0x76) = bme280, 56 (0x38) = aht10
i2c = I2C(1, scl=Pin(SCL_PIN), sda=Pin(SDA_PIN), freq=400000)
i2c_s = SoftI2C(sda=Pin(SDA_PIN_SOFT), scl=Pin(SCL_PIN_SOFT))
# print(i2c.scan())  # to debug I2C
# print(i2c_s.scan())  # to debug SoftI2C

# Create display object using I2C
display = ssd1306.SSD1306_I2C(128, 64, i2c)
display.contrast(50)

# Create AnyTemp object (abstraction for different temp sensors)
temp_sensor = anytemp.AnyTemp(i2c_s, temp_sensor_model)

def wifi_connect(fatal=True):
    global IP
    wlan.active(True)
    if not wlan.isconnected():
        print('\nConnecting to network', end='')
        wlan.connect(wifi_ssid, wifi_password)
        retry = 0
        while not wlan.isconnected():
            if retry >= 20:
                print('WiFi retry limited reached')
                if fatal:
                    restart_device()
                else:
                    return
            print('.', end='')
            utime.sleep(3.0)
            retry += 1
            pass
    print()
    print("Interface's MAC: ", ubinascii.hexlify(network.WLAN().config('mac'),':').decode()) # print the interface's MAC
    IP = wlan.ifconfig()
    print("Interface's IP/netmask/gw/DNS: ", IP,"\n") # print the interface's IP/netmask/gw/DNS addresses

def setup_ntp():
    print("Local time before synchronization：%s" %str(time.localtime()))
    ntptime.host = ntp_server
    ntptime.settime()
    print("Local time after synchronization：%s" %str(time.localtime()))
    (year, month, mday, week_of_year, hour, minute, second, milisecond)=RTC().datetime()
    hour = hour + hour_adjust
    RTC().init((year, month, mday, week_of_year, hour, minute, second, milisecond)) # GMT correction. GMT-7
    print("Local time after timezone offset: %s" %str(time.localtime()))
    print("{}/{:02d}/{:02d} {:02d}:{:02d}:{:02d}".format(RTC().datetime()[0], RTC().datetime()[1], RTC().datetime()[2], RTC().datetime()[4], RTC().datetime()[5],RTC().datetime()[6]))

def send_metrics(client):
    msg = b'{{"s":"{0}","t":"{1}","h":"{2}","r":"{3}","d":"{4}"}}'.format(SIGNAL, TEMPERATURE_STRING, HUMIDITY_STRING, hs.state, hs.humidity_desired)
    try:
        client.publish(TOPIC_PUB, msg)
    except:
        print('MQTT: publish failed')
        return
    print('MQTT: published metrics')

def sub_cb(topic, msg):
    last_receive = time.time()
    global HUMIDITY_REMOTE
    print('%s: received message on topic %s with msg: %s' % (last_receive, topic, msg))
    if topic == TOPIC_SUB:
        re_humidity_val = re.compile("h\":\"(.+?)\"")
        m = re_humidity_val.search(str(msg))
        if m:
            HUMIDITY_REMOTE = round(float(m.group(1)), 1)

def mqtt_connect_and_subscribe():
    global CLIENT_ID, mqtt_server, TOPIC_SUB
    retry = 0
    while True:
        try:
            client = MQTTClient(CLIENT_ID, mqtt_server, port=1883, user=mqtt_user, password=mqtt_password)
            if remote_sensor:
                client.set_callback(sub_cb)
            client.connect()
            if remote_sensor:
                client.subscribe(TOPIC_SUB)
                print('Connected to %s MQTT broker, subscribed to %s topic' % (mqtt_server, TOPIC_SUB))
            else:
                print('Connected to %s MQTT broker' % (mqtt_server))
            return client
        except:
            if retry >= 5:
                print('MQTT retry limited reached')
                return
            print('.', end='')
            utime.sleep(3.0)
            retry += 1
            pass

def restart_device():
    print('Failed to connect to MQTT broker. Restarting...')
    utime.sleep(10)
    machine.reset()

def blink():
    led.on()
    utime.sleep_ms(500)
    led.off()
    utime.sleep_ms(500)

def draw_display():
    display.fill(0)  # clear display by filling with black
    display.rect(0, 0, 128, 64, 1)
    display.hline(0, 50, 128, 1)

    display.text(str(IP[0]), 2, 54, 1)
    display.text(str(SIGNAL), 100, 2, 1)

    if TEMPERATURE_STRING:
        temperature_display = TEMPERATURE_STRING + ' F'
        display.text(temperature_display, 2, 4, 1)
    if HUMIDITY_STRING:
        humidity_display = HUMIDITY_STRING + '%'
        display.text(humidity_display, 2, 18, 1)
    # if PRESSURE_STRING:
    #     display.text(PRESSURE_STRING, 2, 32, 1)
    humidity_desired_display = 'desired:' + str(HUMIDITY_DESIRED) + '%'
    display.text(humidity_desired_display, 2, 32, 1)
    if hs.state == 1:
        switch_display = "on"
    else:
        switch_display = "off"
    display.text(switch_display, 100, 32, 1)

    retry = 3
    while retry > 0:
        try:
            display.show()
            break
        except:
            print("retry display (usually I2C timeout when waking from capacitive touch")
            utime.sleep(0.5)
            retry -= 1
            continue

def wait_for_sensor(sleep_sec):
    print('wait %s seconds on start' % (sleep_sec))
    while sleep_sec > 0:
        utime.sleep(1)
        sleep_sec -= 1

def get_metrics_local():
    # variables used in display (TODO: pass w/ kwargs)
    global TEMPERATURE_STRING
    global HUMIDITY_STRING
    global HUMIDITY_VAL
    # global PRESSURE_STRING
    global SIGNAL

    temp_sensor.read()
    temperature_val = temp_sensor.temperature
    HUMIDITY_VAL = temp_sensor.humidity
    # PRESSURE_STRING = temp_sensor.pressure

    TEMPERATURE_STRING = "{:0.1f}".format(round(temperature_val, 1))
    HUMIDITY_STRING = "{:0.1f}".format(round(HUMIDITY_VAL, 1))

    print(TEMPERATURE_STRING)
    print(HUMIDITY_STRING)
    # print(PRESSURE_STRING)

    SIGNAL = wlan.status('rssi')
    print(SIGNAL)

def display_metrics(display_sec):
    display.poweron()
    draw_display()
    utime.sleep(display_sec)
    display.fill(0)  # clear display by filling with black
    display.poweroff() # power off the display, pixels persist in memory

def humidistat_thread():

    max_retry = 3
    # Connect to MQTT
    print("start mqtt")
    try:
        client = mqtt_connect_and_subscribe()
    except OSError as e:
        print('MQTT: failed to connect')
        return
    print("MQTT: connected")

    # Setup humidistat
    hs.set_humidity_percent(HUMIDITY_DESIRED)
    hs.enable()


    # Initialize last_mqtt_time so MQTT message is sent the first time
    last_mqtt_time = time.time() - MQTT_REPORTING_INTERVAL_SECONDS

    while True:
        get_metrics_local()

        if remote_sensor:
            # check for remote humidity
            try:
                client.check_msg()
                humidity_eval = HUMIDITY_REMOTE
            except Exception as e:
                # If anything fails, reconnect WiFi and MQTT
                print('err: {0}, reconnect WiFi and MQTT'.format(e))
                wifi_connect(fatal=False)
                client = mqtt_connect_and_subscribe()
                continue
        else:
            # use local sensor
            humidity_eval = HUMIDITY_VAL

        send = False
        time_current = time.time()

        if hs.evaluate(humidity_eval):
            # evaluate returns True if anything changed so send update
            send = True
        elif time_current - last_mqtt_time >= MQTT_REPORTING_INTERVAL_SECONDS:
            send = True

        if send:
            try:
                send_metrics(client)
                last_mqtt_time = time_current
            except Exception as e:
                # If anything fails, reconnect WiFi and MQTT
                print('err: {0}, reconnect WiFi and MQTT'.format(e))
                wifi_connect(fatal=False)
                client = mqtt_connect_and_subscribe()
                continue      

        utime.sleep(HUMIDITY_EVALUATION_INTERVAL_SECONDS)

def monitor_touchpad_thread():
    # Setup touchpad sensor
    # https://mpython.readthedocs.io/en/master/library/micropython/machine/machine.TouchPad.html
    touch0 = TouchPad(Pin(TOUCH_PIN))
    touch0.config(TOUCH_MAX_VALUE)

    while True:
        touch_val = touch0.read()
        if touch_val < TOUCH_MAX_VALUE:
            print("touch activated")
            display_metrics(10)
        utime.sleep(1)

def web_page():

    # if gpioSwitch.value() == 1:
    if hs.state == 1:
        gpio_state="ON"
    else:
        gpio_state="OFF"
    print('gpio_state={0}'.format(gpio_state))
    state_msg = hs.get_last_activity_msg()
    mode = "Auto" # hs.mode == 2 is auto
    if hs.mode == 0:
        mode = "Off"
    if hs.mode == 1:
        mode = "On"
    if remote_sensor:
        humidity_curr_string = '{0} ({1})'.format(HUMIDITY_STRING, HUMIDITY_REMOTE)
    else:
        humidity_curr_string = HUMIDITY_STRING

    html = """<html>

<head>
    <title>Humidity Switch #1</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        html {
            font-family: Arial;
            display: inline-block;
            margin: 0px auto;
            text-align: center;
        }

        .button {
            background-color: #ce1b0e;
            border: none;
            color: white;
            padding: 16px 40px;
            text-align: center;
            text-decoration: none;
            display: inline-block;
            font-size: 16px;
            margin: 4px 2px;
            cursor: pointer;
        }

        .button1 {
            background-color: #000000;
        }
    </style>
</head>

<body>
    <h2>ESP MicroPython Web Server</h2>
    <p>Current Temperature: <strong>""" + TEMPERATURE_STRING + """</strong></p>
    <p>Current Humity: <strong>""" + humidity_curr_string + """</strong></p>
    <p>Desired Humity: <strong>""" + str(HUMIDITY_DESIRED) + """</strong></p>
    <p>Mode: """ + mode + """</p>
    <p>GPIO state: <strong>""" + gpio_state + """</strong></p>
    <p><strong>""" + state_msg + """</strong></p>
    <p><strong><a href=\".\">refresh</a></strong></p>
    <p>
        <a href=\"?gpioSwitch=on\"><button class="button">GPIO ON</button></a>
    </p>
    <p>
        <a href=\"?gpioSwitch=off\"><button class="button button1">GPIO OFF</button></a>
    </p>
    <form action="/" method="POST"><center>
      <input type="text" name="set_humidity" placeholder="set_humidity"><br>
      <left><button type="submit">Submit</button></left>
    </center></form>
</body>

</html>"""
    return html

def web_server_thread():
    # Setup webserver
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 80))
    s.listen(5)
    re_set_humidity = re.compile("set_humidity=(\d+)")
    global HUMIDITY_DESIRED

    while True:
        try:
            conn, addr = s.accept()
            print('Got a connection from %s' % str(addr))
            request = conn.recv(1024)
            request = str(request)
            print('Content = %s' % request)
            gpio_switch_on = request.find('/?gpioSwitch=on')  # returns -1 when not found
            gpio_switch_off = request.find('/?gpioSwitch=off')
            if gpio_switch_on == 6:
                print('GPIO ON')
                hs.mode = humidistat.MODE_ON
                hs.evaluate(HUMIDITY_VAL, True) # evaluate humidity with overrides
            if gpio_switch_off == 6:
                print('GPIO OFF')
                hs.mode = humidistat.MODE_OFF
                hs.evaluate(HUMIDITY_VAL, True) # evaluate humidity with overrides
            m = re_set_humidity.search(request)
            if m:
                result = m.group(1)
                print("Setting humidity")
                print(result)
                HUMIDITY_DESIRED = int(result)
                hs.set_humidity_percent(HUMIDITY_DESIRED)
                hs.set_mode(2) # MODE_AUTO
                hs.evaluate(HUMIDITY_VAL, True) # evaluate humidity with overrides
            response = web_page()
            conn.send('HTTP/1.1 200 OK\n')
            conn.send('Content-Type: text/html\n')
            conn.send('Connection: close\n\n')
            conn.sendall(response)
            conn.close()
        except OSError as e:
            print('webserver OS error: %s' % e)
        except Exception as e:
            print('webserver unknown error: %s' % e)


# check how the ESP32 was started up (mainly by touch sensor, hard power on, soft reboot)
boot_reason = machine.reset_cause()
if boot_reason == machine.DEEPSLEEP_RESET:
    print('woke from a deep sleep')  # constant = 4
    wake_reason = machine.wake_reason()
    print("Device running for: " + str(utime.ticks_ms()) + "ms")
    print("wake_reason: " + str(wake_reason))
    if wake_reason == machine.PIN_WAKE:
        print("Woke up by external pin (external interrupt)")
    elif wake_reason == 4:  # machine.RTC_WAKE, but constant doesn't exist
        print("Woke up by RTC (timer ran out)")
    elif wake_reason == 5:  # machine.ULP_WAKE, but constant doesn't match
        print("Woke up capacitive touch")
        DO_DISPLAY = True
elif boot_reason == machine.SOFT_RESET:
    print('soft reset detected')  # constant = 5
elif boot_reason == machine.PWRON_RESET:
    print('power on detected') # constant = 1
    # This is used for 2 main reasons:
    # 1. Safety net in case there are issues with deep sleep that makes it difficult to re-upload
    # 2. Often the sensors need a few seconds to get accurate readings when power is first applied
    #    except deep sleep should cut power on the regulated 3.3V pin, but not 5Vin
    # DO_POWER_ON = True
    # SIGNAL = 'NA'
    wait_for_sensor(6)
    # DO_DISPLAY = True
elif boot_reason == machine.WDT_RESET:
    print('WDT_RESET detected') # constant = 3
    # This also seems to indicate a hard power on
    # DO_POWER_ON = True
    # SIGNAL = 'NA'
    wait_for_sensor(6)
    # DO_DISPLAY = True
else:
    print('boot_reason={0}'.format(boot_reason))

# Connect WiFi
print("connect wifi")
wifi_connect()

print("start webrepl")
webrepl.start()

setup_ntp()

print("starting web_server_thread")
_thread.start_new_thread(web_server_thread, ())
print("starting monitor_touchpad_thread")
_thread.start_new_thread(monitor_touchpad_thread, ())

wait_for_sensor(20)

print("starting humidistat_thread")
_thread.start_new_thread(humidistat_thread, ())

print("done starting threads")
while True:
    utime.sleep(600)
    print("performing garbage collection")
    gc.collect()   #Perform garbage collection
