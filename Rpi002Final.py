##############################################################
# IMPORT LIBRARIES
##############################################################
from tkinter import *
from tkinter import messagebox
from tkinter import scrolledtext
import threading
import time
import board
import adafruit_dht
import mpu6050 as mpu6050_module
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import RPi.GPIO as GPIO
import paho.mqtt.client as mqtt

##############################################################
# GPIO SETUP
##############################################################
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

# LED pin assignment
LED_RED    = 17   # Auto: ON when temp > threshold
LED_YELLOW = 27   # Auto: ON when intensity > threshold
LED_GREEN  = 22   # Controlled by Telegram via Rpi001

GPIO.setup(LED_RED,    GPIO.OUT, initial=0)
GPIO.setup(LED_YELLOW, GPIO.OUT, initial=0)
GPIO.setup(LED_GREEN,  GPIO.OUT, initial=0)

################
# SENSOR SETUP 
################
dhtDevice  = None
mpu_sensor = None
ads        = None
ldr_ch     = None

try:
    dhtDevice = adafruit_dht.DHT22(board.D18)
except Exception as e:
    print(f"[ERROR] DHT22 not found: {e}")

try:
    mpu_sensor = mpu6050_module.mpu6050(0x68)
except Exception as e:
    print(f"[ERROR] MPU6050 not found: {e}")

try:
    i2c = busio.I2C(board.SCL, board.SDA)
    while not i2c.try_lock():
        pass
    i2c.unlock()
    ads    = ADS.ADS1115(i2c, address=0x48)
    ldr_ch = AnalogIn(ads, 0)
    print("[OK] ADS1115 initialized successfully")
except Exception as e:
    ads    = None
    ldr_ch = None
    print(f"[ERROR] ADS1115 init failed: {e}")

##############################################################
# MQTT CONFIGURATION
##############################################################
BROKER = "10.10.3.150"
PORT   = 1883

# Topics Rpi002 PUBLISHES → Rpi001
TOPIC_TEMP      = "home/sensor/temperature"
TOPIC_HUMI      = "home/sensor/humidity"
TOPIC_INTENSITY = "home/sensor/intensity"
TOPIC_MOTION    = "home/sensor/motion"

# Topics Rpi002 SUBSCRIBES ← Rpi001
TOPIC_LED_RED      = "home/led/red"
TOPIC_LED_YELLOW   = "home/led/yellow"
TOPIC_LED_GREEN    = "home/led/green"
TOPIC_THRESH_TEMP  = "home/threshold/temp"
TOPIC_THRESH_LIGHT = "home/threshold/light"

# LED physical status feedback 
TOPIC_RED_STATUS    = "home/led/red/status"
TOPIC_YELLOW_STATUS = "home/led/yellow/status"
TOPIC_GREEN_STATUS  = "home/led/green/status"

# Data request topic
TOPIC_DATA_REQUEST = "home/request/data"

##############################################################
# USER-DEFINED THRESHOLDS
##############################################################
TEMP_THRESHOLD      = 35.0
INTENSITY_THRESHOLD = 30.0

##############################################################
# COLOUR PALETTE
##############################################################
BG_DARK      = "#0f1117"
BG_PANEL     = "#1a1d2e"
BG_MENU      = "#12141f"
ACCENT       = "#00d4ff"
ACCENT2      = "#7c3aed"
SUCCESS      = "#22c55e"
WARNING      = "#f59e0b"
DANGER       = "#ef4444"
TEXT_PRIMARY = "#e2e8f0"
TEXT_MUTED   = "#64748b"
BTN_DEFAULT  = "#1e293b"
BTN_HOVER    = "#334155"
BORDER       = "#2d3748"

##############################################################
# SHARED STATE
##############################################################
latest = {
    "temp":      "--",
    "humi":      "--",
    "intensity": "--",
    "motion":    "0",
}

mqtt_connected    = False
dht_active        = False
mpu_active        = False
ldr_active        = False
last_motion_state = 0
GYRO_THRESH       = 1.0

##############################################################
# HELPER 
##############################################################
def safe_publish(topic, value):
    if mqtt_connected:
        mqtt_client.publish(topic, str(value))

