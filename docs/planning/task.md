# Repair Hailo-8 Environment & Retranscribe Backlog

- `[x]` Phase 1: Environment Repair
  - `[x]` Install `hailort` system packages on the Pi
  - `[x]` Install `python3-hailort` system packages on the Pi
  - `[x]` Symlink `hailo_platform` and shared libraries into the project `.venv`
- `[x]` Phase 2: Verification
  - `[x]` Run `hailortcli scan` to verify hardware is active
  - `[x]` Verify `import hailo_platform` works inside the `.venv`
- `[/]` Phase 3: Retranscribe Backlog
  - `[ ]` Run `scripts/tools/retranscribe_backlog.py` using the repaired environment
  - `[ ]` Verify transcription results for ID 654 match actual Coast Guard audio

## 11. IMU Orientation Level Calibration

- [x] Implement `/api/calibrate_imu` POST calibration endpoint inside `web_app.py`
- [x] Add `imu_pitch_offset`, `imu_roll_offset`, and `imu_heading_offset` keys to dynamic settings in `web_app.py`
- [x] Add Calibrate Flat and Reset buttons to Primary Attitude Euler Angles Grid inside `index.html`
- [x] Update frontend Euler math to subtract offsets dynamically from raw quaternion computations in `index.html`
- [x] Deploy and restart web services, verifying successful UI calibration and offset persistence in SQLite database
