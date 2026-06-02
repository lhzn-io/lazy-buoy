import multiprocessing
import time
import board
import busio
import digitalio
import serial
import logging
import argparse
import os
import json
import datetime
from logging.handlers import TimedRotatingFileHandler
import glob

from adafruit_bno08x import (
    BNO_REPORT_ACCELEROMETER,
    BNO_REPORT_GYROSCOPE,
    BNO_REPORT_MAGNETOMETER,
    BNO_REPORT_ROTATION_VECTOR,
)
from adafruit_bno08x.i2c import BNO08X_I2C
import adafruit_htu31d
import adafruit_gps
import adafruit_rfm9x

from vedirect.vedirect import Vedirect
from sensor_data_packet import SensorDataPacket

# --- Sensor Setup ---

# I2C for BNO08X and HTU31D
i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
bno08x = BNO08X_I2C(i2c)
bno08x.enable_feature(BNO_REPORT_ACCELEROMETER)
bno08x.enable_feature(BNO_REPORT_GYROSCOPE)
bno08x.enable_feature(BNO_REPORT_MAGNETOMETER)
bno08x.enable_feature(BNO_REPORT_ROTATION_VECTOR)

htu = adafruit_htu31d.HTU31D(i2c)

# UART for GPS (find working device)
def find_gps_device(baudrate=9600, timeout=10, logger=None):
    usb_devices = sorted(glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*'))
    if logger:
        logger.debug(f"Scanning for GPS device among: {usb_devices}")
    for dev in usb_devices:
        try:
            if logger:
                logger.debug(f"Trying GPS on {dev}")
            uart = serial.Serial(dev, baudrate=baudrate, timeout=timeout)
            test_gps = adafruit_gps.GPS(uart, debug=False)
            test_gps.send_command(b"PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")
            test_gps.send_command(b"PMTK220,1000")
            test_gps.update()
            if test_gps.latitude is not None or test_gps.has_fix:
                if logger:
                    logger.info(f"GPS found on {dev}")
                return dev
            else:
                if logger:
                    logger.debug(f"No GPS fix on {dev}")
        except Exception as e:
            if logger:
                logger.debug(f"Failed to init GPS on {dev}: {type(e).__name__} - {e}")
            continue
    if logger:
        logger.error("No GPS device found on /dev/ttyUSB*")
    return None

def get_gps_data(gps):
    if gps is None:
        return dict(fix_quality=0, latitude=None, longitude=None, altitude_m=None, speed_kmh=None, satellites=None)
    
    # We will accumulate the highest fix quality seen in the buffer flush
    best_qual = getattr(gps, 'fix_quality', 0)
    for _ in range(30):
        gps.update()
        if hasattr(gps, 'nmea_sentence') and gps.nmea_sentence and 'GGA' in gps.nmea_sentence:
            try:
                parts = gps.nmea_sentence.split(',')
                if len(parts) > 6 and parts[6].isdigit():
                    best_qual = max(best_qual, int(parts[6]))
            except:
                pass
                
    if gps.latitude is not None:
        return dict(
            fix_quality=best_qual,
            latitude=gps.latitude,
            longitude=gps.longitude,
            altitude_m=gps.altitude_m,
            speed_kmh=gps.speed_kmh,
            satellites=gps.satellites,
        )
    return dict(fix_quality=0, latitude=None, longitude=None, altitude_m=None, speed_kmh=None, satellites=None)

def get_bno08x_data():
    # Get all available sensor data from BNO08X
    quat = bno08x.quaternion
    accel = bno08x.acceleration  # (x, y, z) in m/s^2
    gyro = bno08x.gyro           # (x, y, z) in rad/s
    mag = bno08x.magnetic        # (x, y, z) in uT
    return {
        "qx": quat[0],
        "qy": quat[1],
        "qz": quat[2],
        "qw": quat[3],
        "ax": accel[0],
        "ay": accel[1],
        "az": accel[2],
        "gx": gyro[0],
        "gy": gyro[1],
        "gz": gyro[2],
        "mx": mag[0],
        "my": mag[1],
        "mz": mag[2],
    }

def get_htu31d_data():
    return dict(temperature=htu.temperature, relative_humidity=htu.relative_humidity)

def get_ve_data():
    """
    Victron VE.Direct metrics keys:
    Glossary of VE.Direct Metrics
    Here is a line-by-line breakdown:
    Device Identification
    
    PID: 0xA07D
    Key: Product ID.
    Description: A unique code that identifies the type of Victron product. 0xA07D corresponds to a SmartSolar MPPT 150/45 rev3 charger.
    
    FW: 160
    Key: Firmware Version.
    Description: The version of the software running on the controller. Your version is v1.60.
    
    SER#: HQ2443XCNTW
    Key: Serial Number.
    Description: The unique serial number for this specific device.
    Real-time Electrical Data
    
    V: 13080
    Key: Battery Voltage.
    Description: The voltage of the battery bank connected to the controller.
    Unit: millivolts (mV).
    Interpretation: 13080 mV = 13.08 V.
    
    I: -360
    Key: Battery Current.
    Description: The current flowing into or out of the battery. A positive value means the battery is charging; a negative value means it is discharging.
    Unit: milliamps (mA).
    Interpretation: -360 mA = -0.36 A. Your battery is currently discharging.
    
    VPV: 17210
    Key: Panel Voltage (Voltage Photovoltaic).
    Description: The voltage being produced by the solar panel array.
    Unit: millivolts (mV).
    Interpretation: 17210 mV = 17.21 V. This is a reasonable open-circuit voltage for a "12V" panel system at night.
    
    PPV: 0
    Key: Panel Power (Power Photovoltaic).
    Description: The power currently being generated by the solar panels.
    Unit: Watts (W).
    Interpretation: 0 W. This confirms it's nighttime or there's no usable sunlight.
    Controller Status
    
    CS: 3
    Key: Charger State.
    Description: The current operational state of the charge controller's algorithm.
    Interpretation: 3 = Absorption.
    0: Off
    2: Bulk
    3: Absorption
    4: Float
    5: Storage (if enabled)
    6: Equalization (manual or scheduled)
    
    MPPT: 2
    Key: Tracker Operation Mode.
    Description: Indicates the status of the Maximum Power Point Tracking algorithm.
    Interpretation: 2 = MPPT. The controller is actively using its MPPT algorithm to find the best power point (even if current power is zero).
    0: Off
    1: Voltage or Current limited (not in MPPT mode)
    2: MPPT Active
    
    OR: 0x00000000
    Key: Off Reason.
    Description: A bitmask code explaining why the charger has turned off. It's only non-zero when CS is 0.
    Interpretation: 0x00000000 = No reason / Not off. The charger is operating normally.
    
    ERR: 0
    Key: Error Code.
    Description: Indicates if the controller has an active error condition.
    Interpretation: 0 = No error.
    Example Errors: 2 (Battery voltage too high), 17 (Charger temp too high), 33 (PV input voltage too high).
    Load Output Data
    
    LOAD: ON
    Key: Load Output State.
    Description: The status of the dedicated LOAD terminals on the charge controller.
    Interpretation: The load output is currently enabled.
    
    IL: 300
    Key: Load Current.
    Description: The amount of current being drawn by the devices connected to the LOAD terminals.
    Unit: milliamps (mA).
    Interpretation: 300 mA = 0.30 A. This matches the battery discharge current (-0.36A), with the small difference being the controller's own power consumption.
    Historical Data
    
    H19: 9
    Key: Yield Total (Lifetime).
    Description: The total amount of energy the controller has ever harvested from the solar panels. This value is cumulative and does not reset.
    Unit: 0.01 kWh.
    Interpretation: 9 * 0.01 kWh = 0.09 kWh.
    
    H20: 0
    Key: Yield Today.
    Description: The amount of energy harvested so far today. This resets at midnight.
    Unit: 0.01 kWh.
    Interpretation: 0 * 0.01 kWh = 0.00 kWh.
    
    H21: 2
    Key: Maximum Power Today.
    Description: The highest power output from the solar panels recorded so far today.
    Unit: Watts (W).
    Interpretation: 2 W.
    
    H22: 8
    Key: Yield Yesterday.
    Description: The total energy harvested during the previous day.
    Unit: 0.01 kWh.
    Interpretation: 8 * 0.01 kWh = 0.08 kWh.
    
    H23: 26
    Key: Maximum Power Yesterday.
    Description: The highest power output recorded during the previous day.
    Unit: Watts (W).
    Interpretation: 26 W.
    
    HSDS: 4
    Key: History Day Sequence.
    Description: A counter for the number of days of historical data that have been recorded. It rolls over after a year.
    Interpretation: This is the 4th day of operation since the history was last cleared.
    """
    # Read data from Victron MPPT via Vedirect
    ve = Vedirect(port="/dev/ttyUSB1", timeout=5)
    return ve.read_data_single()

def build_packet(**sensor_dicts):
    """
    Accepts sensor data dicts as keyword arguments, builds objects with the correct class name for each sensor type,
    and encodes them into a SensorDataPacket.
    """
    sensor_objs = []
    for sensor_type, data in sensor_dicts.items():
        if data is None:
            continue  # Skip sensors with no data
        obj = type(sensor_type, (), {})()
        for k in SensorDataPacket.SENSOR_ATTRS.get(sensor_type, []):
            setattr(obj, k, data.get(k))
        sensor_objs.append(obj)
    packet = SensorDataPacket(*sensor_objs)
    return packet

def cleanup_old_logs(log_dir, min_free_percent=5):
    statvfs = os.statvfs(log_dir)
    free_percent = (statvfs.f_bavail * statvfs.f_frsize) / (statvfs.f_blocks * statvfs.f_frsize) * 100
    if free_percent < min_free_percent:
        log_files = sorted([
            f for f in os.listdir(log_dir)
            if f.startswith('sensor_data_') and f.endswith('.log')
        ], key=lambda x: os.path.getmtime(os.path.join(log_dir, x)))
        while free_percent < min_free_percent and log_files:
            oldest = log_files.pop(0)
            os.remove(os.path.join(log_dir, oldest))
            statvfs = os.statvfs(log_dir)
            free_percent = (statvfs.f_bavail * statvfs.f_frsize) / (statvfs.f_blocks * statvfs.f_frsize) * 100



def find_devices(timeout=5, logger=None, find_vedirect=True, find_gps=True, sleep_time=1):
    def try_vedirect_in_process(dev, timeout=5):
        """Try Vedirect detection in a separate process, return True if partial packet with 'V' is present, else False."""
        def target(q):
            try:
                from vedirect.vedirect import Vedirect
                ve = Vedirect(port=dev, timeout=timeout)
                data = ve.read_data_single()
                debug_str = f"VE.Direct data from {dev}, type={type(data)}: {data}"
                # Accept any dict with 'V' key, even if incomplete
                if data and isinstance(data, dict) and 'V' in data:
                    required_keys = {'V', 'PID', 'FW', 'SER#'}
                    missing = required_keys - set(data.keys())
                    if missing:
                        q.put({'result': True, 'partial': True, 'missing': list(missing), 'debug': debug_str})
                    else:
                        q.put({'result': True, 'partial': False, 'debug': debug_str})
                else:
                    q.put({'result': False, 'debug': debug_str})
            except Exception as e:
                q.put({'result': False, 'error': str(e), 'debug': f"Exception in VE.Direct process for {dev}: {e}"})
        q = multiprocessing.Queue()
        p = multiprocessing.Process(target=target, args=(q,))
        p.start()
        p.join(timeout)
        if p.is_alive():
            p.terminate()
            p.join()
            return False
        try:
            res = q.get(timeout=1)#q.get_nowait()
            if 'debug' in res and logger:
                logger.debug(res['debug'])
            if isinstance(res, dict) and res.get('result'):
                if res.get('partial') and logger:
                    logger.warning(f"Partial VE.Direct packet detected on {dev}, missing keys: {res.get('missing')}")
                return True
            return False
        except Exception:
            return False
    """
    Generalized device finder for VE.Direct and GPS devices.
    Returns (vedirect_dev, gps_dev) tuple. If a device is not found, its value is None.
    """
    usb_devices = sorted(glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*'))
    if logger:
        logger.debug(f"Scanning for devices among: {usb_devices}")
    vedirect_dev = None
    gps_dev = None
    # First, try to find VE.Direct device if requested
    if find_vedirect:
        for dev in usb_devices:
            if logger:
                logger.debug(f"Trying VE.Direct on {dev}")
            success = try_vedirect_in_process(dev, timeout=5)
            if success:
                vedirect_dev = dev
                if logger:
                    logger.info(f"VE.Direct found on {dev}")
                break
            else:
                if logger:
                    logger.debug(f"No valid VE.Direct data on {dev}")
            time.sleep(sleep_time)
        if vedirect_dev is None and logger:
            logger.error("No VE.Direct device found on USB/ACM ports")
    # Now, try to find GPS device if requested
    if find_gps:
        gps_candidates = [d for d in usb_devices if d != vedirect_dev] if vedirect_dev else usb_devices
        for dev in gps_candidates:
            try:
                if logger:
                    logger.debug(f"Trying GPS on {dev}")
                uart = serial.Serial(dev, baudrate=9600, timeout=timeout)
                test_gps = adafruit_gps.GPS(uart, debug=False)
                test_gps.send_command(b"PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")
                test_gps.send_command(b"PMTK220,1000")
                test_gps.update()
                # Accept as GPS if no exception and object is created
                gps_dev = dev
                if logger:
                    logger.info(f"GPS found on {dev}")
                break
            except Exception as e:
                if logger:
                    logger.debug(f"Failed to init GPS on {dev}: {type(e).__name__} - {e}")
                time.sleep(sleep_time)
                continue
        if gps_dev is None and logger:
            logger.error("No GPS device found on USB/ACM ports")
    return vedirect_dev, gps_dev

def main():

    parser = argparse.ArgumentParser(description="Sensor LoRa Daemon")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging for this module only")
    parser.add_argument("-l", "--logdir", default="./logs", help="Directory for sensor data logs")
    parser.add_argument("-i", "--interval", type=int, default=300, help="Interval between packets in seconds")
    parser.add_argument("-v", "--voltage-threshold", type=float, default=5.1, help="Low voltage shutdown threshold (V), default 5.1V")
    args = parser.parse_args()

    # Set root logger to INFO, only our module to DEBUG if requested
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("sensor_lora_daemon")
    handler = logging.StreamHandler()
    formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
    handler.setFormatter(formatter)
    logger.handlers = []  # Remove any default handlers
    logger.addHandler(handler)
    logger.propagate = False  # Prevent double logging
    logger.setLevel(logging.DEBUG if args.debug else logging.INFO)

    # File logger for sensor data packets
    os.makedirs(args.logdir, exist_ok=True)
    log_filename = datetime.datetime.utcnow().strftime("sensor_data_%Y%m%d-%H%M%S.log")
    file_logger = logging.getLogger("sensor_data_file")
    file_handler = TimedRotatingFileHandler(
        os.path.join(args.logdir, log_filename), when="midnight", backupCount=30, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter('%(message)s'))
    file_logger.handlers = []
    file_logger.addHandler(file_handler)
    file_logger.propagate = False
    file_logger.setLevel(logging.INFO)

    # VE.Direct and GPS initialization
    ve_device, gps_device = find_devices(timeout=5, logger=logger, find_vedirect=True, find_gps=True)
    if not ve_device:
        logger.error("No VE.Direct device found. Will retry every 30s.")
    if not gps_device:
        logger.error("No GPS device found. Will retry every 30s.")
        gps = None
    else:
        uart = serial.Serial(gps_device, baudrate=9600, timeout=10)
        gps = adafruit_gps.GPS(uart, debug=False)
        gps.send_command(b"PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")
        gps.send_command(b"PMTK220,1000")

    # RFM9x LoRa initialization
    # SPI and chip select/reset pins (match working example)
    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    cs = digitalio.DigitalInOut(board.CE1)     # GPIO8 / physical pin 24
    reset = digitalio.DigitalInOut(board.D25)  # GPIO25 / physical pin 22
    try:
        rfm9x = adafruit_rfm9x.RFM9x(spi, cs, reset, 902.5)
        rfm9x.tx_power = 23
        logger.info("RFM9x LoRa radio initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize RFM9x LoRa radio: {type(e).__name__} - {e}")
        rfm9x = None

    # VE.Direct initialization
    error_count = 0
    while True:
        if not ve_device:
            ve_device, _ = find_devices(timeout=5, logger=logger, find_vedirect=True, find_gps=False)
            if not ve_device:
                logger.error("No VE.Direct device found. Will retry every 30s.")
                time.sleep(30)
                continue
            else:
                logger.info(f"Found VE.Direct device: {ve_device}")
        ve = Vedirect(port=ve_device, timeout=5)
        logger.debug("Reading Victron MPPT metrics...")
        try:
            ve_data = ve.read_data_single()
            error_count = 0  # Reset on success
        except Exception as e:
            logger.error(f"VE.Direct read error: {type(e).__name__} - {e}")
            error_count += 1
            if error_count >= 3:
                logger.warning("Reinitializing VE.Direct interface after repeated errors.")
                ve = Vedirect(port=ve_device, timeout=5)
                error_count = 0
            ve_data = None  # Skip this sensor for this packet

        # Check battery voltage and shut down if below threshold
        battery_voltage = None
        if ve_data is not None:
            try:
                # VE.Direct returns voltage in millivolts
                battery_voltage = float(ve_data.get('V', 0)) / 1000.0
                logger.debug(f"Battery voltage: {battery_voltage:.2f} V")
                if battery_voltage < args.voltage_threshold:
                    logger.critical(f"Battery voltage {battery_voltage:.2f} V below threshold ({args.voltage_threshold} V). Initiating shutdown.")
                    # Log shutdown event
                    try:
                        shutdown_record = {
                            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            'event': 'shutdown',
                            'reason': f'Battery voltage {battery_voltage:.2f} V below threshold ({args.voltage_threshold} V)'
                        }
                        file_logger.info(json.dumps(shutdown_record))
                    except Exception as e:
                        logger.error(f"File log error during shutdown: {type(e).__name__} - {e}")
                    # Attempt graceful shutdown
                    os.system('sudo shutdown -h now')
                    time.sleep(60)  # Prevent further loop iterations
                    break
            except Exception as e:
                logger.error(f"Error parsing battery voltage: {type(e).__name__} - {e}")

        gps_data = get_gps_data(gps)
        bno_data = get_bno08x_data()
        htu_data = get_htu31d_data()
        timestamp = datetime.datetime.now(datetime.timezone.utc)
        packet = build_packet(vedirect=ve_data, gps=gps_data, bno08x=bno_data, htu31d=htu_data, timestamp=timestamp)
        # Log packet to file in JSON format
        cleanup_old_logs(args.logdir)
        try:
            record = packet.to_dict()
            # Always put timestamp first, then event if present, then sensors
            log_record = {'timestamp': record['timestamp']}
            if 'event' in record:
                log_record['event'] = record['event']
            log_record['sensors'] = record['sensors']
            file_logger.info(json.dumps(log_record))
        except Exception as e:
            logger.error(f"File log error: {type(e).__name__} - {e}")
        if rfm9x is not None:
            try:
                rfm9x.send(packet.encode_packet())
                logger.info(f"Sent: {packet}")
            except Exception as e:
                logger.error(f"LoRa send error: {type(e).__name__} - {e}")
                # Optionally: continue, or break/raise if persistent
        else:
            logger.warning("RFM9x LoRa radio not initialized, skipping LoRa send.")
        for _ in range(args.interval * 10):
            if gps is not None:
                gps.update()
            time.sleep(0.1)

if __name__ == "__main__":
    main()
