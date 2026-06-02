import struct
import time

# Platform detection
try:
    import board  # CircuitPython-specific module
    IS_CIRCUITPYTHON = True
    IS_RPI = False
except ImportError:
    IS_CIRCUITPYTHON = False
    try:
        import datetime
        import json
        IS_RPI = True
    except ImportError:
        IS_RPI = False

# Import platform-specific modules
if not IS_CIRCUITPYTHON:
    try:
        import datetime
    except ImportError:
        datetime = None
    try:
        import json
    except ImportError:
        json = None

class SensorDataPacket:
    """
    Generalized sensor data packet for encoding/decoding multiple sensor types.
    """
    
    @staticmethod
    def hexdump(data, prefix=""):
        """Create a hexdump -C style output for debugging"""
        if isinstance(data, str):
            data = data.encode('utf-8', errors='replace')
        
        lines = []
        for i in range(0, len(data), 16):
            chunk = data[i:i+16]
            hex_part = ' '.join(f'{b:02x}' for b in chunk)
            # Pad hex part to consistent width (manually for CircuitPython compatibility)
            while len(hex_part) < 47:  # 16*2 + 15 spaces = 47 chars
                hex_part += ' '
            
            # Create ASCII representation
            ascii_part = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in chunk)
            
            lines.append(f"{prefix}{i:08x}  {hex_part}  |{ascii_part}|")
        
        return '\n'.join(lines)
    # Supported sensor types and their attribute order for CSV
    SENSOR_ATTRS = {
        'bme280': ['temperature', 'humidity', 'pressure'],
        'htu31d': ['temperature', 'relative_humidity'],
        'bno08x': ['qx', 'qy', 'qz', 'qw', 'ax', 'ay', 'az', 'gx', 'gy', 'gz', 'mx', 'my', 'mz'],
        'gps': [
            'fix_quality', 'latitude', 'longitude', 'altitude_m', 'speed_kmh', 'satellites',
            'track_angle_deg', 'horizontal_dilution', 'height_geoid', 'pdop', 'vdop'
        ],
        'vedirect': [
            'V', 'I', 'VPV', 'PPV', 'IL', 'CS', 'MPPT', 'ERR', 'H19', 'H20', 'H21', 'H22', 'H23', 'HSDS'
        ],
        # Add more sensor types and their attribute order as needed
    }
    SENSOR_TYPE_IDS = {
        0: 'bme280',
        1: 'htu31d',
        2: 'bno08x',
        3: 'gps',
        4: 'vedirect',
    }
    SENSOR_TYPE_NAMES = {v: k for k, v in SENSOR_TYPE_IDS.items()}
    
    # Platform-specific epoch handling
    if IS_CIRCUITPYTHON:
        # CircuitPython epoch is January 1, 2000, 00:00:00 UTC
        # time.time() returns seconds since CircuitPython epoch
        EPOCH_OFFSET = 0
        EPOCH_YEAR = 2000
    else:
        # Unix/RPi epoch is January 1, 1970, 00:00:00 UTC
        # time.time() returns seconds since Unix epoch
        EPOCH_OFFSET = 946684800  # Seconds between 1970-01-01 and 2000-01-01
        EPOCH_YEAR = 1970

    def __init__(self, *sensors, timestamp=None, debug=0):
        if timestamp is None:
            if IS_CIRCUITPYTHON:
                timestamp = time.time()
            else:
                # For RPi/Unix, we can use time.time() or datetime
                if datetime:
                    timestamp = datetime.datetime.utcnow()
                else:
                    timestamp = time.time()
        self.timestamp = timestamp
        self.debug = debug
        self.sensors = []
        for sensor in sensors:
            sensor_type = self.identify_sensor(sensor)
            if sensor_type:
                self.sensors.append((sensor_type, sensor))

    @classmethod
    def identify_sensor(cls, sensor):
        # Identify sensor type by class name or attributes
        name = sensor.__class__.__name__.lower()
        for key in cls.SENSOR_ATTRS:
            if key in name:
                return key
        return None

    def encode_packet(self):
        # Binary protocol: magic (2 bytes), timestamp (8 bytes, microseconds since EPOCH), sensor count (1 byte), then for each sensor:
        # sensor ID (1 byte), struct length (1 byte), struct data
        SENTINEL = -1e30
        def to_float(val):
            if val is None:
                return SENTINEL
            try:
                return float(val)
            except Exception:
                return SENTINEL
        packet = bytearray()
        packet += b'\x53\x44'  # Magic number 'SD'
        # Timestamp as microseconds since EPOCH (8 bytes)
        if IS_CIRCUITPYTHON:
            # CircuitPython time.time() returns seconds since 2000-01-01
            ts_us = int(self.timestamp * 1_000_000)
        else:
            # Handle datetime object or Unix timestamp
            if hasattr(self.timestamp, 'timestamp'):
                # datetime object
                unix_timestamp = self.timestamp.timestamp()
                # Convert to CircuitPython epoch (2000-01-01 based)
                cp_timestamp = unix_timestamp - self.EPOCH_OFFSET
                ts_us = int(cp_timestamp * 1_000_000)
            else:
                # Assume it's already a Unix timestamp
                cp_timestamp = self.timestamp - self.EPOCH_OFFSET
                ts_us = int(cp_timestamp * 1_000_000)
        
        if self.debug >= 1:
            print(f"DEBUG: Encoding packet with timestamp: {self.timestamp} -> {ts_us} microseconds")
        
        packet += struct.pack('<Q', ts_us)
        packet.append(len(self.sensors))
        
        if self.debug >= 1:
            print(f"DEBUG: Encoding {len(self.sensors)} sensors")
        for sensor_type, sensor in self.sensors:
            type_id = self.SENSOR_TYPE_NAMES[sensor_type]
            attrs = self.SENSOR_ATTRS[sensor_type]
            # For now, encode all as floats (4 bytes each)
            values = [to_float(getattr(sensor, attr, None)) for attr in attrs]
            fmt = '<' + 'f' * len(attrs)
            struct_bytes = struct.pack(fmt, *values)
            
            if self.debug >= 1:
                print(f"DEBUG: Encoding {sensor_type} (ID: {type_id}) with {len(values)} values: {values}")
            
            packet.append(type_id)
            packet.append(len(struct_bytes))
            packet += struct_bytes
        # Optionally add a simple checksum (sum of all bytes modulo 256)
        checksum = sum(packet) % 256
        packet.append(checksum)
        
        result = bytes(packet)
        if self.debug >= 1:
            print(f"DEBUG: Final packet length: {len(result)}, checksum: {checksum}")
        if self.debug >= 2:
            print(f"DEBUG: Encoded packet hexdump:\n{self.hexdump(result, '  ')}")
        
        return result

    @staticmethod
    def decode_packet(packet, debug=0):
        # Binary protocol: magic (2 bytes), timestamp (8 bytes), sensor count (1 byte), then for each sensor:
        # sensor ID (1 byte), struct length (1 byte), struct data, checksum (1 byte)
        if debug >= 1:
            print(f"DEBUG: decode_packet called with type: {type(packet)}, len: {len(packet) if hasattr(packet, '__len__') else 'unknown'}")
        if debug >= 2:
            print(f"DEBUG: Raw packet data:\n{SensorDataPacket.hexdump(packet, '  ')}")
        
        if not (isinstance(packet, (bytes, bytearray)) and len(packet) >= 12):
            raise ValueError(f'Invalid packet type ({type(packet)}) or too short (len={len(packet) if hasattr(packet, "__len__") else "unknown"})')
        
        if debug >= 1:
            print(f"DEBUG: Magic bytes check: {packet[0:2]} (expected b'SD')")
        if packet[0:2] != b'SD':
            raise ValueError(f'Invalid magic number: got {packet[0:2]}, expected b\'SD\'')
        
        # Verify checksum
        expected_checksum = sum(packet[:-1]) % 256
        actual_checksum = packet[-1]
        if debug >= 1:
            print(f"DEBUG: Checksum - expected: {expected_checksum}, actual: {actual_checksum}")
        if expected_checksum != actual_checksum:
            raise ValueError(f'Checksum mismatch: expected {expected_checksum}, got {actual_checksum}')
        
        ts_us = struct.unpack('<Q', packet[2:10])[0]
        timestamp = ts_us / 1_000_000  # Convert microseconds back to seconds
        if debug >= 1:
            print(f"DEBUG: Timestamp: {timestamp} seconds")
        
        sensor_count = packet[10]
        if debug >= 1:
            print(f"DEBUG: Sensor count: {sensor_count}")
        
        idx = 11
        sensors = {}
        for sensor_idx in range(sensor_count):
            if debug >= 1:
                print(f"DEBUG: Processing sensor {sensor_idx + 1}/{sensor_count} at index {idx}")
            if idx + 2 > len(packet) - 1:
                raise ValueError(f'Packet truncated before sensor data at index {idx}')
            sensor_id = packet[idx]
            struct_len = packet[idx + 1]
            if debug >= 1:
                print(f"DEBUG: Sensor ID: {sensor_id}, struct length: {struct_len}")
            idx += 2
            if idx + struct_len > len(packet) - 1:
                raise ValueError(f'Packet truncated in sensor struct at index {idx}')
            struct_bytes = packet[idx:idx + struct_len]
            idx += struct_len
            sensor_type = SensorDataPacket.SENSOR_TYPE_IDS.get(sensor_id)
            if debug >= 1:
                print(f"DEBUG: Sensor type: {sensor_type}")
            if not sensor_type:
                if debug >= 1:
                    print(f"DEBUG: Unknown sensor ID {sensor_id}, skipping")
                continue
            attrs = SensorDataPacket.SENSOR_ATTRS[sensor_type]
            fmt = '<' + 'f' * len(attrs)
            values = struct.unpack(fmt, struct_bytes)
            if debug >= 1:
                print(f"DEBUG: Unpacked values: {values}")
            sensor_obj = type(sensor_type, (), {})()
            for attr, val in zip(attrs, values):
                setattr(sensor_obj, attr, val)
            sensors[sensor_type] = sensor_obj
        return {'timestamp': timestamp, 'sensors': sensors}

    def to_dict(self):
        result = {}
        for sensor_type, sensor in self.sensors:
            result[sensor_type] = {attr: getattr(sensor, attr, None) for attr in self.SENSOR_ATTRS[sensor_type]}
        # Always include event key, default to 'data_packet'
        # Format timestamp appropriately for each platform
        if IS_CIRCUITPYTHON:
            # Format timestamp as string since we don't have datetime.isoformat() in CircuitPython
            timestamp_str = f"{self.timestamp:.3f}"  # Seconds with millisecond precision
        else:
            # On RPi/Unix, prefer datetime formatting if available
            if hasattr(self.timestamp, 'isoformat'):
                timestamp_str = self.timestamp.isoformat()
            else:
                timestamp_str = f"{self.timestamp:.3f}"
        
        out = {
            'timestamp': timestamp_str,
            'event': getattr(self, 'event', 'data_packet'),
            'sensors': result
        }
        return out

    def __str__(self):
        if not IS_CIRCUITPYTHON and json:
            return json.dumps(self.to_dict(), indent=2)
        else:
            # Fallback for CircuitPython or when json is not available
            data = self.to_dict()
            return str(data)
