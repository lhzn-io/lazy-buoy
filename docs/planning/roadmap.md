# Strategic Roadmap - AI-Assisted Marine Domain Awareness

This document outlines the multi-phase strategic roadmap for developing and deploying AI-assisted marine domain awareness capabilities across the `lazy-buoy` and `chatty-buoy` platforms.

---

## 1. Phase 0: Foundations and Embedded Connectivity

Establish stable telemetry pipelines and configure remote orchestration boundaries.

### 1.1 Secure Node Federation
* **Objectives**: Enable passwordless SSH keys and aliases across all development environments (`lhznbuoy`, `skipper`, and `mariner`).
* **Parity**: Setup standard `.agents/configs/` metadata registers inside the `mariner-agent-lab` control folder.
* **Status**: Complete. Passwordless connectivity via `ssh lhznbuoy` and `ssh skipper` is fully operational.

### 1.2 Remote Directory Parity
* **Objectives**: Reorganize remote filesystems to adhere to `lhzn-io` organizational workspace rules.
* **Status**: Complete. Remote buoy repository successfully renamed and harmonized to `/home/pi/Projects/lhzn-io/lazy-buoy`.

---

## 2. Phase 1: Local Telemetry Consolidation and MCP Server Development

Implement robust sensor-polling services and wrap the local hardware stack with an MCP interface.

### 2.1 Environmental and Power Polling Service
* **Objectives**: Implement lightweight Python daemons (`pySerial` and `adafruit-circuitpython`) to poll the Victron charge controller, Sparkfun RTK GPS, ISM330DHCX IMU, and SHT40 temperature/humidity sensor inside the `lazy-buoy` repository structure.
* **Technical Constraints**: Implement a strict 10-second polling duty cycle for the Victron charge controller to prevent serial locking. Use exponential backoffs (up to 3 retries) on I2C reads to handle structural vibration.

### 2.2 Local MCP Server Construction
* **Objectives**: Write an embedded Model Context Protocol (MCP) server running on `lazy-buoy` to expose physical parameters directly to the fleet.
* **Tools exposed**:
  - `get_power_status`: Return battery volts, load current, charging watts, and SOC.
  - `get_environmental_telemetry`: Return temperature, humidity, and 9DOF orientation values.
  - `get_gps_coordinates`: Return latitude, longitude, speed, and NMEA signal status.

---

## 3. Phase 2: Edge VHF Demodulation and Whisper-Hailo Transcription

Activate the RTL-SDR radio stack and accelerate speech-to-text models on the local co-processor.

### 3.1 RTL-SDR Squelch and Audio Capture
* **Objectives**: Setup background `rtl_fm` stream capturing on Marine VHF Channel 16 (156.800 MHz) and other high-traffic local channels.
* **Technical Constraints**: Limit sample rates to 1.024 MSps to manage CPU loads and minimize thermal output within the weatherproof case. Implement an automated software squelch to record audio segments only when signal energy spikes.

### 3.2 Hailo-8 Accelerated Transcription
* **Objectives**: Load quantized Whisper-Tiny or Whisper-Base models onto the Hailo-8 M.2 co-processor using the HailoRT driver.
* **Pipeline**: Automatically feed squelched VHF audio clips into the Whisper pipeline. Store transcribed plain-text, time-stamps, channel identifiers, and estimated Signal-to-Noise Ratio (SNR) in `/var/db/lazy-buoy/transcripts.sqlite`.

---

## 4. Phase 3: Spatial Traffic Fusion and VHF Association

Integrate secondary radio signals to map acoustic intelligence to real-world entities.

### 4.1 AIS and ADS-B Stream Consolidation
* **Objectives**: Run localized decoding daemons (`rtl_ais` and `dump1090`) to process ship positions (AIVDM messages) and aircraft trajectories (Mode-S transponders).
* **Storage**: Index vessel/aircraft IDs, headings, speeds, and coordinates in a local traffic database.

### 4.2 Cross-Modal Intelligence Fusion
* **Objectives**: Run a spatial-temporal fusion routine. When a VHF radio transmission is recorded, query nearby AIS tracks within a 10-nautical-mile radius to identify the most likely transmitting vessels based on signal strength, position, and heading.
* **MCP Integration**: Add a `get_traffic_summary` tool to the MCP server, enabling language models on `skipper` or `mariner` to fetch a consolidated picture of local vessels and matching VHF audio transcripts.

---

## 5. Phase 4: System Scaling and Hardware Migration

Address power envelopes and migrate core sensing peripherals to high-performance sovereign nodes.

### 5.1 Power Envelope Assessment
* **Objectives**: Conduct a detailed energy study measuring the total current draw of the Raspberry Pi 5 under full load (Whisper transcribing active on Hailo-8, two RTL-SDR dongles sampling, and LoRa broadcasting).
* **Parity**: Optimize solar-charging profiles and LiPo battery capacities to ensure 24/7 standalone execution during winter low-light periods.

### 5.2 Node Migration (AGX Orin / AGX Thor)
* **Objectives**: Plan the physical and electrical migration of the entire peripheral stack (SDRs, I2C, GPS, and VE.Direct) to a Jetson AGX Orin or Jetson AGX Thor.
* **Rationale**: Upgrading the compute platform enables the execution of larger local language models and more complex real-time audio/visual models on the edge, pending the installation of a higher-capacity battery and solar array.
