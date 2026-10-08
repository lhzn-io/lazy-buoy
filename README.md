# lazy-buoy

Solar-powered marine sensor node from the Long Horizon Observatory. A Raspberry Pi 5
in a sealed case monitors marine VHF radio, transcribes voice traffic on device,
logs environmental and power telemetry, holds an RTK GNSS position, and broadcasts
compact status packets over LoRa.

## What it does

* **Marine VHF monitoring.** One RTL-SDR dongle runs RTLSDR-Airband in multichannel
  mode, centered at 156.86 MHz at 1.024 MSps, with a 12 dB per-channel SNR squelch.
  Monitored channels: 16 (156.800), 9 (156.450), 68 (156.425), 69 (156.475),
  72 (156.625), and 26 ship transmit (157.300 MHz).
* **On-device transcription.** Recorded segments are queued in SQLite and transcribed
  by Whisper tiny on a Hailo-8L accelerator. The queue drains only when battery and
  solar power allow.
* **Telemetry.** Battery and solar data from a Victron MPPT (VE.Direct), orientation
  from a BNO08x IMU, temperature and humidity from an HTU31D, and GNSS position,
  logged as JSON lines every 15 s and broadcast over LoRa (902.5 MHz).
* **RTK positioning.** A u-blox ZED-F9P receives RTCM3 corrections from an NTRIP
  caster and reaches RTK float or fixed.