#########################################################################################
# MASTER LED CONTROLLER-handles RED, YELLOW and GREEN — all publish status back to Rpi001
#########################################################################################
def change_led_state(color, state_bool):
    val = 1 if state_bool else 0
    if color == "RED":
        GPIO.output(LED_RED, val)
        update_led_label("RED", state_bool)
        safe_publish(TOPIC_RED_STATUS, val)       # publish RED physical state
    elif color == "YELLOW":
        GPIO.output(LED_YELLOW, val)
        update_led_label("YELLOW", state_bool)
        safe_publish(TOPIC_YELLOW_STATUS, val)    # publish YELLOW physical state
    elif color == "GREEN":
        GPIO.output(LED_GREEN, val)
        update_led_label("GREEN", state_bool)
        safe_publish(TOPIC_GREEN_STATUS, val)

##############################################################
# THRESHOLD LED FLASH FEEDBACK
# Temp threshold updated  → RED LED flashes 3×
# Light threshold updated → YELLOW LED flashes 3×
##############################################################
def flash_led(color, times=3, on_ms=300, off_ms=200):
    """Flash a LED to confirm threshold was received from Telegram."""
    def _flash():
        pin_map = {"RED": LED_RED, "YELLOW": LED_YELLOW, "GREEN": LED_GREEN}
        pin = pin_map.get(color)
        if pin is None:
            return
        for _ in range(times):
            GPIO.output(pin, 1)
            time.sleep(on_ms / 1000)
            GPIO.output(pin, 0)
            time.sleep(off_ms / 1000)
        # After flashing, restore correct LED state
        window.after(100, restore_led_after_flash, color)

    threading.Thread(target=_flash, daemon=True).start()

def restore_led_after_flash(color):
    """Re-apply the correct LED state after flash completes."""
    if color == "RED":
        if latest["temp"] != "--" and float(latest["temp"]) > TEMP_THRESHOLD:
            change_led_state("RED", True)
        else:
            change_led_state("RED", False)
    elif color == "YELLOW":
        if latest["intensity"] != "--" and float(latest["intensity"]) > INTENSITY_THRESHOLD:
            change_led_state("YELLOW", True)
        else:
            change_led_state("YELLOW", False)

##############################################################
# MQTT CLIENT
##############################################################
def on_connect(client, userdata, flags, rc):
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        print("[MQTT] Connected")
        client.subscribe(TOPIC_LED_RED)
        client.subscribe(TOPIC_LED_YELLOW)
        client.subscribe(TOPIC_LED_GREEN)
        client.subscribe(TOPIC_THRESH_TEMP)
        client.subscribe(TOPIC_THRESH_LIGHT)
        client.subscribe(TOPIC_DATA_REQUEST)
        # Publish initial OFF states on connect
        change_led_state("RED",    False)
        change_led_state("YELLOW", False)
        change_led_state("GREEN",  False)
    else:
        mqtt_connected = False

def on_disconnect(client, userdata, rc):
    global mqtt_connected
    mqtt_connected = False

def on_message(client, userdata, msg):
    global TEMP_THRESHOLD, INTENSITY_THRESHOLD
    payload = msg.payload.decode().strip().upper()
    topic   = msg.topic

    # LED commands from Telegram
    if topic == TOPIC_LED_RED:
        change_led_state("RED", payload in ["1", "ON"])

    elif topic == TOPIC_LED_YELLOW:
        change_led_state("YELLOW", payload in ["1", "ON"])

    elif topic == TOPIC_LED_GREEN:
        change_led_state("GREEN", payload in ["1", "ON"])

    # Threshold updates from Telegram
    elif topic == TOPIC_THRESH_TEMP:
        try:
            raw_val = msg.payload.decode().strip()
            TEMP_THRESHOLD = float(raw_val)
            window.after(0, _update_temp_thresh_gui, TEMP_THRESHOLD)
            flash_led("RED", times=3)
            print(f"[Threshold] Temp → {TEMP_THRESHOLD}°C  (RED LED flashed)")
        except:
            pass

    elif topic == TOPIC_THRESH_LIGHT:
        try:
            raw_val = msg.payload.decode().strip()
            INTENSITY_THRESHOLD = int(raw_val)
            window.after(0, _update_light_thresh_gui, INTENSITY_THRESHOLD)
            flash_led("YELLOW", times=3)
            print(f"[Threshold] Light → {INTENSITY_THRESHOLD}  (YELLOW LED flashed)")
        except:
            pass

    # Data request: Telegram asked for latest readings
    elif topic == TOPIC_DATA_REQUEST:
        print("[MQTT] Data request — re-publishing latest readings")
        safe_publish(TOPIC_TEMP,      latest["temp"])
        safe_publish(TOPIC_HUMI,      latest["humi"])
        safe_publish(TOPIC_INTENSITY, latest["intensity"])
        safe_publish(TOPIC_MOTION,    latest["motion"])

