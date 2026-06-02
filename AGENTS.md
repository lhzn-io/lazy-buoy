# lazy-buoy Operations Guide

Welcome! If you are an autonomous agent operating within this repository, please adhere to the following guidelines and constraints.

## 1. Hardware Context
This codebase runs on a Raspberry Pi-based environment connected to specific physical hardware:
- **I2C Sensors**: HTU31D (Temperature/Humidity), BNO08X (IMU/Orientation).
- **USB/Serial Devices**: GPS modules (e.g., Adafruit Ultimate GPS or SparkFun RTK GPS), Victron MPPT charge controller (via VE.Direct).
- **SPI Devices**: Adafruit RFM9x LoRa radio.
- **SDR Hardware**: RTL-SDR dongle via USB.

**Note**: Be mindful of physical pin mappings (`board.SCL`, `board.SDA`, `board.CE1`, `board.D25`, `board.SCK`, `board.MOSI`, `board.MISO`) and device paths (`/dev/ttyUSB*`, `/dev/ttyACM*`). Do not arbitrarily change these without user confirmation.

## 2. Power Management Priorities
The system runs on battery/solar power and is prone to **low-voltage kernel crashes** if drawn too heavily. 
- Always default to power-conscious architectural patterns.
- Minimize CPU cycles when reading from sensors.
- The RTL-SDR (`dump1090`) and LoRa transmitter (`tx_power=23`) are significant power draws. 
- Favor batching data transmissions, deep-sleep modes, and dynamic peripheral polling (e.g., scaling back when voltage is low) over continuous loops.

## 3. Dependency and Environment Management
- **Unified Environment Manager**: The repository relies on `uv` to manage its python runtime, virtual environment (`.venv`), and locked dependencies (`pyproject.toml` and `uv.lock`). Avoid using `conda`, `pip install`, or system python packages.
- **Environment Updates**: To add dependencies, use `/home/pi/.local/bin/uv add <package>` and commit the updated `pyproject.toml` and `uv.lock` files.
- **`dump1090`**: This is a standalone C application that must be compiled from source. It relies on system-level packages (`libusb`, `rtl-sdr`). It is launched via the `aircraft_lora_daemon.py` subprocess.

## 4. Testing and Safety Constraints
- **CRITICAL**: The `src/sensor_lora_daemon.py` includes an emergency shutdown protocol (`os.system('sudo shutdown -h now')`) triggered when the battery voltage reports below the voltage threshold.
- **DO NOT REMOVE OR BREAK THIS SAFETY MEASURE**. It prevents deep discharge of the batteries, which could permanently damage the remote node.
- Ensure that any refactoring of the sensor loop maintains the integrity of the VE.Direct voltage checks.
- When making commits, follow conventional commits (e.g., `feat:`, `fix:`) and stage files explicitly. NO `git add .`. Keep commit messages clean of emojis.

