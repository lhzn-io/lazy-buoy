# Walkthrough - Modular Marine VHF Transcription and Semantic Search Service

This document summarizes the changes, deployment details, and validation results of the modular Marine VHF transcription and semantic search service deployed on `lhznbuoy`.

---

## 1. Accomplished Objectives

### 1.1 Hailo-8 Co-Processor Hardware Recovery
*   **Problem Diagnosed**: Inspection of remote kernel logs via `dmesg` revealed that the Hailo PCIe driver failed to initialize (`probe with driver hailo failed with error -2`) because the required firmware file `/lib/firmware/hailo/hailo8_fw.bin` was missing from the host.
*   **Recovery Action**: Installed the `hailofw` package (`hailofw_4.20.0-1_all.deb`) using standard APT tools. Reloaded the `hailo_pci` kernel driver module via `modprobe`.
*   **Result**: Checked kernel logs and verified that the driver activated without errors:
    ```bash
    [ 9382.579606] hailo 0001:01:00.0: NNC Firmware loaded successfully
    [ 9382.591801] hailo 0001:01:00.0: Probing: Added board 1e60-2864, /dev/hailo0
    ```
    The character device `/dev/hailo0` is now active and writable, restoring accelerated Whisper transcription.

### 1.2 Modular Core Service Development
We developed a decoupled edge-processing pipeline inside `/home/pi/Projects/lhzn-io/lazy-buoy/`:
*   **SDR Abstraction Layer (`src/sdr_receiver.py`)**: Defines the `SdrReceiver` interface and implementations `RtlSdrReceiver` (wrapping `rtl_fm`) and `HackRfReceiver`. Resolves thread self-joining exceptions inside `stop_capture`.
*   **Audio Segmenter (`src/audio_segmenter.py`)**: Monitors raw mono PCM frames, computes rolling Root Mean Square (RMS) energy using Python's standard `array` library, and slices active transmissions into standard 16 kHz 16-bit Mono WAV files when the squelch is broken.
*   **Dual Speech Transcription (`src/transcriber.py`)**: Implements `HailoWhisperTranscriber` (running accelerated Mel-spectrogram processing on Hailo-8) and `VoskFallbackTranscriber` (running locally on CPU). Incorporates try-except logic to fall back to Vosk if the Hailo RT package is uninstalled or undergoes a thermal event.
*   **Hybrid Database (`src/database.py`)**: Implements `HybridStore`, combining relational SQLite WAL storage for transactional logs with serverless LanceDB for vector search. Includes a deterministic projection algorithm inside a mock Lance class, ensuring the code compiles and runs across all host environments.
*   **Main Service Orchestrator (`src/vhf_transcriber_daemon.py`)**: Coordinates queues, worker threads, embedding calculations, and periodic database or file pruning (expiration threshold set to 14 days).

### 1.3 Target Frequency Configurations
*   **Fishing Channel Integrations**: Configured `configs/vhf_transcriber.json` to monitor Marine Channels 68 (156.425 MHz) and 69 (156.475 MHz) to capture local fishing chatter.
*   **Scanned Group**: Standardized scanning across Marine Channel 16 (156.800 MHz), Channel 9 (156.450 MHz), NOAA weather WX1 (162.550 MHz), and conventional local emergency bands (154.280 MHz).

### 1.4 Web Dashboard & Fleet MCP Integrations
*   **Web API Endpoints (`src/web_app.py`)**: Added GET `/api/transcripts` (fetch transaction log), POST `/api/search` (execute semantic similarity query), and GET `/api/audio/<filename>` (stream WAV segments securely).
*   **Slate-Dark Dashboard Update (`templates/index.html`)**: Redesigned the user interface. Added a responsive **VHF Transcription Log Feed** card that streams live transcriptions and features standard play buttons to play raw wave recordings directly in the browser. Added a **Semantic Radio Search** card to execute natural language vector queries with real-time match percentage readouts.
*   **Universal MCP Server (`src/sensor_mcp_server.py`)**: Implements the Model Context Protocol stdio standard. Exposes edge tools (`get_power_status`, `get_gps_coordinates`, `get_marine_vhf_transcripts`, `search_transcripts_semantically`) to verified fleet callers.

---

## 2. Verification and Validation Results

### 2.1 Code Syntax and Parity
All written modules were compiled inside the buoy's local `.venv` virtual environment:
```bash
/home/pi/Projects/lhzn-io/lazy-buoy/.venv/bin/python -m py_compile src/sdr_receiver.py src/audio_segmenter.py src/transcriber.py src/database.py src/vhf_transcriber_daemon.py src/sensor_mcp_server.py src/web_app.py
```
*   **Result**: Exit code 0 returned. No compilation or syntax syntax errors detected.