def _update_temp_thresh_gui(val):
    ent_temp_thresh.delete(0, END)
    ent_temp_thresh.insert(0, str(val))
    lbl_settings_msg.configure(
        text=f"✓ Temp threshold updated to {val}°C via Telegram  (RED LED flashed)",
        fg=WARNING)

def _update_light_thresh_gui(val):
    ent_int_thresh.delete(0, END)
    ent_int_thresh.insert(0, str(val))
    lbl_settings_msg.configure(
        text=f"✓ Light threshold updated to {val} via Telegram  (YELLOW LED flashed)",
        fg=WARNING)

mqtt_client = mqtt.Client()
mqtt_client.on_connect    = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message    = on_message

def mqtt_thread_fn():
    while True:
        try:
            mqtt_client.connect(BROKER, PORT)
            mqtt_client.loop_forever()
        except:
            time.sleep(5)

##############################################################
# MAIN WINDOW
##############################################################
window = Tk()
window.title("— S.M.A.R.T Home System —")
window.configure(bg=BG_DARK)

win_w, win_h = 880, 650
screen_w = window.winfo_screenwidth()
screen_h = window.winfo_screenheight()
window.geometry(f"{win_w}x{win_h}+{(screen_w-win_w)//2}+{(screen_h-win_h)//2}")

FONT_TITLE   = ("Courier New", 18, "bold")
FONT_HEADING = ("Courier New", 13, "bold")
FONT_BODY    = ("Courier New", 11)
FONT_SMALL   = ("Courier New", 9)
FONT_BIG     = ("Courier New", 22, "bold")
FONT_STATUS  = ("Courier New", 13, "bold")

def close_confirm():
    res = messagebox.askyesnocancel("Exit", "Do you want to exit?")
    if res is True:
        GPIO.output(LED_RED,    0)
        GPIO.output(LED_YELLOW, 0)
        GPIO.output(LED_GREEN,  0)
        GPIO.cleanup()
        window.destroy()

window.protocol("WM_DELETE_WINDOW", close_confirm)

##############################################################
# LAYOUT
##############################################################
menu_frame = Frame(window, bg=BG_MENU, width=160)
menu_frame.place(x=0, y=0, width=160, relheight=1)
Frame(window, bg=ACCENT, width=2).place(x=160, y=0, width=2, relheight=1)
content_frame = Frame(window, bg=BG_PANEL)
content_frame.place(x=162, y=0, width=698, relheight=1)
lbl_time = Label(window, font=("Courier New", 11),
                 fg=TEXT_PRIMARY, bg=BG_DARK)
lbl_time.place(x=700, y=10)

page_welcome  = Frame(content_frame, bg=BG_PANEL)
page_dht      = Frame(content_frame, bg=BG_PANEL)
page_ldr      = Frame(content_frame, bg=BG_PANEL)
page_mpu      = Frame(content_frame, bg=BG_PANEL)
page_led      = Frame(content_frame, bg=BG_PANEL)
page_settings = Frame(content_frame, bg=BG_PANEL)

for f in (page_welcome, page_dht, page_ldr, page_mpu, page_led, page_settings):
    f.place(relx=0, rely=0, relwidth=1, relheight=1)

def stop_all_sensors():
    global dht_active, ldr_active, mpu_active
    global dht_job, ldr_job
    if dht_active:
        dht_active = False
        if dht_job:
            window.after_cancel(dht_job)
    if ldr_active:
        ldr_active = False
        if ldr_job:
            window.after_cancel(ldr_job)
    mpu_active = False

