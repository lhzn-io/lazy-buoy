# lhznbuoy

A multi-sensor data collection and LoRa transmission system for environmental and aircraft (ADS-B) monitoring, designed for edge deployment on a solar-powered Raspberry Pi 5.

## Project Structure

* **`src/`**: Core daemon services and protocols:
  - `sensor_lora_daemon.py`: Main sensor ETL and LoRa broadcaster.
  - `aircraft_lora_daemon.py`: ADS-B decoder and parser.
  - `ntrip_client.py`: RTCM3 correction injector for sub-meter GPS RTK fixes.
  - `web_app.py`: Flask web dashboard and telemetry API.
* **`configs/`**: Systemd unit configurations (`lhzn-sensor.service`, `lhzn-ntrip.service`, `lhzn-web.service`).
* **`scripts/`**: System logs and offline test scripts (`scripts/tests/`).
* **`docs/`**: Technical notebooks and architecture documentation.
* **`legacy/`**: Historic diagnostic/patching scripts preserved for troubleshooting.

---

## Quickstart

### 1. Pre-requisites
Ensure `uv` is installed on your host system:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Setup Virtual Environment
Run `uv sync` to automatically initialize the Python virtual environment and install all dependencies declared in `pyproject.toml`:
```bash
uv sync
```

### 3. Run Daemons Locally
* To run the environmental sensor and power telemetry daemon:
  ```bash
  uv run src/sensor_lora_daemon.py
  ```
* To run the web dashboard:
  ```bash
  uv run src/web_app.py
  ```

---

## Installing dump1090 (Required for ADS-B/aircraft support)

The aircraft tracking pipeline requires the `dump1090` utility built from source:
```bash
sudo apt update
sudo apt install git build-essential pkg-config libusb-1.0-0-dev rtl-sdr libncurses5-dev libhackrf-dev

git clone https://github.com/flightaware/dump1090.git
cd dump1090
make
```

Ensure the resulting `dump1090` executable is referenced correctly in your execution paths.

---

## Systemd Service Installation

The background services can be registered to startup automatically. Standard systemd unit templates are located in the `configs/` folder:
1. Copy the configurations:
   ```bash
   sudo cp configs/lhzn-*.service /etc/systemd/system/
   ```
2. For the web dashboard, create the symlink:
   ```bash
   sudo ln -sf /home/pi/Projects/lhzn-io/lazy-buoy/configs/lhzn-web.service /etc/systemd/system/lhzn-web.service
   ```
3. Reload and restart:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable lhzn-sensor lhzn-ntrip lhzn-web
   sudo systemctl restart lhzn-sensor lhzn-ntrip lhzn-web
   ```

---

## License
MIT

