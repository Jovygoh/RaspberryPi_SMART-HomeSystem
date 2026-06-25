import paho.mqtt.client as mqtt
import BlynkLib
import threading
import requests
import time
import logging

from telegram import Update,ReplyKeyboardMarkup
from telegram.ext import (ApplicationBuilder, CommandHandler,MessageHandler,ContextTypes,filters)

#######################################################
# CONFIGURATION
#######################################################
BLYNK_AUTH_TOKEN=""

TELEGRAM_TOKEN=""
TELEGRAM_CHAT_ID=""

MQTT_BROKER="10.10.3.150"
MQTT_PORT=1883

TOPIC_TEMP="home/sensor/temperature"
TOPIC_HUMI="home/sensor/humidity"
TOPIC_INTENSITY="home/sensor/intensity"
TOPIC_MOTION="home/sensor/motion"

# Only GREEN LED is controlled from Telegram
# RED LED   → controlled automatically by temperature threshold (on Rpi002)
# YELLOW LED → controlled automatically by light threshold (on Rpi002)
TOPIC_GREEN_LED="home/led/green"

TOPIC_THRESH_TEMP="home/threshold/temp"
TOPIC_THRESH_LIGHT="home/threshold/light"

TOPIC_RED_STATUS    = "home/led/red/status"
TOPIC_YELLOW_STATUS = "home/led/yellow/status"
TOPIC_GREEN_STATUS  = "home/led/green/status"

VPIN_INTENSITY=4
VPIN_TEMP=5
VPIN_HUMI=6
VPIN_MOTION=7

#####################################################
# INITIALIZE DATA
#####################################################
sensor_data={"temperature":"--","humidity":"--","intensity":"--","motion":"--",}

led_state={"red":False,"green":False,"yellow":False,}

led_prev_state = {"red":False,"yellow":False,"green":False,}

#####################################################
# BLYNK SETUP
#####################################################
blynk=BlynkLib.Blynk(BLYNK_AUTH_TOKEN)

def upload_to_blynk():
    try:
        if sensor_data["temperature"]!="--":
            blynk.virtual_write(VPIN_TEMP,sensor_data["temperature"])
        if sensor_data["humidity"]!="--":
            blynk.virtual_write(VPIN_HUMI,sensor_data["humidity"])
        if sensor_data["intensity"]!="--":
            blynk.virtual_write(VPIN_INTENSITY,sensor_data["intensity"])
        if sensor_data["motion"]!="--":
            blynk.virtual_write(VPIN_MOTION,sensor_data["motion"])
    except Exception as e:
        print(f"[Blynk] Upload error:{e}")

def run_blynk():
    while True:
        try:
            blynk.run()
        except Exception as e:
            print(f"[Blynk] Error:{e}")
            time.sleep(2)

##################################################################
# TELEGRAM HELPER
##################################################################
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text})
    except Exception as e:
        print(f"[Telegram] Send error: {e}")

##################################################################
# MQTT SETUP
##################################################################
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[MQTT] Connected to broker")
        # Sensor data from RPi002
        client.subscribe(TOPIC_TEMP)
        client.subscribe(TOPIC_HUMI)
        client.subscribe(TOPIC_INTENSITY)
        client.subscribe(TOPIC_MOTION)
        # FIX: Subscribe to physical LED status feedback from RPi002
        client.subscribe(TOPIC_RED_STATUS)
        client.subscribe(TOPIC_YELLOW_STATUS)
        client.subscribe(TOPIC_GREEN_STATUS)
    else:
        print(f"[MQTT] Connection failed, code: {rc}")

def on_message(client, userdata, msg):
    topic = msg.topic
    value = msg.payload.decode().strip()

    if topic == TOPIC_TEMP:
        sensor_data["temperature"] = value
        upload_to_blynk()
        print(f"[MQTT] Temperature: {value} °C")

    elif topic == TOPIC_HUMI:
        sensor_data["humidity"] = value
        upload_to_blynk()
        print(f"[MQTT] Humidity: {value}%")

    elif topic == TOPIC_INTENSITY:
        sensor_data["intensity"] = value
        upload_to_blynk()
        print(f"[MQTT] Intensity: {value}")

    elif topic == TOPIC_MOTION:
        sensor_data["motion"] = value
        print(f"[MQTT] Motion: {value}")
        if value == "1":
            send_telegram_message("⚠️ Motion DETECTED at home!")
            blynk.virtual_write(VPIN_MOTION, 1)
        else:
            blynk.virtual_write(VPIN_MOTION, 0)

    elif topic == TOPIC_RED_STATUS:
        new_state = (value=="1")
        if new_state and not led_state["red"]:
            send_telegram_message(f"TEMPERATURE ALERT!!!\nTemperature exceeded threshold!\nCurrent Temperature: {sensor_data['temperature']} °C\nRED LED has turned on." )
        led_state["red"]      = new_state
        led_prev_state["red"] = new_state

    elif topic == TOPIC_YELLOW_STATUS:
        new_state =(value=="1")
        if new_state and not led_state["yellow"]:
            send_telegram_message(f"INTENSITY ALERT!!!\nIntensity exceeded threshold!\nCurrent Intensity: {sensor_data['intensity']} °C\nYellow LED has turned on.")
        led_state["yellow"]=new_state
        led_prev_state["yellow"] =new_state
        
    elif topic == TOPIC_GREEN_STATUS:
        led_state["green"] = (value == "1")
        print(f"[MQTT] GREEN LED physical: {'ON' if led_state['green'] else 'OFF'}")