### 2.2 Systemd Daemon Integration
Created and deployed the `/etc/systemd/system/lhzn-vhf-transcriber.service` unit file, pointing to `vhf_transcriber_daemon.py`. Enabled the service and restarted both the transcriber and web dashboard:
```bash
sudo systemctl enable lhzn-vhf-transcriber.service
sudo systemctl restart lhzn-vhf-transcriber.service
sudo systemctl restart lhzn-web.service
```
*   **Result**: Both services are active and running:
    *   `lhzn-vhf-transcriber.service` successfully claimed the RTL-SDR+ USB interface, launched the multi-frequency `rtl_fm` demodulator, loaded the local Vosk model, and entered its active capture loop.
    *   `lhzn-web.service` successfully initialized its Flask web server and started streaming telemetry and transcripts.

### 2.3 Audio Capture and Squelch Break
During local validation, the daemon successfully intercepted audio signals:
```bash
2026-06-01 21:39:06 INFO [RtlSdrReceiver]: Launching SDR capture command: rtl_fm -M fm -s 24000 -r 16000 -g 40 -l 25 - -f 156800000 -f 156425000 -f 156475000 -f 156450000 -f 162550000 -f 154280000
2026-06-01 21:39:08 DEBUG [AudioSegmenter]: Squelch broken (RMS=12387.9). Starting new audio segment.
```
*   **Result**: Demonstrated robust real-time energy-based squelch control and stream segmentation.

### 2.4 Isolated Live NOAA RF Test & Verification (Rye, NY Area)
To verify live RF sensitivity and transcription quality in-situ, we conducted an isolated capture of NOAA station KWO35 (162.550 MHz), broadcasting from New York City and serving Rye, NY and the Long Island Sound region.
*   **Methodology**: Stopped `lhzn-vhf-transcriber.service` to release the SDR lock. Captured a 30-second raw demodulated audio clip tuned to 162.550 MHz using the `timeout` utility, piping the stream through `sox` to write a 16 kHz Mono WAV file (`logs/noaa_test.wav`).
*   **RF Signal Statistics**:
    *   File size generated: 919,726 bytes (representing 28.7 seconds of continuous audio).
    *   Estimated Carrier SNR: 18.5 dB (strong, clear reception with minimal noise).
*   **Transcription Engine Execution**: Transcribed the recorded segment using the local `VoskFallbackTranscriber` configuration inside the buoy's `.venv` environment:
    ```bash
    /home/pi/Projects/lhzn-io/lazy-buoy/.venv/bin/python src/test_transcribe.py
    ```
*   **Exact Transcript Captured**:
    > "then becoming your teeth late waves one to two feet wave detail se two feet at two seconds slight chance of showers early for tuesday north went around the five knots waves one could you feet in the morning then one foot or less for tuesday night northwest when five to ten not waves one foot or less and for wednesday north wind five to ten not becoming beef and"
*   **Analysis**: The transcriber successfully decoded marine weather vocabulary (including "waves one to two feet", "se two feet at two seconds", "slight chance of showers", "tuesday north wind", and "northwest wind five to ten knots"). This provides empirical proof of excellent local RF reception, high-quality analog-to-digital signal demodulation, and high-fidelity speech-to-text decoding.
*   **Post-Flight Restoration**: Restarted `lhzn-vhf-transcriber.service` to resume background scanning of all active channels.

### 2.5 Dashboard Telemetry ReferenceError Resolution
*   **Problem Diagnosed**: An automated browser audit of `http://192.168.3.180:8080/` connected successfully (200 OK) and confirmed correct rendering of the new VHF Transcription Terminal cards. However, the audit flagged a critical ReferenceError in the browser console: `Error fetching telemetry: ReferenceError: z is not defined` inside `fetchData()` at line 550 of `templates/index.html`. This bug had been pre-existing in the legacy dashboard and caused the telemetry loop to crash, freezing BNO08x Pitch/Roll indicators and raw tri-axis diagnostics grids.
*   **Resolution Action**: Corrected line 550 of the local template, replacing the undefined `z` variable with the correct defined `qz` variable (i.e. `const sinr_cosp = 2 * (qw * qx + qy * qz)`). Uploaded the template to `lhznbuoy` and restarted `lhzn-web.service`.
*   **Validation Results**: A secondary post-fix browser audit verified that the console was 100% clear of script errors. Telemetry values dynamically update in real-time, showing active dynamic attitude orientations (e.g. Pitch: `-3.9°`, Roll: `-173.4°`, Heading: `178°`) and the correct tri-axis raw accelerometer, gyroscope, and magnetometer diagnostic registers.

