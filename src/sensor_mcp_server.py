import sys
import json
import os
import logging
import sqlite3
import glob

# Local module imports
from database import HybridStore

# Configure logging to stderr to prevent interference with stdout JSON-RPC messaging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s]: %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger("SensorMcpServer")

LOG_DIR = "/home/pi/Projects/lhzn-io/lazy-buoy/logs"
sqlite_path = os.path.join(LOG_DIR, "transcripts.sqlite")
lancedb_dir = os.path.join(LOG_DIR, "lancedb")

# Initialize global Hybrid Store connection
db_store = HybridStore(sqlite_path=sqlite_path, lancedb_dir=lancedb_dir)
try:
    db_store.initialize()
except Exception as e:
    logger.error(f"Failed to initialize HybridStore database: {e}")


def get_latest_telemetry():
    """Helper to retrieve the latest raw telemetry dictionary from active logs."""
    log_files = sorted(glob.glob(os.path.join(LOG_DIR, "sensor_data_*.log")))
    if not log_files:
        return {}
        
    latest_log = log_files[-1]
    try:
        with open(latest_log, "r") as f:
            lines = f.readlines()
            if lines:
                return json.loads(lines[-1])
    except Exception as e:
        logger.error(f"Failed to read latest telemetry log: {e}")
    return {}


def list_tools():
    """Return the list of exposed MCP tools."""
    return {
        "tools": [
            {
                "name": "get_power_status",
                "description": "Retrieve current battery voltage, net current, solar PV charging watts, and charge state from the Victron MPPT controller.",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "get_gps_coordinates",
                "description": "Retrieve high-accuracy RTK geographical positioning coordinates, speed, track angle, and precision dilutions (HDOP, PDOP).",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "get_marine_vhf_transcripts",
                "description": "Retrieve a list of recent Marine VHF, NOAA, and public safety radio transcriptions stored in the local edge database.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum transcripts to retrieve (default: 10, max: 50).",
                            "default": 10
                        }
                    }
                }
            },
            {
                "name": "search_transcripts_semantically",
                "description": "Perform a real-time semantic vector similarity search over Marine VHF transcripts using natural language queries.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language query terms (e.g. vessel distress or engine issues)."
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum semantic matches to return (default: 5).",
                            "default": 5
                        }
                    },
                    "required": ["query"]
                }
            }
        ]
    }


def call_tool(name, arguments):
    """Execute the matching tool function and return structured JSON contents."""
    try:
        if name == "get_power_status":
            tel = get_latest_telemetry()
            v_dir = tel.get("sensors", {}).get("vedirect", {})
            if not v_dir:
                return {"content": [{"type": "text", "text": "Power telemetry temporarily unavailable."}]}
                
            cs_map = {"0": "Off", "2": "Bulk", "3": "Absorption", "4": "Float", "5": "Storage"}
            state = cs_map.get(v_dir.get("CS", ""), "Unknown")
            
            output = (
                f"Battery Voltage: {float(v_dir.get('V', 0)) / 1000.0:.2f} V\n"
                f"Net Current: {v_dir.get('I', 0)} mA\n"
                f"Solar PV Power: {v_dir.get('PPV', 0)} W\n"
                f"Charger State: {state}\n"
                f"Daily Yield: {int(v_dir.get('H20', 0)) * 10} Wh"
            )
            return {"content": [{"type": "text", "text": output}]}
            
        elif name == "get_gps_coordinates":
            tel = get_latest_telemetry()
            gps = tel.get("sensors", {}).get("gps", {})
            if not gps:
                return {"content": [{"type": "text", "text": "GPS telemetry temporarily unavailable."}]}
                
            output = (
                f"Coordinates: {gps.get('latitude', 0.0):.6f}, {gps.get('longitude', 0.0):.6f}\n"
                f"Altitude: {gps.get('altitude_m', 0.0):.1f} m\n"
                f"Sats in View: {gps.get('satellites', 0)}\n"
                f"Speed: {gps.get('speed_kmh', 0.0):.2f} km/h\n"
                f"Precision: HDOP={gps.get('horizontal_dilution', 1.0):.2f}, PDOP={gps.get('pdop', 1.0):.2f}"
            )
            return {"content": [{"type": "text", "text": output}]}
            
        elif name == "get_marine_vhf_transcripts":
            limit = min(int(arguments.get("limit", 10)), 50)
            records = db_store.get_recent_transcripts(limit=limit)
            
            if not records:
                return {"content": [{"type": "text", "text": "No radio transcripts logged in database."}]}
                
            output_lines = []
            for r in records:
                output_lines.append(
                    f"[{r['timestamp']}] {r['channel_name']} ({(r['frequency_hz'] / 1000000.0):.3f} MHz) | SNR: {r['snr_db']} dB\n"
                    f"Transcript: \"{r['transcript_text']}\"\n"
                    f"------------------------------------------------"
                )
            return {"content": [{"type": "text", "text": "\n".join(output_lines)}]}
            
        elif name == "search_transcripts_semantically":
            query = arguments.get("query", "").strip()
            limit = min(int(arguments.get("limit", 5)), 20)
            
            if not query:
                return {"content": [{"type": "text", "text": "Error: Empty query parameter."}]}
                
            results = db_store.search_semantically(query, limit=limit)
            if not results:
                return {"content": [{"type": "text", "text": "No semantic matches found."}]}
                
            output_lines = []
            for r in results:
                pct = r['similarity_score'] * 100.0
                output_lines.append(
                    f"[{r['timestamp']}] {r['channel_name']} | Semantic Similarity: {pct:.1f}%\n"
                    f"Text: \"{r['transcript_text']}\"\n"
                    f"------------------------------------------------"
                )
            return {"content": [{"type": "text", "text": "\n".join(output_lines)}]}
            
        else:
            return {"error": {"code": -32601, "message": f"Tool not found: {name}"}}
            
    except Exception as e:
        logger.error(f"Error executing tool {name}: {e}")
        return {"error": {"code": -32000, "message": str(e)}}


def main():
    """Main execution loop reading stdin JSON-RPC messages and writing stdout responses."""
    logger.info("Sensor Model Context Protocol (MCP) Server started.")
    
    # Enable unbuffered stdin/stdout binary pipelines
    sys.stdout.reconfigure(encoding="utf-8")
    
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
                
            request = json.loads(line)
            method = request.get("method")
            req_id = request.get("id")
            
            if method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": list_tools()
                }
            elif method == "tools/call":
                params = request.get("params", {})
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                
                tool_result = call_tool(tool_name, arguments)
                if "error" in tool_result:
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": tool_result["error"]
                    }
                else:
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": tool_result
                    }
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not supported: {method}"}
                }
                
            # Write unbuffered JSON-RPC response to stdout
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            
        except json.JSONDecodeError:
            logger.error("Failed to parse incoming line as valid JSON-RPC.")
            continue
        except Exception as e:
            logger.critical(f"Critical MCP execution error: {e}")
            break
            
    logger.info("Sensor Model Context Protocol (MCP) Server stopped.")


if __name__ == "__main__":
    main()
