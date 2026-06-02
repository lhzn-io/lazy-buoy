# Repository Analysis: lhznbuoy

## 1. Project Goal
Based on the `README.md`, the primary goal of the repository is to operate a multi-sensor data collection and LoRa transmission system designed for environmental and aircraft (ADS-B) monitoring. The system integrates various hardware components including a Victron MPPT charge controller, GPS, BNO08X IMU, HTU31D humidity/temperature sensor, and an RTL-SDR dongle for listening to aircraft ADS-B signals. It transmits compact binary data packets containing telemetry and aircraft data over LoRa.

## 2. Current Architecture
The software is Python-based (Python 3.13) running on what appears to be a Raspberry Pi (inferred from CircuitPython libraries, SPI/I2C connections, and `/home/pi/` directory).

### Key Components:
- **`sensor_lora_daemon.py`**: A continuously running loop that queries environmental sensors (I2C), GPS (UART), and the Victron MPPT charge controller (VE.Direct serial). It aggregates this telemetry into binary packets and transmits them via the RFM9x LoRa radio.
- **`aircraft_lora_daemon.py`**: Interacts with the `dump1090` utility. `dump1090` interfaces with an RTL-SDR dongle. The daemon creates a socket connection to read the raw ADS-B hex messages, processes them with `pyModeS`, and transmits the aircraft positions via LoRa.
- **Hardware Integration**: Heavy reliance on `adafruit-circuitpython` libraries for I2C and SPI peripheral communication, raw serial communication for GPS and VE.Direct, and local socket integration for `dump1090`.

## 3. Power Consumption & System Resources (Initial Assessment)
You noted a concern regarding a low-voltage kernel crash. This indicates the system's power draw periodically or continuously exceeds the supply capability, leading to an undervoltage lockout or battery depletion.

### Potential Culprits for High Power Draw:
1. **RTL-SDR Dongle**: Software Defined Radios are notorious power hogs. Listening to ADS-B continuously on an RTL-SDR can easily draw hundreds of milliamps.
2. **`dump1090` + Heavy Polling**: Running `dump1090` requires constant high-speed processing of the 2MHz RF bandwidth, equating to high, continuous CPU usage which linearly scales with power draw.
3. **Continuous LoRa Transmissions**: The RFM9x is currently configured at `tx_power = 23` (the maximum, ~130mA peak) and `tx_power = 13` in the daemons. Frequent polling and transmitting prevent the system from entering idle or deep-sleep states.
4. **GPS Polling**: If the GPS module is active continuously, it draws a steady ~30-50mA.
5. **No Power Management / Sleep States**: The daemons primarily use simple `time.sleep()` loops and continuously poll peripherals. There are no dynamic adjustments to polling frequencies based on battery state (despite reading battery state from the MPPT controller).

## 4. Elements for `AGENTS.md`
To orient future agents operating in this repository, `AGENTS.md` should include:

- **Hardware Context**: Note that the environment is a Raspberry Pi (or similar SBC) connected to specific physical hardware (SDR, I2C sensors, SPI LoRa, Serial GPS, Victron MPPT). Agents must be mindful of hardware pin mapping and continuous background processes.
- **Power Management Priorities**: Instruct agents to default to power-conscious architectural patterns. E.g., batching transmissions, scaling down CPU usage, turning off peripherals when battery state drops below a threshold.
- **Dependency Nuances**: Acknowledge that `dump1090` is built from source and not a Python package, and `pyModeS` is used for decoding. Note the Conda ecosystem combined with system-level apt packages (like `libusb`).
- **Testing and Safety Constraints**: Warn agents about "bricking" the node remotely. Daemons handle power cut-offs (`os.system('sudo shutdown -h now')` based on VE.Direct voltage); agents must not break this critical safety net during code refactors.