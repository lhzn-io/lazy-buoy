# lazy-buoy Roadmap

Durable plan for the lazy-buoy sensor node within the AI-assisted marine domain
awareness workstream (`lazy-buoy`, `chatty-buoy` on `skipper`, and `mariner`).
Status as of 2026-10-08.

---

## Phase 0: Foundations (complete)

* Passwordless SSH and host aliases across `lhznbuoy`, `skipper`, and `mariner`;
  fleet names resolve under `.internal`.
* Repository at `lhznbuoy:~/Projects/lhzn-io/lazy-buoy`, managed with `uv`.

## Phase 1: Telemetry and MCP (in service)

* **Telemetry daemon** (`src/sensor_lora_daemon.py`): Victron MPPT over VE.Direct,
  ZED-F9P GNSS, BNO08x IMU, and HTU31D, logged every 15 s and broadcast over LoRa.
  The Victron is never queried more than once per 10 s.
* **RTK** (`src/ntrip_client.py`): RTCM3 from an NTRIP caster; RTK fixed reached on
  2026-10-08.
* **MCP server** (`src/sensor_mcp_server.py`): initial stdio server. Next: confirm the
  tool set (`get_power_status`, `get_environmental_telemetry`, `get_gps_coordinates`)
  and serve it to `skipper` and `mariner`.

## Phase 2: VHF capture and transcription (in service)

* **Capture:** RTLSDR-Airband multichannel at 1.024 MSps, center 156.86 MHz, six
  marine channels (16, 9, 68, 69, 72, 26 ship transmit), 12 dB per-channel SNR squelch.
* **Transcription:** Whisper tiny on the Hailo-8L, queued in SQLite and drained only
  when power allows.
* **Next:**
  * Measure real per-segment SNR (currently a fixed placeholder value).
  * Drop non-speech transcripts (bracketed tags, punctuation-only output) and very
    short segments before they reach the database.
  * Offload audio to `skipper` for a larger Whisper model, keeping the on-device
    transcript as a first pass.
  * Locate and suppress narrowband interference measured in and near the band
    (2026-10-08 sweep), likely from electronics inside the case.

## Phase 3: Traffic awareness

* **AIS vessel tracking** on a second SDR (AIS 1 and 2 at 161.975 and 162.025 MHz
  fall outside the 1.024 MSps VHF capture), with a decoder such as AIS-catcher.
* **ADS-B aircraft tracking** with a 1090 MHz antenna and SDR. The decoder and LoRa
  packet format already exist (`src/aircraft_lora_daemon.py`,
  `src/aircraft_data_packet.py`, dump1090 and pyModeS).
* **Fusion:** when a VHF call is recorded, rank AIS tracks within about 10 nautical
  miles as likely transmitters, and expose the result through an MCP
  `get_traffic_summary` tool.

## Phase 4: Energy autonomy

Measured 2026-10-02 to 10-07: the node draws about 6.8 W (about 160 Wh per day).
The 30 W panel yielded 0 to 110 Wh per day, so the 16 Ah LiFePO4 battery drains
over consecutive days.

* **Implemented:** low-voltage sleep (12.0 V) with clean shutdown and MPPT load cut;
  automatic wake when the MPPT reconnects at 13.2 V under solar charge.
* **Solar:** 100 W panel, with the PV fuse, wiring, and case entry upgraded
  (see `docs/src/hardware.md`). Raise the reconnect level toward 13.4 V afterwards.
* **Load reduction**, in order of expected savings:
  * Duty-cycle the Hailo transcription workload instead of holding it ready.
  * Lower CPU clocks and disable unused interfaces (HDMI, Bluetooth).
  * Tie the LoRa interval and transmit power (23 dBm today) to battery state.
  * Scale sensor polling with battery state rather than fixed-interval loops.
* **Always-on supervisor:** ESP32-S2 Feather with an RFM95W FeatherWing for telemetry
  and remote restart while the Pi is off (proposal in `docs/src/hardware.md`).
* **Battery care:** periodic full charges so the LiFePO4 cells balance; the
  controller logged no absorption time on most days with the 30 W panel.

## Phase 5: Sensor network

* **LoRaWAN sensor gateway:** let the buoy receive general-purpose LoRaWAN sensors
  deployed nearby (water temperature, tide, weather) and relay their data with its
  own telemetry.
* **Lab receiving station** that can also transmit signed commands back to the buoy.

## Phase 6: Compute migration (long term)

* Move the peripheral stack (SDRs, I2C, GNSS, VE.Direct) to a Jetson AGX Orin or
  Thor for larger on-board models. These draw 15 to 60 W, against 6.8 W today, so
  migration depends on a much larger battery and solar array.