def show_page(p):
    stop_all_sensors()
    p.tkraise()

##############################################################
# WIDGET HELPERS
##############################################################
def page_title(parent, text):
    bar = Frame(parent, bg=ACCENT2, height=48)
    bar.place(relx=0, rely=0, relwidth=1, height=48)
    Label(bar, text=text, font=FONT_TITLE, fg=TEXT_PRIMARY,
          bg=ACCENT2, pady=10).place(x=20, y=0)

def mk_btn(parent, text, cmd, color=BTN_DEFAULT, fg=TEXT_PRIMARY, w=16, h=1):
    return Button(parent, text=text, command=cmd,
                  font=FONT_BODY, fg=fg, bg=color,
                  activebackground=BTN_HOVER, activeforeground=fg,
                  relief=FLAT, cursor="hand2",
                  width=w, height=h, padx=4, pady=4)

def mk_section(parent, label, y):
    Label(parent, text=f"── {label} ──────────────",
          font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_PANEL).place(x=20, y=y)

def styled_log(parent, w, h):
    return scrolledtext.ScrolledText(parent, width=w, height=h,
                                     font=FONT_BODY,
                                     bg="#0d1117", fg=SUCCESS,
                                     insertbackground=ACCENT,
                                     relief=FLAT, bd=0)

def value_box(parent, x, y, w=260, h=80):
    Frame(parent, bg=BTN_DEFAULT, width=w, height=h).place(x=x, y=y)

def update_time():
    current = time.strftime("%Y-%m-%d %H:%M:%S")
    lbl_time.config(text=current)
    window.after(1000, update_time)

##############################################################
# MENU
##############################################################
menu_visible    = False
logo_bar        = Frame(menu_frame, bg=ACCENT2, height=48, cursor="hand2")
logo_bar.pack(fill=X)
btn_group_frame = Frame(menu_frame, bg=BG_MENU)

def stk_clicked():
    global menu_visible
    if menu_visible:
        btn_group_frame.pack_forget()
        menu_visible = False
    else:
        btn_group_frame.pack(fill=X, padx=4, pady=2)
        menu_visible = True

lbl_menu = Label(logo_bar, text="☰  MENU", font=("Courier New", 12, "bold"),
                 fg=TEXT_PRIMARY, bg=ACCENT2, pady=14, cursor="hand2")
lbl_menu.pack(side=LEFT, padx=12)
lbl_menu.bind("<Button-1>", lambda e: stk_clicked())
logo_bar.bind("<Button-1>",  lambda e: stk_clicked())
Frame(menu_frame, bg=BORDER, height=1).pack(fill=X, padx=8, pady=4)

def nav_btn(label, icon, page):
    Button(btn_group_frame,
           text=f"{icon}  {label}",
           font=FONT_BODY, fg=TEXT_PRIMARY, bg=BTN_DEFAULT,
           activebackground=ACCENT2, activeforeground=TEXT_PRIMARY,
           relief=FLAT, anchor=W, cursor="hand2",
           padx=12, pady=8,
           command=lambda: show_page(page)).pack(fill=X, pady=2)

nav_btn("DHT22",    "◎", page_dht)
nav_btn("LDR",      "◈", page_ldr)
nav_btn("MPU6050",  "◆", page_mpu)
nav_btn("LEDs",     "◉", page_led)
nav_btn("Settings", "⚙", page_settings)

lbl_mqtt_sidebar = Label(menu_frame, text="● MQTT OFF",
                         font=FONT_SMALL, fg=DANGER, bg=BG_MENU)
lbl_mqtt_sidebar.pack(side=BOTTOM, pady=6)

##############################################################
# WELCOME PAGE
##############################################################
Label(page_welcome, text="S.M.A.R.T HOME",
      font=("Courier New", 30, "bold"), fg=ACCENT,
      bg=BG_PANEL).place(relx=0.5, rely=0.25, anchor="center")
