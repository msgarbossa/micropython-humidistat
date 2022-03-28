
from machine import Pin, ADC, Timer, RTC, TouchPad, deepsleep, I2C, SoftI2C, UART
import esp32
from mqtt import MQTTClient 
import utime
import ssd1306
import webrepl
import time  # needed for ntptime and/or getting uptime
import network
import ntptime
import ubinascii
import _thread
import humidistat
import anytemp
import re

try:
  import usocket as socket
except:
  import socket

# Pins and pin configs
sdaPin = 21
sclPin = 22
sdaPinSoft = 18
sclPinSoft = 19
led = Pin(2,Pin.OUT)  # for onboard LED blink
doDisplay = False
doPowerOn = False
gpioPin = 13
touchPin = 15
touchMaxValue = 250  # 625 when not touching, 120 when touching, check less than this value

# Event timing
humidityEvaluationIntervalSeconds = 60
mqttReportingIntervalSeconds = 300

# Wifi object
wlan = network.WLAN(network.STA_IF)

# MQTT
client_id = ubinascii.hexlify(machine.unique_id())
topic_sub = b'home/%s/cmd' % (dev_name)
topic_pub = b'home/%s/metrics' % (dev_name)

# metric variables
message_interval = 300  # duration of deep sleep
signal = 0
temperature_string = ""
humidity_val = 0
humidity_string = ""
pressure_string = ""
status = ""
ip = ""

# Humidistat
hs = humidistat.Humidistat(gpioPin)
humidity_desired = 40

# I2C
# 60 (0x3c) = ssd1306, 118 (0x76) = bme280, 56 (0x38) = aht10
i2c = I2C(1, scl=Pin(sclPin), sda=Pin(sdaPin), freq=400000)
i2c_s = SoftI2C(sda=Pin(sdaPinSoft), scl=Pin(sclPinSoft))
# print(i2c.scan())  # to debug I2C
# print(i2c_s.scan())  # to debug SoftI2C

# Create display object using I2C
display = ssd1306.SSD1306_I2C(128, 64, i2c)
display.contrast(50)

# Create AnyTemp object (abstraction for different temp sensors)
tempSensor = anytemp.AnyTemp(i2c_s, temp_sensor_model)

def wifi_connect(wifi_ssid,wifi_passwd):
    global ip
    wlan.active(True)
    if not wlan.isconnected():
        print('\nConnecting to network', end='')
        wlan.connect(wifi_ssid, wifi_passwd)
        retry = 0
        while not wlan.isconnected():
            if retry >= 20:
                print('WiFi retry limited reached')
                restart_and_reconnect()
            print('.', end='')
            utime.sleep(0.5)
            retry += 1
            pass
    print()
    print("Interface's MAC: ", ubinascii.hexlify(network.WLAN().config('mac'),':').decode()) # print the interface's MAC
    ip = wlan.ifconfig()
    print("Interface's IP/netmask/gw/DNS: ", ip,"\n") # print the interface's IP/netmask/gw/DNS addresses

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

def mqttConnect():
    global client_id, mqtt_server
    client = MQTTClient(client_id, mqtt_server, port=1883, user=mqtt_user, password=mqtt_password)
    client.connect()
    print('Connected to %s MQTT broker' % (mqtt_server))
    return client

def restart_and_reconnect():
    print('Failed to connect to MQTT broker. Reconnecting...')
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

    display.text(str(ip[0]), 2, 54, 1)
    display.text(str(signal), 100, 2, 1)

    if temperature_string:
        temperature_display = temperature_string + ' F'
        display.text(temperature_display, 2, 4, 1)
    if humidity_string:
        humidity_display = humidity_string + '%'
        display.text(humidity_display, 2, 18, 1)
    # if pressure_string:
    #     display.text(pressure_string, 2, 32, 1)
    humidity_desired_display = 'desired:' + str(humidity_desired) + '%'
    display.text(humidity_desired_display, 2, 32, 1)
    if hs.state == 1:
      switchDisplay = "on"
    else:
      switchDisplay = "off"
    display.text(switchDisplay, 100, 32, 1)

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

def get_metrics():
    # variables used in display (TODO: pass w/ kwargs)
    global temperature_string
    global humidity_string
    global humidity_val
    global pressure_string
    global status
    global signal

    tempSensor.read()
    temperature_val = tempSensor.temperature
    humidity_val = tempSensor.humidity
    pressure_string = tempSensor.pressure

    temperature_string = "{:0.1f}".format(round(temperature_val, 1))
    humidity_string = "{:0.1f}".format(round(humidity_val, 1))

    print(temperature_string)
    print(humidity_string)
    print(pressure_string)

    signal = wlan.status('rssi')
    print(signal)

