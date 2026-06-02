from flask import Flask, jsonify, render_template
import os
import glob
import json

app = Flask(__name__, template_folder="../templates")
LOG_DIR = "/home/pi/Projects/lhzn-io/lazy-buoy/logs"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/data")
def get_data():
    log_files = sorted(glob.glob(os.path.join(LOG_DIR, "sensor_data_*.log")))
    if not log_files:
        return jsonify([])
    
    latest_log = log_files[-1]
    data_points = []
    
    try:
        with open(latest_log, "r") as f:
            # Read all lines, keeping the most recent 120 points (e.g., 30 mins if polling every 15s)
            lines = f.readlines()[-120:]
            for line in lines:
                try:
                    data_points.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"Error reading log: {e}")
        
    return jsonify(data_points)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