Label(page_welcome, text="Sensor & Control Unit",
      font=("Courier New", 14, "bold"), fg=TEXT_PRIMARY,
      bg=BG_PANEL).place(relx=0.5, rely=0.38, anchor="center")
Frame(page_welcome, bg=ACCENT, height=3,
      width=380).place(relx=0.5, rely=0.47, anchor="center")
Label(page_welcome, text="Select a component from the menu ☰ to begin",
      font=FONT_BODY, fg=TEXT_MUTED,
      bg=BG_PANEL).place(relx=0.5, rely=0.57, anchor="center")

show_page(page_welcome)

##############################################################
# DHT22 PAGE
##############################################################
page_title(page_dht, "◎  DHT22 — Temperature & Humidity")
mk_section(page_dht, "Live Readings", 62)

value_box(page_dht, 20,  95, 260, 80)
value_box(page_dht, 300, 95, 260, 80)

Label(page_dht, text="TEMPERATURE", font=FONT_SMALL,
      fg=TEXT_MUTED, bg=BTN_DEFAULT).place(x=30, y=100)
Label(page_dht, text="HUMIDITY", font=FONT_SMALL,
      fg=TEXT_MUTED, bg=BTN_DEFAULT).place(x=310, y=100)

lbl_temp = Label(page_dht, text="-- °C",
                 font=("Courier New", 28, "bold"), fg=DANGER, bg=BTN_DEFAULT)
lbl_temp.place(x=30, y=122)

lbl_humi = Label(page_dht, text="-- %",
                 font=("Courier New", 28, "bold"), fg="#fb923c", bg=BTN_DEFAULT)
lbl_humi.place(x=310, y=122)

lbl_dht_mqtt = Label(page_dht, text="MQTT: --",
                     font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_PANEL)
lbl_dht_mqtt.place(x=20, y=185)

lbl_led_auto_temp = Label(page_dht, text="Red LED auto: OFF",
                          font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_PANEL)
lbl_led_auto_temp.place(x=20, y=205)

dht_job = None

def dht_read_loop():
    global dht_job
    if not dht_active:
        return
    try:
        temp = dhtDevice.temperature
        humi = dhtDevice.humidity
        if temp is not None and humi is not None:
            latest["temp"] = round(temp, 1)
            latest["humi"] = round(humi, 1)
            lbl_temp.configure(text=f"{temp:.1f} °C")
            lbl_humi.configure(text=f"{humi:.1f} %")
            safe_publish(TOPIC_TEMP, round(temp, 1))
            safe_publish(TOPIC_HUMI, round(humi, 1))
            lbl_dht_mqtt.configure(
                text=f"MQTT: Temp={temp:.1f}  Humi={humi:.1f}  sent", fg=SUCCESS)

            # Automation: RED LED — change_led_state now publishes status back
            if temp > TEMP_THRESHOLD:
                change_led_state("RED", True)
                lbl_led_auto_temp.configure(
                    text=f"Red LED auto: ON  (>{TEMP_THRESHOLD}°C)", fg=DANGER)
            else:
                change_led_state("RED", False)
                lbl_led_auto_temp.configure(
                    text=f"Red LED auto: OFF (<={TEMP_THRESHOLD}°C)", fg=SUCCESS)
        else:
            lbl_temp.configure(text="None")
            lbl_humi.configure(text="None")
    except RuntimeError as e:
        lbl_temp.configure(text="Err")
        lbl_humi.configure(text="Err")
        print(f"[DHT22] RuntimeError: {e}")
    dht_job = window.after(10000, dht_read_loop)

def toggle_dht():
    global dht_active, dht_job
    if not dht_active:
        dht_active = True
        dht_btn.configure(text="■  Stop Reading", bg=DANGER, fg="#0f1117")
        dht_read_loop()
    else:
        dht_active = False
        if dht_job:
            window.after_cancel(dht_job)
            dht_job = None
        dht_btn.configure(text="▶  Start Reading", bg=SUCCESS, fg="#0f1117")
        lbl_temp.configure(text="-- °C")
        lbl_humi.configure(text="-- %")
        lbl_dht_mqtt.configure(text="MQTT: --", fg=TEXT_MUTED)
        latest["temp"] = "--"
        latest["humi"] = "--"