def display_metrics(display_sec):
    display.poweron()
    draw_display()
    utime.sleep(display_sec)
    display.fill(0)  # clear display by filling with black
    display.poweroff() # power off the display, pixels persist in memory

def send_metrics():
    msg = b'{{"s":"{0}","t":"{1}","h":"{2}","r":"{3}"}}'.format(signal, temperature_string, humidity_string, hs.state)

    # Connect to MQTT
    print("start mqtt")
    try:
        client = mqttConnect()
    except OSError as e:
        print('MQTT: failed to connect')
        return
        # restart_and_reconnect()
    print("MQTT: connected")

    try:
        client.publish(topic_pub, msg)
    except:
        print('MQTT: publish failed')
        return
        # restart_and_reconnect()

    print('MQTT: published metrics')

def humidistatThread():

    # Setup humidistat
    hs.SetHumidityPercent(humidity_desired)
    hs.Enable()

    # Initialize lastMqttTime so MQTT message is sent the first time
    lastMqttTime = time.time() - mqttReportingIntervalSeconds

    while True:
        get_metrics()
        hs.Evaluate(humidity_val)
        timeCurrent = time.time()
        if timeCurrent - lastMqttTime >= mqttReportingIntervalSeconds:
            send_metrics()
            lastMqttTime = timeCurrent
        utime.sleep(humidityEvaluationIntervalSeconds)

def monitorTouchpadThread():
    # Setup touchpad sensor
    # https://mpython.readthedocs.io/en/master/library/micropython/machine/machine.TouchPad.html
    touch0 = TouchPad(Pin(touchPin))
    touch0.config(touchMaxValue)

    while True:
        touchVal = touch0.read()
        if touchVal < touchMaxValue:
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
    state_msg = hs.GetLastActivityMsg()
    mode = "Auto" # hs.mode == 2 is auto
    if hs.mode == 0:
        mode = "Off"
    if hs.mode == 1:
        mode = "On"

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
    <p>Current Temperature: <strong>""" + temperature_string + """</strong></p>
    <p>Current Humity: <strong>""" + humidity_string + """</strong></p>
    <p>Desired Humity: <strong>""" + str(humidity_desired) + """</strong></p>
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

def webServerThread():
    # Setup webserver
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 80))
    s.listen(5)
    re_set_humidity = re.compile("set_humidity=(\d+)")
    global humidity_desired

    while True:
        try:
            conn, addr = s.accept()
            print('Got a connection from %s' % str(addr))
            request = conn.recv(1024)
            request = str(request)
            print('Content = %s' % request)
            gpioSwitch_on = request.find('/?gpioSwitch=on')  # returns -1 when not found
            gpioSwitch_off = request.find('/?gpioSwitch=off')
            if gpioSwitch_on == 6:
                print('GPIO ON')
                hs.mode = humidistat.MODE_ON
                hs.Evaluate
            if gpioSwitch_off == 6:
                print('GPIO OFF')
                hs.mode = humidistat.MODE_OFF
                hs.Evaluate
            m = re_set_humidity.search(request)
            if m:
                result = m.group(1)
                print("Setting humidity")
                print(result)
                humidity_desired = int(result)
                hs.SetHumidityPercent(humidity_desired)
                hs.Evaluate(humidity_val, True) # Evaluate humidity with overrides
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
      doDisplay = True
elif boot_reason == machine.SOFT_RESET:
  print('soft reset detected')  # constant = 5
elif boot_reason == machine.PWRON_RESET:
  print('power on detected') # constant = 1
  # This is used for 2 main reasons:
  # 1. Safety net in case there are issues with deep sleep that makes it difficult to re-upload
  # 2. Often the sensors need a few seconds to get accurate readings when power is first applied
  #    except deep sleep should cut power on the regulated 3.3V pin, but not 5Vin
  # doPowerOn = True
  # signal = 'NA'
  wait_for_sensor(6)
  # doDisplay = True
elif boot_reason == machine.WDT_RESET:
  print('WDT_RESET detected') # constant = 3
  # This also seems to indicate a hard power on
  # doPowerOn = True
  # signal = 'NA'
  wait_for_sensor(6)
  # doDisplay = True
else:
  print('boot_reason={0}'.format(boot_reason))

# Connect WiFi
print("connect wifi")
wifi_connect(ssid, password)

print("start webrepl")
webrepl.start()

setup_ntp()

print("starting webServerThread")
_thread.start_new_thread(webServerThread, ())
print("starting monitorTouchpadThread")
_thread.start_new_thread(monitorTouchpadThread, ())

wait_for_sensor(20)

print("starting humidistatThread")
_thread.start_new_thread(humidistatThread, ())

print("done starting threads")
while(True):
    utime.sleep(600)
    print("performing garbage collection")
    gc.collect()   #Perform garbage collection