mqtt_cl = mqtt.Client()
mqtt_cl.on_connect = on_connect
mqtt_cl.on_message = on_message

def run_mqtt():
    while True:
        try:
            mqtt_cl.connect(MQTT_BROKER, MQTT_PORT)
            mqtt_cl.loop_forever()
        except Exception as e:
            print(f"[MQTT] Error: {e}, retrying...")
            time.sleep(5)
#############################################################
# TELEGRAM KEYBOARD/BUTTON
#############################################################
keyboard=[
["Temperature","Humidity"],
["Intensity","Motion"],
["All Sensors","LED Status"],
["GREEN LED","Settings"],
]

reply_markup=ReplyKeyboardMarkup(keyboard,resize_keyboard=True)

###############################################################
# TELEGRAM COMMAND
###############################################################
async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to S.M.A.R.T Home System!\nChoose an option from the menu below:",reply_markup=reply_markup)
    
async def set_temp_threshold(update:Update,context:ContextTypes.DEFAULT_TYPE):
    try:
        value=float(context.args[0])
        mqtt_cl.publish(TOPIC_THRESH_TEMP,str(value))
        #not affect other function running while waiting for the response
        await update.message.reply_text(f"Temperature threshold set to {value} °C\nSystem will turn red LED on when temperature exceed this threshold.",reply_markup=reply_markup)
        
    except(IndexError,ValueError):
        await update.message.reply_text("Unrecognize command.\nPlease follow this format:\n\n /settemp 35")

async def set_light_threshold(update:Update,context:ContextTypes.DEFAULT_TYPE):
    try:
        value=int(context.args[0])
        mqtt_cl.publish(TOPIC_THRESH_LIGHT,str(value))
        await update.message.reply_text(f"Light threshold set to {value} %\nSystem will turn green LED on when intensity exceeds this.",reply_markup=reply_markup)
        
    except(IndexError,ValueError):
        await update.message.reply_text("Unrecognize command.\nPlease follow this format:\n\n /setlight 500")

#################################################################################
async def handle_buttons(update:Update,context:ContextTypes.DEFAULT_TYPE):
    text=update.message.text
    
    if text=="Temperature":
        await update.message.reply_text(f"Temperature: {sensor_data['temperature']}°C")
        
    elif text=="Humidity":
        await update.message.reply_text(f"Humidity: {sensor_data['humidity']}%")
        
    elif text=="Intensity":
        await update.message.reply_text(f"Light Intensity: {sensor_data['intensity']}%")
        
    elif text=="Motion":
        motion_text="Motion Detected" if sensor_data["motion"]=="1" else "No Motion"
        await update.message.reply_text(f"Motion Status: {motion_text}")
        
    elif text =="All Sensors":
        motion_text="Motion Detected" if sensor_data["motion"]=="1"else"No Motion"
        await update.message.reply_text(f"S.M.A.R.T Home Sensor Readings\n"f"{'-'*30}\n"f"Temperature: {sensor_data['temperature']} °C\n"f"Humidity:{sensor_data['humidity']}%\n"f"Intensity:{sensor_data['intensity']}%\n"f"Motion: {motion_text}")
        
    elif text=="LED Status":
        def state(s):
            return "ON" if s else "OFF"
        await update.message.reply_text(
            f"LED Status\n{'-'*25}\n"
            f"RED    : {state(led_state['red'])}  (auto - temp threshold)\n"
            f"GREEN  : {state(led_state['green'])}  (Telegram control)\n"
            f"YELLOW : {state(led_state['yellow'])}  (auto - light threshold)"
        )
        
    elif text=="GREEN LED":
        led_state["green"]=not led_state["green"]
        if led_state["green"]:
            state="1"
        else:
            state="0"
            
        mqtt_cl.publish(TOPIC_GREEN_LED,state)
        if led_state["green"]:
            status="ON"
        else:
            status="OFF"
        await update.message.reply_text(f"GREEN LED turned {status}")
        
    elif text=="Settings":
        await update.message.reply_text("Threshold Settigs:\nSet temperature threshold\n/settemp 30\n\nSet light intensity threshold\n/setlight 500")

if __name__=="__main__":
    logging.basicConfig(level=logging.WARNING)
    
    #Start MQTT in background thread 
    mqtt_thread=threading.Thread(target=run_mqtt,daemon=True)
    mqtt_thread.start()
    print("[MQTT] Thread started")
    
    #Give MQTT a moment to connect before bot starts
    time.sleep(2)
    
    #Build and start Telegram bot
    app=ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("settemp",set_temp_threshold))
    app.add_handler(CommandHandler("setlight",set_light_threshold))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_buttons))
    
    print("[TELEGRAM] Bot started - send /start to your bot")
    app.run_polling()