dht_btn = mk_btn(page_dht, "▶  Start Reading", toggle_dht,
                 color=SUCCESS, fg="#0f1117", w=18)
dht_btn.place(x=20, y=240)
Label(page_dht, text="Readings sent via MQTT every 10 s",
      font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_PANEL).place(x=20, y=290)

##############################################################
# LDR PAGE
##############################################################
page_title(page_ldr, "◈  LDR + ADS1115 — Light Intensity")
mk_section(page_ldr, "Live Readings", 62)

value_box(page_ldr, 20, 95, 300, 80)
Label(page_ldr, text="INTENSITY (raw)", font=FONT_SMALL,
      fg=TEXT_MUTED, bg=BTN_DEFAULT).place(x=30, y=100)

lbl_intensity = Label(page_ldr, text="--",
                      font=("Courier New", 28, "bold"), fg=ACCENT, bg=BTN_DEFAULT)
lbl_intensity.place(x=30, y=122)

lbl_ldr_mqtt = Label(page_ldr, text="MQTT: --",
                     font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_PANEL)
lbl_ldr_mqtt.place(x=20, y=185)

lbl_led_auto_ldr = Label(page_ldr, text="Yellow LED auto: OFF",
                         font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_PANEL)
lbl_led_auto_ldr.place(x=20, y=205)

ldr_job = None

def ldr_read_loop():
    global ldr_job
    if not ldr_active or ldr_ch is None:
        return
    try:
        raw = ldr_ch.value
        raw = raw / 26260 * 100
        raw = round(raw, 1)
        latest["intensity"] = raw
        lbl_intensity.configure(text=f"{raw} %")
        safe_publish(TOPIC_INTENSITY, raw)
        lbl_ldr_mqtt.configure(text=f"MQTT: Intensity={raw}%  sent", fg=SUCCESS)

        # Automation: YELLOW LED — change_led_state now publishes status back
        if raw > INTENSITY_THRESHOLD:
            change_led_state("YELLOW", True)
            lbl_led_auto_ldr.configure(
                text=f"Yellow LED auto: ON  (>{INTENSITY_THRESHOLD})", fg=DANGER)
        else:
            change_led_state("YELLOW", False)
            lbl_led_auto_ldr.configure(
                text=f"Yellow LED auto: OFF (<={INTENSITY_THRESHOLD})", fg=SUCCESS)
    except Exception as e:
        lbl_intensity.configure(text="Err")
        print(f"[LDR] Error: {e}")
    ldr_job = window.after(2000, ldr_read_loop)

def toggle_ldr():
    global ldr_active, ldr_job
    if not ldr_active:
        ldr_active = True
        ldr_btn.configure(text="■  Stop Reading", bg=DANGER, fg="#0f1117")
        ldr_read_loop()
    else:
        ldr_active = False
        if ldr_job:
            window.after_cancel(ldr_job)
            ldr_job = None
        ldr_btn.configure(text="▶  Start Reading", bg=SUCCESS, fg="#0f1117")
        lbl_intensity.configure(text="--")
        lbl_ldr_mqtt.configure(text="MQTT: --", fg=TEXT_MUTED)
        latest["intensity"] = "--"

ldr_btn = mk_btn(page_ldr, "▶  Start Reading", toggle_ldr,
                 color=SUCCESS, fg="#0f1117", w=18)
ldr_btn.place(x=20, y=240)
Label(page_ldr, text="Readings sent via MQTT every 2 s",
      font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_PANEL).place(x=20, y=290)

##############################################################
# MPU6050 PAGE
##############################################################
page_title(page_mpu, "◆  MPU6050 — Motion Detection")
mk_section(page_mpu, "Accelerometer & Gyroscope", 62)

mpu_active        = True
last_motion_state = 0

lbl_motion_status = Label(page_mpu, text="◌  Idle",
                          font=FONT_STATUS, fg=TEXT_MUTED, bg=BG_PANEL)
lbl_motion_status.place(x=20, y=95)

txt_mpu = styled_log(page_mpu, 52, 10)
txt_mpu.place(x=20, y=295)
Frame(page_mpu, bg=ACCENT, height=1).place(x=20, y=293, width=620)

