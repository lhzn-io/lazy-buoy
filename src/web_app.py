from flask import Flask, jsonify, render_template, request, send_from_directory
import os
import glob
import json

# Local module imports
from database import HybridStore

app = Flask(__name__, template_folder="../templates")
LOG_DIR = "/home/pi/Projects/lhzn-io/lazy-buoy/logs"
AUDIO_DIR = os.path.join(LOG_DIR, "audio")

# Initialize global Hybrid Store connection
sqlite_path = os.path.join(LOG_DIR, "transcripts.sqlite")
lancedb_dir = os.path.join(LOG_DIR, "lancedb")

db_store = HybridStore(sqlite_path=sqlite_path, lancedb_dir=lancedb_dir)
try:
    db_store.initialize()
except Exception as e:
    print(f"Warning: Failed to initialize global HybridStore vector store: {e}")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def get_data():
    """Retrieve raw telemetry records from active log files."""
    log_files = sorted(glob.glob(os.path.join(LOG_DIR, "sensor_data_*.log")))
    if not log_files:
        return jsonify([])
    
    latest_log = log_files[-1]
    data_points = []
    
    try:
        with open(latest_log, "r") as f:
            # Read all lines, keeping the most recent 120 points (e.g. 30 mins)
            lines = f.readlines()[-120:]
            for line in lines:
                try:
                    data_points.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"Error reading log: {e}")
        
    return jsonify(data_points)


@app.route("/api/transcripts", methods=["GET"])
def get_transcripts():
    """Expose recent VHF radio transcriptions stored in the SQLite ledger."""
    try:
        limit = request.args.get("limit", default=30, type=int)
        channel = request.args.get("channel", default=None, type=str)
        query = request.args.get("query", default=None, type=str)
        recent_transcripts = db_store.get_recent_transcripts(limit=limit, channel_name=channel, query=query)
        return jsonify(recent_transcripts)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/transcripts/histogram", methods=["GET"])