* **Solar-aware power management.** On sustained low battery the node shuts down
  cleanly and has the charge controller cut its power; the controller restores power,
  and the Pi boots, once solar charging lifts the battery. See
  [Power management](docs/src/hardware.md#power-management).
* **Web dashboard** on port 8080: live telemetry, IMU attitude, VHF transcripts and
  call histogram, and tuner presets (marine VHF, local emergency, NOAA weather, FM).
* **MCP server** (`src/sensor_mcp_server.py`, stdio) exposing sensor state to agent
  runtimes.

## Hardware

Raspberry Pi 5 with AI HAT+ (Hailo-8L), RTL-SDR Blog V4, u-blox ZED-F9P, RFM9x LoRa,
BNO08x, HTU31D, Victron BlueSolar MPPT 75/15, a 12.8 V 16 Ah LiFePO4 battery, and a
12 V solar panel, in a Nanuk case with SMA bulkheads for VHF, GNSS, and LoRa antennas.

Components, fusing, wiring diagram, and planned upgrades:
[docs/src/hardware.md](docs/src/hardware.md).

![Enclosure wiring](docs/src/images/enclosure_wiring.svg)

## Repository layout

| Path | Contents |
| :--- | :--- |
| `src/sensor_lora_daemon.py` | Sensor polling, JSON logging, LoRa broadcast, low-voltage sleep and wake |
| `src/victron_hex.py` | VE.Direct HEX client for the MPPT load output |
| `src/vhf_transcriber_daemon.py` | Capture loop, transcription queue, power-gated worker |
| `src/sdr_receiver.py` | RTLSDR-Airband config generation and capture (rtl_fm fallback) |
| `src/transcriber.py` | Hailo Whisper and Vosk transcribers |
| `src/database.py` | SQLite transcript store, queue, histogram queries |
| `src/ntrip_client.py` | NTRIP client injecting RTCM3 into the GNSS receiver |
| `src/web_app.py`, `templates/` | Flask dashboard and JSON API |
| `src/sensor_mcp_server.py` | MCP server over stdio |
| `configs/` | systemd units and `vhf_transcriber.json` |
| `patches/` | Local patch for the upstream STT_hailo_whisper checkout |
| `scripts/` | Victron query and setup tools, health logging, bench tools (`tools/`), hardware smoke tests (`tests/`) |
| `tests/` | Offline unit tests (no hardware needed) |
| `docs/src/` | Hardware documentation and diagrams |

## Services

| Unit | Runs | Purpose |
| :--- | :--- | :--- |
| `lhzn-sensor` | `src/sensor_lora_daemon.py` | Telemetry, LoRa, power management |
| `lhzn-vhf-transcriber` | `src/vhf_transcriber_daemon.py` | VHF capture and transcription |
| `lhzn-ntrip` | `src/ntrip_client.py` | RTK corrections |
| `lhzn-web` | `src/web_app.py` | Dashboard on port 8080 |

All four restart automatically (`Restart=always`).

## Installation (Raspberry Pi 5, Raspberry Pi OS Bookworm)

1. **System packages.** RTL-SDR drivers, plus HailoRT 4.20 for the AI HAT+
   (`hailort`, `hailofw`, `python3-hailort` from the Raspberry Pi repositories).
2. **RTLSDR-Airband.** Build from source and install to `/usr/local/bin/rtl_airband`
   (<https://github.com/rtl-airband/RTLSDR-Airband>).
3. **Python environment.** Install [uv](https://docs.astral.sh/uv/), then:
   ```bash
   uv sync --inexact
   ```
   Use `--inexact` on the node. The transcription stack (`torch`, `transformers`,
   `scipy`) and Hailo's `hailo_platform` wheel are installed in `.venv` but not yet
   declared in `pyproject.toml`, and a plain `uv sync` removes them.
4. **Whisper on Hailo.** Clone
   [STT_hailo_whisper](https://github.com/Seeed-Projects/STT_hailo_whisper) into the
   repository root (it is gitignored) and apply the local patch:
   ```bash
   git -C STT_hailo_whisper apply ../patches/stt_hailo_whisper-io-reshape.patch
   ```
5. **NTRIP credentials.** Create `ntrip.env` in the repository root (gitignored) with
   `NTRIP_CASTER`, `NTRIP_PORT`, `NTRIP_MOUNTPOINT`, `NTRIP_USER`, and `NTRIP_PASSWORD`.
6. **Services.**
   ```bash
   sudo cp configs/lhzn-*.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now lhzn-sensor lhzn-vhf-transcriber lhzn-ntrip lhzn-web
   ```

## Configuration

* **Power management** (`configs/lhzn-sensor.service` flags): `-v 12.0` sleep threshold,
  `--low-voltage-count 3`, `--reconnect-voltage 13.2`, `--backstop-voltage 11.6`,
  `--wake-after 3600`. At boot the daemon writes the matching MPPT load-output
  settings (only when they differ).
* **VHF** (`configs/vhf_transcriber.json`): device type, gain, squelch, transcription
  engine (`hailo` with Vosk fallback), audio retention (14 days). Channel frequencies
  and sample rate for RTLSDR-Airband are set in `src/sdr_receiver.py`.

## Operations

```bash
systemctl status lhzn-sensor lhzn-vhf-transcriber lhzn-ntrip lhzn-web
journalctl -u lhzn-sensor -f
```

* Data: `logs/sensor_data_*.log` (JSON lines), `logs/transcripts.sqlite`, `logs/audio/`.
* Shut down (`sudo poweroff`) before switching between battery and bench power; the
  two supplies are wired either-or.

## Tests

```bash
.venv/bin/python tests/test_low_voltage_guard.py
.venv/bin/python tests/test_victron_power.py
```

The scripts in `scripts/tests/` exercise real hardware and must run on the node.

## Operating constraints

* Do not query the Victron controller more often than once every 10 s.
* Keep the RTL-SDR at or below 2.048 MSps; 1.024 MSps is the standard for marine VHF.
* Keep the low-voltage guard in `src/sensor_lora_daemon.py` intact; it protects the
  battery from deep discharge.

Agent-facing development notes are in [AGENTS.md](AGENTS.md).

## Roadmap

Planned capabilities, detailed in [docs/planning/roadmap.md](docs/planning/roadmap.md):

* **AIS vessel tracking** on a second SDR, associating VHF calls with nearby vessels.
* **ADS-B aircraft tracking** with a 1090 MHz antenna; the decoder and LoRa packet
  format are in `src/aircraft_*.py`.
* **LoRaWAN sensor gateway** for general-purpose sensors deployed around the buoy.
* **Always-on power supervisor** (ESP32-S2 Feather with a LoRa FeatherWing) for
  telemetry and remote restart while the Pi is off; see
  [the proposal](docs/src/hardware.md#proposed-esp32-s2-power-supervisor-not-built).
* **100 W solar** for year-round operation.

## License

MIT. See [LICENSE](LICENSE).