def read_mpu():
    global last_motion_state
    if not mpu_active or mpu_sensor is None:
        return
    try:
        accel = mpu_sensor.get_accel_data()
        ax, ay, az = accel['x'], accel['y'], accel['z']
        magnitude = (ax**2 + ay**2 + az**2) ** 0.5
        motion_detected = 1 if abs(magnitude - 9.8) > 1.5 else 0

        if motion_detected == 1 and last_motion_state == 0:
            lbl_motion_status.configure(text="● Motion DETECTED!", fg=DANGER)
            txt_mpu.insert(INSERT,
                f"[{time.strftime('%H:%M:%S')}] Motion DETECTED (mag={magnitude:.2f})\n")
            txt_mpu.see(INSERT)
            safe_publish(TOPIC_MOTION, 1)

        elif motion_detected == 0 and last_motion_state == 1:
            lbl_motion_status.configure(text="◎ No Motion", fg=SUCCESS)
            txt_mpu.insert(INSERT,
                f"[{time.strftime('%H:%M:%S')}] Motion stopped\n")
            txt_mpu.see(INSERT)
            safe_publish(TOPIC_MOTION, 0)

        last_motion_state = motion_detected

    except Exception as e:
        txt_mpu.insert(INSERT, f"MPU error: {e}\n")
        txt_mpu.see(INSERT)

    window.after(500, read_mpu)

def toggle_mpu():
    global mpu_active
    if not mpu_active:
        mpu_active = True
        btn_mpu.configure(text="■  Stop Reading", bg=DANGER, fg="#0f1117")
        read_mpu()
    else:
        mpu_active = False
        btn_mpu.configure(text="▶  Start Reading", bg=SUCCESS, fg="#0f1117")
        lbl_motion_status.configure(text="◌  Idle", fg=TEXT_MUTED)

btn_mpu = mk_btn(page_mpu, "▶  Start Reading", toggle_mpu,
                 color=SUCCESS, fg="#0f1117", w=18)
btn_mpu.place(x=20, y=170)

mk_btn(page_mpu, "⌫  Clear Log", lambda: txt_mpu.delete(1.0, END),
       color=BTN_DEFAULT, w=14).place(x=250, y=170)

Label(page_mpu, text=f"Gyro threshold: ±{GYRO_THRESH} deg/s  |  "
                     f"Motion alerts sent to Rpi001 → Telegram + Blynk",
      font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_PANEL).place(x=20, y=225)

##############################################################
# LED STATUS PAGE
##############################################################
page_title(page_led, "◉  LED Status & Control")
mk_section(page_led, "Current LED State", 62)

def led_row(parent, y, color_name, gpio_pin, color_hex):
    Label(parent, text=f"{color_name} LED (GPIO {gpio_pin})",
          font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_PANEL).place(x=20, y=y)
    lbl = Label(parent, text="● OFF", font=FONT_STATUS, fg=DANGER, bg=BG_PANEL)
    lbl.place(x=250, y=y)
    mk_btn(parent, "ON",
           lambda p=gpio_pin, l=lbl, c=color_name: manual_led(p, 1, l, c),
           color=SUCCESS, fg="#0f1117", w=6).place(x=380, y=y-2)
    mk_btn(parent, "OFF",
           lambda p=gpio_pin, l=lbl, c=color_name: manual_led(p, 0, l, c),
           color=DANGER,  fg="#0f1117", w=6).place(x=450, y=y-2)
    return lbl

lbl_red_led    = led_row(page_led, 95,  "RED",    LED_RED,    DANGER)
lbl_yellow_led = led_row(page_led, 145, "YELLOW", LED_YELLOW, WARNING)
lbl_green_led  = led_row(page_led, 195, "GREEN",  LED_GREEN,  SUCCESS)

Label(page_led,
      text="Red → auto (temp)  |  Yellow → auto (intensity)  |  Green → Telegram",
      font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_PANEL).place(x=20, y=255)

