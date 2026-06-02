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
            
        if ("max_segment_duration_seconds" not in response_data and 
            "noaa_enabled" not in response_data and 
            "emergency_enabled" not in response_data):
            return jsonify({"error": "Missing setting parameters"}), 400
            
        return jsonify(response_data)
    else:
        # GET method
        val = db_store.get_setting("max_segment_duration_seconds", default="10.0")
        noaa = db_store.get_setting("noaa_enabled", default="1")
        emergency = db_store.get_setting("emergency_enabled", default="1")
        return jsonify({
            "max_segment_duration_seconds": float(val),
            "noaa_enabled": noaa == "1",
            "emergency_enabled": emergency == "1"
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