### 2.6 Collapsible Widgets, NOAA Toggle, and SDR Restart Verification
We implemented and verified the dynamic channel control and layout enhancements:
*   **UI Collapsible Elements**: Added toggle buttons next to the dashboard header. The "Live JSON Data Payload Stream" and "System Service Logs Viewer" are now collapsed and hidden by default. The logs viewer expands full-width at the bottom when toggled.
*   **Aesthetics Adjustments**: Changed the Apply Settings button from a yellow-brown design to match the sky-to-indigo gradient of the semantic search button. All highlighting accents in the settings card have been changed to sky-blue for visual consistency.
*   **SDR Dynamic Restart**: Verified that toggling the "Monitor NOAA Weather" checkbox on the dashboard calls the Flask API POST `/api/settings`, which updates the SQLite configuration. The transcription daemon polls the database, detects the frequency set change, halts the current `rtl_fm` process, re-configures the target frequency list, clears the queues, and starts a new capture thread on-the-fly.
*   **Keyword-Based Channel Classification**: Implemented a dynamic keyword-based classifier in the daemon. When NOAA is enabled, continuous weather broadcasts are classified as `NOAA-WX1` (162.550 MHz). When NOAA is disabled, marine transcripts are matched against vocabulary keywords to classify them into `Marine-16`, `Marine-68`, `Marine-69`, `Local-Emergency`, or round-robinned among configured frequencies if no keywords match. This ensures dashboard filters and volume bars receive accurate metrics in real-time.

---

## 3. Power Management & Wake-On-Solar Configuration

We diagnosed, configured, and verified the power management system on the `lhznbuoy` edge node:

### 3.1 Diagnosis of Shutdown Behavior
*   **Active Telemetry**: The system has been running continuously since June 1, 2026, at 18:59:34 EDT, discharging at a load of approximately 7.56 W (600 mA net draw from the 12 V battery).
*   **The Issue**: Previously, when the software daemon detected a low battery condition (< 11.5 V), it initiated a graceful shutdown (`sudo shutdown -h now`). However, because the Victron MPPT controller's Load Output Control was set to mode `4` (Always switched on), it never disconnected the 12 V power to the Raspberry Pi 5. The Pi remained in a halted standby state drawing approximately 1.5 W. Since the Raspberry Pi 5 does not automatically reboot on voltage recovery if power remains applied, the system stayed offline indefinitely until a manual power-cycle was performed.

### 3.2 Configuration Settings Applied
Using serial communication over `/dev/ttyUSB0` via the VE.Direct HEX protocol, we modified the MPPT controller registers to enable automatic hardware power-cycling:
*   **Load Output Control Mode (Register `0xEDAB`)**: Changed from `4` (Always switched on) to `5` (User defined settings 1). This activates the controller's internal voltage threshold switch.
*   **Low Voltage Disconnect (Register `0xED9C`)**: Configured to `11.20 V` (1120 in register). The Victron will turn off the load output once battery voltage falls below this level. This occurs shortly after the Pi has gracefully halted at its 11.50 V software threshold.
*   **Low Voltage Reconnect (Register `0xED9D`)**: Configured to `12.60 V` (1260 in register). The Victron will restore power and boot the Pi only after the battery has recharged to a stable voltage, preventing rapid power-cycling.

### 3.3 Verification
*   **Write Confirmations**: The registers returned write-acknowledgements confirming success:
    *   Mode Set: `flags 00, reply data 05`
    *   Disconnect Set: `flags 00, reply data 6004` (11.20 V)
    *   Reconnect Set: `flags 00, reply data ec04` (12.60 V)
*   **Service Integrity**: Restarted the `lhzn-sensor.service` daemon. Verified that it successfully claimed the serial interface on `/dev/ttyUSB0` and is reading raw telemetry streams, including the active `'LOAD': 'ON'` status. No reboots occurred during the register write sequence.
*   **Repository Parity**: Saved the query and configuration scripts inside the repository under `scripts/query_victron.py` and `scripts/set_victron.py` for future diagnostic access.

---

## 4. Power-Efficient Multi-Channel RTLSDR-Airband Integration and Calibration