def manual_led(pin, state, lbl, name):
    """GUI manual override — also publishes feedback so Telegram stays in sync."""
    GPIO.output(pin, state)
    lbl.configure(text="● ON" if state else "● OFF",
                  fg=SUCCESS if state else DANGER)
    # Publish status feedback so RPi001 LED Status stays accurate
    if pin == LED_RED:
        safe_publish(TOPIC_RED_STATUS,    "1" if state else "0")
    elif pin == LED_YELLOW:
        safe_publish(TOPIC_YELLOW_STATUS, "1" if state else "0")
    elif pin == LED_GREEN:
        safe_publish(TOPIC_GREEN_STATUS,  "1" if state else "0")

def update_led_label(color, state):
    """Called by change_led_state() to sync LED page display."""
    mapping = {"RED": lbl_red_led, "YELLOW": lbl_yellow_led, "GREEN": lbl_green_led}
    lbl = mapping.get(color)
    if lbl:
        lbl.configure(text="● ON"  if state else "● OFF",
                      fg=SUCCESS   if state else DANGER)

##############################################################
# SETTINGS PAGE
##############################################################
page_title(page_settings, "⚙  Settings — Thresholds")
mk_section(page_settings, "Automation Thresholds", 62)

Label(page_settings, text="Temperature threshold (°C):",
      font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_PANEL).place(x=20, y=100)
ent_temp_thresh = Entry(page_settings, font=FONT_BODY, bg=BTN_DEFAULT,
                        fg=TEXT_PRIMARY, insertbackground=ACCENT, relief=FLAT, width=10)
ent_temp_thresh.insert(0, str(TEMP_THRESHOLD))
ent_temp_thresh.place(x=300, y=100)

Label(page_settings, text="Intensity threshold (raw ADS1115):",
      font=FONT_BODY, fg=TEXT_PRIMARY, bg=BG_PANEL).place(x=20, y=145)
ent_int_thresh = Entry(page_settings, font=FONT_BODY, bg=BTN_DEFAULT,
                       fg=TEXT_PRIMARY, insertbackground=ACCENT, relief=FLAT, width=10)
ent_int_thresh.insert(0, str(INTENSITY_THRESHOLD))
ent_int_thresh.place(x=300, y=145)

lbl_settings_msg = Label(page_settings, text="",
                         font=FONT_SMALL, fg=SUCCESS, bg=BG_PANEL)
lbl_settings_msg.place(x=20, y=210)

def apply_thresholds():
    global TEMP_THRESHOLD, INTENSITY_THRESHOLD
    try:
        TEMP_THRESHOLD      = float(ent_temp_thresh.get())
        INTENSITY_THRESHOLD = int(ent_int_thresh.get())
        lbl_settings_msg.configure(
            text=f"✓ Applied: Temp>{TEMP_THRESHOLD}°C  Intensity>{INTENSITY_THRESHOLD}",
            fg=SUCCESS)
        print(f"[Settings] Temp={TEMP_THRESHOLD}  Intensity={INTENSITY_THRESHOLD}")
    except ValueError:
        lbl_settings_msg.configure(
            text="✗ Invalid value — enter numbers only", fg=DANGER)

mk_btn(page_settings, "✓ Apply", apply_thresholds,
       color=SUCCESS, fg="#0f1117", w=12).place(x=20, y=175)

Label(page_settings,
      text="Telegram /settemp → updates threshold + flashes RED LED 3×\n"
           "Telegram /setlight → updates threshold + flashes YELLOW LED 3×",
      font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_PANEL).place(x=20, y=260)

##############################################################
# MQTT STATUS — update sidebar every 2 s
##############################################################
def update_mqtt_indicator():
    if mqtt_connected:
        lbl_mqtt_sidebar.configure(text="● MQTT ON",  fg=SUCCESS)
    else:
        lbl_mqtt_sidebar.configure(text="● MQTT OFF", fg=DANGER)
    window.after(2000, update_mqtt_indicator)

update_mqtt_indicator()

##############################################################
# START MQTT THREAD
##############################################################
threading.Thread(target=mqtt_thread_fn, daemon=True).start()

##############################################################
# AUTO START ALL SENSORS ON BOOT
##############################################################
toggle_dht()
toggle_ldr()
toggle_mpu()

##############################################################
# MAIN LOOP
##############################################################
update_time()
window.mainloop()
