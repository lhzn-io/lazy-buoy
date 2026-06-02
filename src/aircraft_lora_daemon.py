import argparse
import subprocess
import socket
import time
from aircraft_data_packet import AircraftDataPacket
import adafruit_rfm9x
import logging

# --- LoRa Setup (reuse your existing setup code as needed) ---
import board
import busio
import digitalio

spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
cs = digitalio.DigitalInOut(board.CE1)
reset = digitalio.DigitalInOut(board.D25)
rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, 902.5)
rfm9x.tx_power = 13

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# --- RTL-SDR/ADSB Setup ---
# NOTE: This requires the 'pyModeS' Python package and a working RTL-SDR dongle.
# This daemon can optionally start dump1090 if not running.
#
# Example command to start dump1090:
#   dump1090 --net --net-only --quiet --raw --lat <your_lat> --lon <your_lon>
#
# By default, this daemon connects to dump1090 on 127.0.0.1:30002.
# For more info: https://github.com/flightaware/dump1090

def wait_for_port(host, port, timeout=10):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False

def get_adsb_aircraft(scan_seconds=2, host='127.0.0.1', port=30002, debug=0):
    """
    Connect to dump1090 --net (default port 30002) and process incoming hex messages for scan_seconds.
    Returns a list of aircraft dicts as before.
    If debug >= 1, print aircraft as soon as they are decoded.
    If debug >= 2, print every raw message and parsing step.
    """
    import socket
    import time
    from pyModeS import adsb

    aircraft_dict = {}
    msg0 = {}
    msg1 = {}
    t0 = {}
    t1 = {}
    start = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    s.settimeout(1.0)
    buffer = b''
    try:
        while time.time() - start < scan_seconds:
            try:
                data = s.recv(4096)
                if not data:
                    break
                buffer += data
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    msg = line.strip().decode()
                    # Strip leading '*' and trailing ';' if present (dump1090 format)
                    if msg.startswith('*') and msg.endswith(';'):
                        msg = msg[1:-1]
                    if debug >= 2:
                        logging.debug(f"[DEBUG2] Raw message: {msg}")
                    if len(msg) < 28:
                        if debug >= 2:
                            logging.debug(f"[DEBUG2] Skipping short message: {msg}")
                        continue
                    icao = adsb.icao(msg)
                    tc = adsb.typecode(msg)
                    if debug >= 2:
                        logging.debug(f"[DEBUG2] ICAO: {icao}, Typecode: {tc}")
                    if 1 <= tc <= 4:
                        tail = adsb.callsign(msg)
                        # Strip trailing underscores from callsign
                        if tail:
                            tail = tail.rstrip('_')
                        aircraft_dict.setdefault(icao, {})['tail'] = tail or ""
                        if debug >= 2:
                            logging.debug(f"[DEBUG2] Callsign: {tail}")
                    if tc == 19:
                        vel = adsb.velocity(msg)
                        if vel:
                            aircraft_dict.setdefault(icao, {})['speed'] = vel[0]
                            aircraft_dict[icao]['heading'] = vel[1]
                            if debug >= 2:
                                logging.debug(f"[DEBUG2] Velocity: {vel}")
                    if 5 <= tc <= 18:
                        if adsb.oe_flag(msg):
                            msg1[icao] = msg
                            t1[icao] = int(time.time())
                            if debug >= 2:
                                logging.debug(f"[DEBUG2] OE=1, msg1[{icao}] updated")
                        else:
                            msg0[icao] = msg
                            t0[icao] = int(time.time())
                            if debug >= 2:
                                logging.debug(f"[DEBUG2] OE=0, msg0[{icao}] updated")
                        if icao in msg0 and icao in msg1:
                            pos = adsb.position(msg0[icao], msg1[icao], t0[icao], t1[icao])
                            alt = adsb.altitude(msg)
                            if pos:
                                aircraft_dict.setdefault(icao, {})['lat'] = pos[0]
                                aircraft_dict[icao]['lon'] = pos[1]
                                aircraft_dict[icao]['alt'] = alt
                                aircraft_dict[icao]['icao'] = icao  # Keep as hex string
                                aircraft_dict[icao]['ts'] = int(time.time())
                                if debug >= 1:
                                    ac = aircraft_dict[icao]
                                    logging.info(f"[DEBUG1] Aircraft detected: ICAO={ac.get('icao')}, Lat={ac.get('lat')}, Lon={ac.get('lon')}, Alt={ac.get('alt')}, Speed={ac.get('speed')}, Heading={ac.get('heading')}, Tail={ac.get('tail')}")
            except socket.timeout:
                continue
    finally:
        s.close()

    # Fill missing fields with None or 0
    for ac in aircraft_dict.values():
        ac.setdefault('icao', "")
        ac.setdefault('lat', 0.0)
        ac.setdefault('lon', 0.0)
        ac.setdefault('alt', 0.0)
        ac.setdefault('speed', 0.0)
        ac.setdefault('heading', 0.0)
        ac.setdefault('ts', int(time.time()))
        ac.setdefault('tail', "")
    return list(aircraft_dict.values())