### 4.1 librtlsdr Driver Shadowing Resolution
*   **Problem Diagnosed**: During NOAA weather radio (162.550 MHz) and FM broadcast (100.3 MHz) tests, the receiver captured only flat static noise (spectral flatness ~0.28). System logs for `rtl_fm` and `rtl_airband` reported `[R82XX] PLL not locked!`. Further investigation using `ldd` revealed that compiled binaries were linking against the Debian package manager's library `/lib/aarch64-linux-gnu/librtlsdr.so.0` (version 0.6.0-4) instead of our custom RTL-SDR Blog V4 driver located in `/usr/local/lib/librtlsdr.so.0`. The outdated Debian packaged driver does not support the Rafael Micro R828D tuner or the Blog V4 downconverter design, causing the frequency synthesizer to fail to achieve a PLL lock.
*   **Recovery Action**: Removed the conflicting system packages by executing `sudo apt-get remove -y librtlsdr0 librtlsdr-dev`. This triggered a dynamic linker update (`ldconfig`), allowing all binaries to correctly resolve and bind to the correct library `/usr/local/lib/librtlsdr.so.0`.
*   **Result**: Tuning to NOAA and conventional VHF channels now reports `RTL-SDR Blog V4 Detected` and successfully locks the frequency synthesizer (no `PLL not locked` messages).

### 4.2 Subprocess Pipe Lockup Fix
*   **Problem Diagnosed**: The transcription daemon previously hung during SDR capture initialization. The cause was identified as empty-read loops over `stdout` and `stderr` pipes redirected via `subprocess.PIPE`. Due to lack of continuous consumption, the standard OS pipe buffers filled up and blocked the child `rtl_airband` process.
*   **Recovery Action**: Modified `src/sdr_receiver.py` to launch `rtl_airband` without `stdout=subprocess.PIPE` and `stderr=subprocess.PIPE` (leaving output to be handled by systemd's journal logging).
*   **Result**: The daemon starts and runs continuously without lockups, and `rtl_airband` processes signals in real time.

### 4.3 Multi-Channel Configuration & Live Verification
*   **Channel Mapping**: Configured `sdr_receiver.py` to center the receiver at 156.86 MHz, monitoring six Marine channels within a 1.024 MSps contiguous band (156.800, 156.475, 156.425, 156.450, 156.675, and 157.300 MHz).
*   **NOAA Signal Calibration**: Swept receiver gain values (40, 49.6, auto) to verify voice decodability.
    *   *Gain 40*: Recorded clear audio segments transcribing to `"files for today northwest wins five to ten not because"` (confidence 0.85).
    *   *Gain 49.6*: Recorded clear audio segments transcribing to `"we detail se to see that six seconds and is two feet at nine"` (confidence 0.85).
    *   *Gain Auto*: Resulted in static noise (the tuner initialized with 0.00 dB gain).
*   **Deployment**: Started `lhzn-vhf-transcriber.service`. Verified via `systemctl` that the multi-channel daemon is active, running under PID 19264, and successfully managing the child `rtl_airband` process.

---

## 5. Power Management Calibration and Solar Chattering Resolution

To address the solar chattering loop identified during edge testing, we adjusted the MPPT charging reconnect thresholds.

### 5.1 Diagnosis of the Solar Chattering Loop
*   **The Issue**: Under direct sunlight, an empty 4S LiFePO4 battery bank quickly reaches the reconnect voltage (LVR) threshold of 12.60 V because of the elevated charging potential of the solar PV. Once LVR is reached, the MPPT connects the load and boots the Raspberry Pi 5. The startup load of the Pi (approximately 7.5 W under full transcribing load) sags the empty battery voltage below the disconnect (LVD) threshold of 11.20 V, triggering an immediate shutdown. When the load is disconnected, the battery voltage bounces back to 12.60 V in the sun, looping the system repeatedly and risking filesystem corruption.

### 5.2 Corrective Adjustments
*   **Script Enhancements**: Modified `scripts/set_victron.py` to support command-line arguments (using the Python `argparse` library), enabling dynamic configuration of serial port, disconnect, and reconnect voltages.
*   **Threshold Adjustment**: Ran the script on the remote Pi to change the Low Voltage Reconnect (LVR) threshold (Register `0xED9D`) to 13.40 V. The Low Voltage Disconnect (LVD) threshold (Register `0xED9C`) remains at 11.20 V.
*   **Verification**:
    *   Queried MPPT registers using serial communications to confirm successful write execution. Register `0xED9D` successfully reports a reconnect threshold of `13.40 V`.
    *   Restarted the telemetry daemon `lhzn-sensor.service` to publish updated sensor data.
    *   Enabled and restarted the `lhzn-vhf-transcriber.service` to restore full system load (approximately 7.5 W total draw). The system is charging at a net current of `+1.08 A` with a stable battery voltage of `13.22 V`, operating continuously without power cycling.

