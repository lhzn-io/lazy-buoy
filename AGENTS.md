# lazy-buoy Agent Guide

`lazy-buoy` runs on a solar-powered Raspberry Pi 5 in a sealed Nanuk case: marine VHF capture and on-device transcription, environmental and power telemetry over LoRa, RTK GNSS, and solar-aware power management. It is the sensor node of the `mariner-agent-lab` workstream; `skipper` (chatty-buoy) and `mariner` consume its data. The human-facing overview and install steps are in `README.md`; hardware, fusing, and wiring are in `docs/src/hardware.md`.

Fleet-wide rules (name resolution, pre-flight checks, git execution rules) live in `../uplift-agent-lab/AGENTS.md` and `$HOME/.agents/global-rules.md`. Planning context lives in `mariner-agent-lab/docs/planning/roadmap.md`.

---

## Execution Model

* **Node:** `lhznbuoy` (`ssh lhznbuoy`, resolves to `lhznbuoy.internal` over Wi-Fi; `lhznbuoy-eth` when wired). The working copy at `lhznbuoy:~/Projects/lhzn-io/lazy-buoy` is canonical. The `lazy-buoy/` folder in `mariner-agent-lab` is a stale mirror; do not edit it.
* **Commit and push on the node.** `gh` is authenticated on `lhznbuoy` and pushes over HTTPS.
* **Fetch before committing.** Run `git fetch origin` and check `git rev-list --left-right --count main...origin/main`. On 2026-10-05 the history was rewritten to remove a hard-coded location; if both counts are non-zero with matching subjects, replay local commits onto `origin/main` and never merge the old history.
* **The node is live.** Services run continuously from the working tree. Restart only the service you changed, and only after the user agrees. The user may be in the field with the node on battery power.

---

## Hardware Context

| Item | Detail |
| :--- | :--- |
| Board | Raspberry Pi 5, Raspberry Pi OS Bookworm, Python 3.11; no RTC backup cell (clock is wrong until NTP syncs) |
| Accelerator | AI HAT+ 13 TOPS (Hailo-8L), HailoRT 4.20 |
| VHF | RTL-SDR Blog V4 on the Pi's USB, RTLSDR-Airband multichannel, center 156.86 MHz |
| GNSS | u-blox ZED-F9P at `/dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_GNSS_receiver-if00`; RTCM3 from an NTRIP caster |
| LoRa | RFM9x on SPI (`board.CE1`, reset `board.D25`), 902.5 MHz, 23 dBm |
| I2C | BNO08x IMU and HTU31D on STEMMA QT (`board.SCL`, `board.SDA`) |
| Power | Victron BlueSolar MPPT 75/15 over VE.Direct USB (`/dev/ttyUSB0`); 12.8 V 16 Ah LiFePO4; Pi fed from the MPPT load output through a 12 V to 5 V, 5 A buck converter |

### Electrical and Radio Safety Rules
* Do not query the Victron controller more often than once every 10 s, and use a 1.0 s serial timeout.
* The sensor daemon owns the VE.Direct port. Stop `lhzn-sensor` before running `scripts/query_victron.py` or `scripts/set_victron.py`, and start it again afterwards.
* Keep the RTL-SDR at or below 2.048 MSps; 1.024 MSps is the standard for marine VHF. Use `rtl_sdr -s 2048000` for spectrum checks; `rtl_power` chooses its own rate, up to 2.8 MSps.
* Retry I2C reads with exponential backoff (up to 3 retries) before flagging a sensor failure.
* Shut the Pi down before switching between battery and bench power; the two supplies are wired either-or.
* Do not change pin mappings, device paths, fuses, or load wiring without the user's confirmation.

---

## Power Management Invariants

The low-voltage guard in `src/sensor_lora_daemon.py` protects the battery from deep discharge and is the only path back from a flat battery. Changes to it must keep these properties and pass `tests/`:

* Halt only after `--low-voltage-count` (3) consecutive valid readings below `-v` (12.0 V). Frames without a voltage field, or below 6 V, are ignored.
* Before halting: arm the RTC wake alarm, sync, then raise the MPPT load-disconnect level above the present voltage so the controller removes power.
* At boot, restore the MPPT backstop (disconnect `--backstop-voltage` 11.6 V, reconnect `--reconnect-voltage` 13.2 V). The daemon refuses to start unless backstop < threshold < reconnect.
* MPPT settings are stored in EEPROM: write only values that differ (`VictronHex.ensure`), never in a loop.
* The transcription worker drains its queue only when battery voltage and solar input allow.

---

## Repository Layout

| Path | Purpose |
| :--- | :--- |
| `src/` | Daemons and libraries; see the layout table in `README.md` |
| `configs/` | systemd units (the installed copies in `/etc/systemd/system/` must match) and `vhf_transcriber.json` |
| `patches/` | Local patch applied to the gitignored `STT_hailo_whisper/` checkout |
| `scripts/` | Victron tools, health logging, bench tools (`tools/`), hardware smoke tests (`tests/`, need real hardware) |
| `tests/` | Offline unit tests; no hardware needed |
| `docs/src/` | Hardware documentation and SVG diagrams |
| `docs/planning/roadmap.md` | Durable roadmap; the only planning file tracked here |

---

## Commands

```bash
# Service status and logs
systemctl status lhzn-sensor lhzn-vhf-transcriber lhzn-ntrip lhzn-web
journalctl -u lhzn-sensor -f

# Offline tests (run before committing changes to the sensor daemon or victron_hex)
.venv/bin/python tests/test_low_voltage_guard.py
.venv/bin/python tests/test_victron_power.py

# After editing a unit file in configs/, install it and reload
sudo cp configs/lhzn-sensor.service /etc/systemd/system/ && sudo systemctl daemon-reload
```

---

## Dependencies

* `uv` manages the virtual environment (`/home/pi/.local/bin/uv`). Add packages with `uv add` and commit `pyproject.toml` and `uv.lock`.
* Run `uv sync --inexact` on the node. `torch`, `transformers`, `scipy`, and Hailo's `hailo_platform` wheel are installed in `.venv` but not yet declared, and a plain `uv sync` removes them.
* HailoRT comes from Raspberry Pi apt packages (`hailort`, `hailofw`, `python3-hailort`). RTLSDR-Airband is built from source into `/usr/local/bin/rtl_airband`.
* `STT_hailo_whisper/` is an upstream checkout (Seeed-Projects); local changes go in `patches/`, not in the checkout.

---

## Public Repository Rules

This repository is public.
* Never commit coordinates, place names that locate the node, credentials, or keys. The receiver position comes from the GNSS at runtime; NTRIP credentials live in the gitignored `ntrip.env`.
* Before each commit, scan the whole tracked tree, not only the diff, for coordinate-like numbers and secrets (for example `git grep -nE` for six-decimal numbers and for `key`, `token`, `password` assignments).
* Logs, databases, audio, and model files stay out of git (`.gitignore`).

---

## Style and Git Rules

* No emojis and no em-dashes in documentation, code comments, or commit messages. Keep language quantitative and grounded.
* Follow the Agent Execution Rules in `../uplift-agent-lab/AGENTS.md`: present the full diff and exact commit message before any commit, never push without the user's direct go-ahead, and author every commit as `dfry-lhzn <dfry@lhzn.io>` with no `Co-Authored-By:` trailers.
* Conventional commits, explicit per-file staging (no `git add .`), and atomic changes.
* Ephemeral planning files (`task.md`, `implementation_plan.md`, `walkthrough.md`) are never committed here; `.gitignore` lists them as a backstop. They live in the internal umbrella repository at `mariner-agent-lab/docs/planning/lazy-buoy/`. Only the durable `docs/planning/roadmap.md` is tracked in this repository.