def main():
    parser = argparse.ArgumentParser(description="Aircraft LoRa Daemon with dump1090 integration")
    parser.add_argument('--host', default='127.0.0.1', help='dump1090 host (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=30002, help='dump1090 port (default: 30002)')
    parser.add_argument('--lat', type=float, default=None, help='Receiver latitude (for dump1090)')
    parser.add_argument('--lon', type=float, default=None, help='Receiver longitude (for dump1090)')
    parser.add_argument('--start-dump1090', action='store_true', help='Start dump1090 if not running')
    parser.add_argument('--no-start-dump1090', action='store_true', help='Do NOT start dump1090 automatically (default: start if not running)')
    parser.add_argument('--dump1090-path', default='./dump1090/dump1090', help='Path to dump1090 executable (default: ./dump1090/dump1090)')
    parser.add_argument('--scan-seconds', type=int, default=15, help='Seconds to scan for aircraft per cycle (default: 2)')
    parser.add_argument('--debug', type=int, default=0, help='Debug level: 0=off, 1=aircraft, 2=raw messages and parsing')
    args = parser.parse_args()

    # Optionally start dump1090 if not running
    if not args.no_start_dump1090:
        if not wait_for_port(args.host, args.port, timeout=5):
            if args.lat is None or args.lon is None:
                logging.error("Latitude and longitude must be specified to start dump1090!")
                exit(1)
            logging.info(f"Starting dump1090 on {args.host}:{args.port}...")
            dump1090_cmd = [
                args.dump1090_path,
                '--net', '--quiet', '--raw',
                '--lat', str(args.lat), '--lon', str(args.lon)
            ]
            subprocess.Popen(dump1090_cmd)
            # Wait for port to be open
            if not wait_for_port(args.host, args.port, timeout=10):
                logging.error("Failed to start dump1090 or port did not open in time.")
                exit(1)
        else:
            logging.info(f"dump1090 already running on {args.host}:{args.port}")
    else:
        if not wait_for_port(args.host, args.port, timeout=5):
            logging.error(f"dump1090 must be running and listening on {args.host}:{args.port}!")
            exit(1)

    while True:
        logging.info("Scanning for aircraft...")
        aircraft_list = get_adsb_aircraft(scan_seconds=args.scan_seconds, host=args.host, port=args.port, debug=args.debug)
        # Convert ICAO from hex string to int for packet encoding, but keep hex for logging
        for ac in aircraft_list:
            if isinstance(ac.get('icao'), str) and ac['icao']:
                try:
                    ac['icao_int'] = int(ac['icao'], 16)
                except Exception:
                    ac['icao_int'] = 0
            else:
                ac['icao_int'] = 0
        # Build packet using ICAO as int, but keep logs as hex
        packet_aircraft_list = []
        for ac in aircraft_list:
            ac_packet = ac.copy()
            ac_packet['icao'] = ac.get('icao_int', 0)
            packet_aircraft_list.append(ac_packet)
        if aircraft_list:
            for ac in aircraft_list:
                logging.info(f"Aircraft detected: ICAO={ac.get('icao')}, Lat={ac.get('lat')}, Lon={ac.get('lon')}, Alt={ac.get('alt')}, Speed={ac.get('speed')}, Heading={ac.get('heading')}, Tail={ac.get('tail')}")
        else:
            logging.info("No aircraft detected.")
        packet = AircraftDataPacket(packet_aircraft_list)
        try:
            rfm9x.send(packet.encode_packet())
            logging.info(f"Sent {len(aircraft_list)} aircraft: {aircraft_list}")
        except Exception as e:
            logging.error(f"LoRa send error: {type(e).__name__} - {e}")
            raise e
        time.sleep(10)

if __name__ == "__main__":
    main()