def get_transcripts_histogram():
    """Expose timeseries call counts bucketed by duration."""
    try:
        channel = request.args.get("channel", default=None, type=str)
        duration = request.args.get("duration", default="1d", type=str)
        histogram_data = db_store.get_histogram_data(duration=duration, channel_name=channel)
        return jsonify(histogram_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/metrics", methods=["GET"])
def get_metrics():
    """Retrieve aggregate chatter metrics per channel over a sliding relative hour window."""
    try:
        window_hours = request.args.get("window_hours", default=24, type=int)
        metrics = db_store.get_chatter_metrics(window_hours=window_hours)
        return jsonify(metrics)
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/search", methods=["POST"])
def search_transcripts():
    """Execute real-time semantic vector searches via natural language queries."""
    req_data = request.get_json() or {}
    query = req_data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Empty search query string"}), 400
        
    try:
        limit = req_data.get("limit", default=5, type=int)
        results = db_store.search_semantically(query, limit=limit)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/audio/<filename>", methods=["GET"])
def get_audio_file(filename):
    """Securely stream physical recording segments to browser players."""
    # Prevent directory traversal attacks by securing the basename
    safe_filename = os.path.basename(filename)
    return send_from_directory(AUDIO_DIR, safe_filename)


@app.route("/api/logs/<service>", methods=["GET"])
def get_service_logs(service):
    """Retrieve the last 100 journalctl log lines for a specific systemd service."""
    import subprocess
    
    service_map = {
        "transcriber": "lhzn-vhf-transcriber.service",
        "sensor": "lhzn-sensor.service",
        "web": "lhzn-web.service",
        "ntrip": "lhzn-ntrip.service"
    }
    
    service_name = service_map.get(service)
    if not service_name:
        return jsonify({"error": f"Invalid service identifier: {service}"}), 400
        
    try:
        limit = request.args.get("limit", default=100, type=int)
        cmd = ["journalctl", "-u", service_name, "-n", str(limit), "--no-pager"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return jsonify({
            "service": service,
            "service_name": service_name,
            "logs": result.stdout.splitlines()
        })
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Failed to execute journalctl: {e.stderr}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tuner", methods=["GET", "POST"])
def get_tuner_info():
    """Retrieve or update dynamic tuner settings and active frequencies."""
    config_path = os.path.join(os.path.dirname(__file__), "../configs/vhf_transcriber.json")
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except Exception as e:
            print(f"Error loading config in web app: {e}")

    if request.method == "POST":
        req_data = request.get_json() or {}
        
        squelch = req_data.get("squelch_level")
        gain = req_data.get("gain_db")
        preset = req_data.get("preset")
        center = req_data.get("center_frequency_hz")
        span = req_data.get("span_hz")
        
        updated_config = False
        
        if squelch is not None:
            try:
                val = float(squelch)
                if val < 0.0 or val > 300.0:
                    return jsonify({"error": "Squelch must be between 0.0 and 300.0"}), 400
                db_store.set_setting("squelch_level", str(val))
                if "sdr_settings" in config:
                    config["sdr_settings"]["squelch_level"] = int(val)
                    updated_config = True
            except ValueError:
                return jsonify({"error": "Invalid squelch value"}), 400
                
        if gain is not None:
            try:
                val = float(gain)
                if val < 0.0 or val > 50.0:
                    return jsonify({"error": "Gain must be between 0.0 and 50.0 dB"}), 400
                db_store.set_setting("gain_db", str(val))
                if "sdr_settings" in config:
                    config["sdr_settings"]["gain_db"] = val
                    updated_config = True
            except ValueError:
                return jsonify({"error": "Invalid gain value"}), 400

        if center is not None:
            try:
                val = int(center)
                db_store.set_setting("center_frequency_hz", str(val))
            except ValueError:
                return jsonify({"error": "Invalid center frequency"}), 400

        if span is not None:
            try:
                val = int(span)
                db_store.set_setting("span_hz", str(val))
            except ValueError:
                return jsonify({"error": "Invalid span frequency"}), 400

        if preset is not None:
            if preset == "noaa_wx":
                db_store.set_setting("noaa_enabled", "1")
                db_store.set_setting("emergency_enabled", "0")
                db_store.set_setting("fm_radio_enabled", "0")
                db_store.set_setting("center_frequency_hz", "")
                db_store.set_setting("span_hz", "")
                db_store.set_setting("squelch_level", "0.0")
                db_store.set_setting("gain_db", "40.0")
                if "sdr_settings" in config:
                    config["sdr_settings"]["device_type"] = "rtl-sdr"
                    config["sdr_settings"]["squelch_level"] = 0
                    config["sdr_settings"]["gain_db"] = 40.0
                    updated_config = True
            elif preset == "marine_vhf":
                db_store.set_setting("noaa_enabled", "0")
                db_store.set_setting("emergency_enabled", "0")
                db_store.set_setting("fm_radio_enabled", "0")
                db_store.set_setting("center_frequency_hz", "")
                db_store.set_setting("span_hz", "")
                db_store.set_setting("squelch_level", "25.0")
                db_store.set_setting("gain_db", "40.0")
                if "sdr_settings" in config:
                    config["sdr_settings"]["device_type"] = "rtl-airband"
                    config["sdr_settings"]["squelch_level"] = 25
                    config["sdr_settings"]["gain_db"] = 40.0
                    updated_config = True
            elif preset == "local_emergency":
                db_store.set_setting("noaa_enabled", "0")
                db_store.set_setting("emergency_enabled", "1")
                db_store.set_setting("fm_radio_enabled", "0")
                db_store.set_setting("center_frequency_hz", "")
                db_store.set_setting("span_hz", "")
                db_store.set_setting("squelch_level", "25.0")
                db_store.set_setting("gain_db", "40.0")
                if "sdr_settings" in config:
                    config["sdr_settings"]["device_type"] = "rtl-airband"
                    config["sdr_settings"]["squelch_level"] = 25
                    config["sdr_settings"]["gain_db"] = 40.0
                    updated_config = True
            elif preset == "fm_radio":
                db_store.set_setting("noaa_enabled", "0")
                db_store.set_setting("emergency_enabled", "0")
                db_store.set_setting("fm_radio_enabled", "1")
                db_store.set_setting("center_frequency_hz", "")
                db_store.set_setting("span_hz", "")
                db_store.set_setting("squelch_level", "0.0")
                db_store.set_setting("gain_db", "30.0")
                if "sdr_settings" in config:
                    config["sdr_settings"]["device_type"] = "rtl-sdr"
                    config["sdr_settings"]["squelch_level"] = 0
                    config["sdr_settings"]["gain_db"] = 30.0
                    updated_config = True
            else:
                return jsonify({"error": f"Unknown preset profile: {preset}"}), 400

        if updated_config:
            try:
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                return jsonify({"error": f"Failed to save configuration: {e}"}), 500
                
        import subprocess
        try:
            subprocess.run(["sudo", "systemctl", "restart", "lhzn-vhf-transcriber.service"], check=True)
        except Exception as e:
            print(f"Warning: Failed to restart transcriber service: {e}")
            
        return jsonify({"status": "success"})

    # GET request handler
    noaa_val = db_store.get_setting("noaa_enabled", default="1")
    noaa_enabled = (noaa_val == "1")
    emergency_val = db_store.get_setting("emergency_enabled", default="1")
    emergency_enabled = (emergency_val == "1")
    fm_val = db_store.get_setting("fm_radio_enabled", default="0")
    fm_enabled = (fm_val == "1")

    sdr_settings = config.get("sdr_settings", {})
    device_type = sdr_settings.get("device_type", "rtl-sdr")
    gain = sdr_settings.get("gain_db", 40.0)
    squelch = sdr_settings.get("squelch_level", 25)

    # Check for manual overrides in settings DB
    center_override = db_store.get_setting("center_frequency_hz")
    span_override = db_store.get_setting("span_hz")

    if noaa_enabled:
        center_freq = int(center_override) if center_override else 162550000
        span = int(span_override) if span_override else 250000
        preset = "noaa_wx"
        freqs = [{"frequency_hz": 162550000, "name": "NOAA-WX1"}]
    elif fm_enabled:
        center_freq = int(center_override) if center_override else 98100000
        span = int(span_override) if span_override else 200000
        preset = "fm_radio"
        freqs = [{"frequency_hz": 98100000, "name": "FM-98.1"}]
    else:
        center_freq = int(center_override) if center_override else 156600000
        span = int(span_override) if span_override else 1024000
        preset = "local_emergency" if emergency_enabled else "marine_vhf"
        
        # Build active frequencies list
        active_freqs = []
        for f in config.get("scan_frequencies", []):
            freq = f["frequency_hz"]
            name = f.get("name", "")
            if "NOAA" in name or freq == 162550000:
                continue
            if not emergency_enabled and (freq in [154280000, 155310000, 155550000] or "Emergency" in name or "WCPD" in name):
                continue
            active_freqs.append({"frequency_hz": freq, "name": name})
        freqs = active_freqs

    tuner_data = {
        "device_type": device_type,
        "center_frequency_hz": center_freq,
        "span_hz": span,
        "gain_db": float(gain),
        "squelch_level": float(squelch),
        "noaa_enabled": noaa_enabled,
        "emergency_enabled": emergency_enabled,
        "fm_radio_enabled": fm_enabled,
        "preset": preset,
        "frequencies": freqs
    }

    return jsonify(tuner_data)


@app.route("/api/calibrate_imu", methods=["POST"])
def calibrate_imu():
    """Calibrate the IMU by setting the current pitch, roll, heading as new offsets (new 'flat')."""
    req_data = request.get_json() or {}
    
    if req_data.get("clear"):
        db_store.set_setting("imu_pitch_offset", "0.0")
        db_store.set_setting("imu_roll_offset", "0.0")
        db_store.set_setting("imu_heading_offset", "0.0")
        return jsonify({
            "status": "success",
            "imu_pitch_offset": 0.0,
            "imu_roll_offset": 0.0,
            "imu_heading_offset": 0.0
        })
        
    # Get current raw IMU readings from the active log
    log_files = sorted(glob.glob(os.path.join(LOG_DIR, "sensor_data_*.log")))
    if not log_files:
        return jsonify({"error": "No active telemetry log file found"}), 404
        
    latest_log = log_files[-1]
    last_line = None
    try:
        with open(latest_log, "r") as f:
            lines = f.readlines()
            if lines:
                last_line = lines[-1]
    except Exception as e:
        return jsonify({"error": f"Failed to read telemetry log: {e}"}), 500
        
    if not last_line:
        return jsonify({"error": "Active telemetry log is empty"}), 404
        
    try:
        record = json.loads(last_line)
        bno_data = record.get("sensors", {}).get("bno08x")
        if not bno_data:
            return jsonify({"error": "No BNO08x sensor data found in last record"}), 400
            
        # Extract raw quaternions
        qx = bno_data.get("qx", 0.0)
        qy = bno_data.get("qy", 0.0)
        qz = bno_data.get("qz", 0.0)
        qw = bno_data.get("qw", 0.0)
        
        # Calculate raw Euler angles
        import math
        sinr_cosp = 2.0 * (qw * qx + qy * qz)
        cosr_cosp = 1.0 - 2.0 * (qx * qx + qy * qy)
        raw_roll = math.atan2(sinr_cosp, cosr_cosp) * (180.0 / math.pi)
        
        sinp = 2.0 * (qw * qy - qz * qx)
        if abs(sinp) >= 1.0:
            raw_pitch = math.copysign(math.pi / 2.0, sinp) * (180.0 / math.pi)
        else:
            raw_pitch = math.asin(sinp) * (180.0 / math.pi)
            
        siny_cosp = 2.0 * (qw * qz + qx * qy)
        cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
        raw_heading = math.atan2(siny_cosp, cosy_cosp) * (180.0 / math.pi)
        if raw_heading < 0.0:
            raw_heading += 360.0
            
        # Save raw values to database as offsets
        db_store.set_setting("imu_pitch_offset", f"{raw_pitch:.6f}")
        db_store.set_setting("imu_roll_offset", f"{raw_roll:.6f}")
        db_store.set_setting("imu_heading_offset", f"{raw_heading:.6f}")
        
        return jsonify({
            "status": "success",
            "imu_pitch_offset": raw_pitch,
            "imu_roll_offset": raw_roll,
            "imu_heading_offset": raw_heading
        })
        
    except Exception as e:
        return jsonify({"error": f"Error during calibration: {e}"}), 500


@app.route("/api/settings", methods=["GET", "POST"])
def manage_settings():
    """Retrieve or update system settings dynamically."""
    if request.method == "POST":
        req_data = request.get_json() or {}
        response_data = {"status": "success"}
        
        # 1. Update segment duration if provided
        max_duration = req_data.get("max_segment_duration_seconds")
        if max_duration is not None:
            try:
                val = float(max_duration)
                if val < 5.0 or val > 120.0:
                    return jsonify({"error": "Duration must be between 5.0 and 120.0 seconds"}), 400
                db_store.set_setting("max_segment_duration_seconds", str(val))
                response_data["max_segment_duration_seconds"] = val
            except ValueError:
                return jsonify({"error": "Invalid float value"}), 400
                
        # 2. Update NOAA enabled checkbox if provided
        noaa_enabled = req_data.get("noaa_enabled")
        if noaa_enabled is not None:
            noaa_val = "1" if noaa_enabled else "0"
            db_store.set_setting("noaa_enabled", noaa_val)
            response_data["noaa_enabled"] = noaa_enabled

        # 3. Update Emergency & Police enabled checkbox if provided
        emergency_enabled = req_data.get("emergency_enabled")
        if emergency_enabled is not None:
            emergency_val = "1" if emergency_enabled else "0"
            db_store.set_setting("emergency_enabled", emergency_val)
            response_data["emergency_enabled"] = emergency_enabled

        # 4. Update IMU calibration offsets if provided
        for offset_key in ["imu_pitch_offset", "imu_roll_offset", "imu_heading_offset"]:
            val = req_data.get(offset_key)
            if val is not None:
                try:
                    db_store.set_setting(offset_key, str(float(val)))
                    response_data[offset_key] = float(val)
                except ValueError:
                    return jsonify({"error": f"Invalid offset value for {offset_key}"}), 400
            
        if ("max_segment_duration_seconds" not in response_data and 
            "noaa_enabled" not in response_data and 
            "emergency_enabled" not in response_data and
            "imu_pitch_offset" not in response_data and
            "imu_roll_offset" not in response_data and
            "imu_heading_offset" not in response_data):
            return jsonify({"error": "Missing setting parameters"}), 400
            
        return jsonify(response_data)
    else:
        # GET method
        val = db_store.get_setting("max_segment_duration_seconds", default="10.0")
        noaa = db_store.get_setting("noaa_enabled", default="1")
        emergency = db_store.get_setting("emergency_enabled", default="1")
        pitch_offset = db_store.get_setting("imu_pitch_offset", default="0.0")
        roll_offset = db_store.get_setting("imu_roll_offset", default="0.0")
        heading_offset = db_store.get_setting("imu_heading_offset", default="0.0")
        return jsonify({
            "max_segment_duration_seconds": float(val),
            "noaa_enabled": noaa == "1",
            "emergency_enabled": emergency == "1",
            "imu_pitch_offset": float(pitch_offset),
            "imu_roll_offset": float(roll_offset),
            "imu_heading_offset": float(heading_offset)
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

