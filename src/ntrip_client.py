import socket
import base64
import serial
import time
import argparse
import logging
import threading

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')


import glob
import os
import json

def get_latest_coords():
    # Read the latest loc from the JSON logs to feed NYSNet without fighting the sensor daemon for the serial port
    log_files = sorted(glob.glob("/home/pi/Projects/lhznbuoy/logs/sensor_data_*.log"))
    if not log_files: 
        return None, None
    try:
        with open(log_files[-1], "r") as f:
            lines = f.readlines()[-20:]
            for line in reversed(lines):
                data = json.loads(line)
                if data.get('sensors', {}).get('gps', {}).get('latitude'):
                    return data['sensors']['gps']['latitude'], data['sensors']['gps']['longitude']
    except:
        pass
    return None, None

def format_gga(lat, lon):
    # Convert decimal degrees to NMEA GGA format (DDMM.MMMMM)
    lat_dir = 'N' if lat >= 0 else 'S'
    lon_dir = 'E' if lon >= 0 else 'W'
    lat_abs = abs(lat)
    lon_abs = abs(lon)
    lat_str = f"{int(lat_abs):02d}{(lat_abs - int(lat_abs)) * 60:07.4f}"
    lon_str = f"{int(lon_abs):03d}{(lon_abs - int(lon_abs)) * 60:07.4f}"
    # Calculate NMEA checksum
    nmea_payload = f"GPGGA,000000.00,{lat_str},{lat_dir},{lon_str},{lon_dir},1,08,1.0,0.0,M,0.0,M,,"
    checksum = 0
    for char in nmea_payload:
        checksum ^= ord(char)
    return f"${nmea_payload}*{checksum:02X}\r\n".encode('ascii')

def read_nmea_and_send(ser, get_socket):
    while True:
        lat, lon = get_latest_coords()
        if lat and lon:
            gga = format_gga(lat, lon)
            s = get_socket()
            if s and s.fileno() != -1:
                try:
                    s.sendall(gga)
                except:
                    pass
        time.sleep(10)


def run_ntrip(caster, port, mountpoint, user, password, serial_port, baudrate):
    try:
        ser = serial.Serial(serial_port, baudrate, timeout=1.0)
        logging.info(f"Opened {serial_port} at {baudrate} baud to inject RTCM3.")
    except Exception as e:
        logging.error(f"Failed to open serial port {serial_port}: {e}")
        return

    auth_str = f"{user}:{password}"
    auth_b64 = base64.b64encode(auth_str.encode('ascii')).decode('ascii')
    
    request = (
        f"GET /{mountpoint} HTTP/1.0\r\n"
        f"User-Agent: NTRIP PythonClient/1.0\r\n"
        f"Authorization: Basic {auth_b64}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )

    s = None

    # Start the NMEA reading thread
    nmea_thread = threading.Thread(target=read_nmea_and_send, args=(ser, lambda: s), daemon=True)
    nmea_thread.start()

    while True:
        try:
            logging.info(f"Connecting to NTRIP caster at {caster}:{port}/{mountpoint}...")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10.0)
            s.connect((caster, port))
            
            s.sendall(request.encode('ascii'))
            
            resp = s.recv(1024)
            if b"ICY 200 OK" not in resp and b"HTTP/1.1 200 OK" not in resp and b"HTTP/1.0 200 OK" not in resp:
                logging.error(f"NTRIP connection failed. Server responded: {resp.decode('ascii', errors='replace').strip()}")
                time.sleep(10)
                continue
                
            logging.info("Connected successfully. Streaming RTCM3 data to GPS...")
            
            s.settimeout(None)
            bytes_written = 0
            last_log = time.time()
            
            while True:
                data = s.recv(4096)
                if not data:
                    logging.warning("NTRIP server closed the connection.")
                    break
                    
                ser.write(data)
                ser.flush()
                
                bytes_written += len(data)
                
                if time.time() - last_log > 30:
                    logging.info(f"Streaming Active: Injected {bytes_written} bytes of RTCM3 to {serial_port}.")
                    last_log = time.time()
                    
        except Exception as e:
            logging.error(f"Network or serial error: {e}")
            time.sleep(5)
        finally:
            if s:
                try:
                    s.close()
                except:
                    pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NTRIP Client for RTK GPS")
    parser.add_argument('--caster', type=str, required=True, help="NTRIP Caster Hostname/IP (e.g., rtk.nysnet.org)")
    parser.add_argument('--port', type=int, default=2101, help="NTRIP Port (default: 2101)")
    parser.add_argument('--mountpoint', type=str, required=True, help="Mountpoint (e.g., NYRY_RTCM3)")
    parser.add_argument('--user', type=str, required=True, help="NTRIP Username")
    parser.add_argument('--password', type=str, required=True, help="NTRIP Password")
    parser.add_argument('--serial', type=str, default='/dev/ttyACM0', help="GPS Serial Port (default: /dev/ttyACM0)")
    parser.add_argument('--baud', type=int, default=9600, help="Serial Baudrate (default: 9600)")
    
    args = parser.parse_args()
    run_ntrip(args.caster, args.port, args.mountpoint, args.user, args.password, args.serial, args.baud)
