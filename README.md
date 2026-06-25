# S.M.A.R.T. Home System

A decentralized, dual-node **Smart Home Automation and Monitoring System** built on Raspberry Pi hardware. The system leverages **MQTT** for lightweight inter-device messaging, features a **Tkinter-based GUI dashboard** for local control, and integrates with **Telegram Bot API** and **Blynk IoT** for remote monitoring, alert notifications, and manual overrides.

## 👥 Project Team
* **Member1** (System Node 1 / Integration)
* **Member2** (System Node 2 / Hardware & Local GUI)

---

## 🏗️ System Architecture & Workflow

The system is split into two primary operational nodes interacting seamlessly over an MQTT broker:

### 1. Node 1 (`Rpi001Final.py` - Remote Control & Cloud Bridge)
* **Telegram Integration:** Implements an interactive Telegram bot allowing users to query live temperature/humidity/light data, set automation thresholds (`/settemp`, `/setlight`), and toggle hardware remotely.
* **Blynk Platform Link:** Syncs real-time physical states and sensor parameters to the Blynk IoT app dashboard.
* **MQTT Routing:** Dispatches threshold changes and remote control commands to Node 2, while parsing incoming sensor streams.

### 2. Node 2 (`Rpi002Final.py` - Local Hardware & Dashboard)
* **Sensor Suite:** * **DHT22:** Real-time Ambient Temperature & Humidity tracking.
  * **LDR + ADS1115 (ADC):** High-precision light intensity conversions.
  * **MPU6050:** Motion and vibration detection.
* **Local GUI:** A multi-page Tkinter graphical user interface showcasing system metrics, MQTT connectivity indicator, and historical data logs.
* **Edge Automation:** Automatically triggers physical indicators based on thresholds:
  * **🔴 Red LED:** Automated threshold alarm for high temperatures.
  * **🟡 Yellow LED:** Automated threshold alarm for light intensity limits.
  * **🟢 Green LED:** Manually toggleable from the Telegram Bot interface.

---

## 🛠️ Hardware Requirements
* Raspberry Pi (Single or Dual setup)
* DHT22 Temperature & Humidity Sensor
* LDR (Light Dependent Resistor)
* ADS1115 16-Bit I2C ADC Module
* MPU6050 Accelerometer/Gyroscope Sensor
* 3x LEDs (Red, Yellow, Green) + matching resistors
* Breadboard & Jumper wires

---

## 💻 Tech Stack & Dependencies

* **Language:** Python 3
* **Protocols:** MQTT (Eclipse Mosquitto Broker)
* **Cloud & APIs:** Python Telegram Bot Framework, BlynkLib, HTTP Requests
* **GUI Framework:** Python Tkinter
* **Hardware Libraries:** `RPi.GPIO`, `board`, `adafruit_dht`, `adafruit_ads1x15`, `mpu6050-raspberrypi`

Install the required Python modules before execution:
```bash
pip install paho-mqtt blynkapi python-telegram-bot adafruit-circuitpython-dht adafruit-circuitpython-ads1x15 mpu6050-raspberrypi
