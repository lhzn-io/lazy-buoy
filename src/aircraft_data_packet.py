import struct

class AircraftDataPacket:
    MAGIC = b'AD'  # 2 bytes
    # Format for a single aircraft record:
    # I: uint32 (ICAO), f: float32 (lat), f: float32 (lon), f: float32 (alt),
    # f: float32 (speed), f: float32 (heading), I: uint32 (timestamp), 8s: 8-byte tail
    AIRCRAFT_STRUCT_FMT = '<IfffffI8s'
    AIRCRAFT_STRUCT_SIZE = struct.calcsize(AIRCRAFT_STRUCT_FMT)

    def __init__(self, aircraft_list):
        """
        aircraft_list: list of dicts, each with keys:
            icao (int), lat (float), lon (float), alt (float), speed (float), heading (float), ts (int), tail (str)
        """
        self.aircraft_list = aircraft_list

    def encode_packet(self):
        count = len(self.aircraft_list)
        header = self.MAGIC + struct.pack('<B', count)
        records = b''
        for ac in self.aircraft_list:
            tail_bytes = ac.get('tail', '').encode('ascii', errors='ignore')[:8].ljust(8, b'\x00')
            record = struct.pack(
                self.AIRCRAFT_STRUCT_FMT,
                ac.get('icao', 0),
                ac.get('lat', 0.0),
                ac.get('lon', 0.0),
                ac.get('alt', 0.0),
                ac.get('speed', 0.0),
                ac.get('heading', 0.0),
                ac.get('ts', 0),
                tail_bytes
            )
            records += record
        # Optionally, add a checksum here if desired
        return header + records

    @classmethod
    def decode_packet(cls, data):
        if not data.startswith(cls.MAGIC):
            raise ValueError("Invalid magic number for AircraftDataPacket")
        count = data[2]
        offset = 3
        aircraft_list = []
        for _ in range(count):
            record = data[offset:offset+cls.AIRCRAFT_STRUCT_SIZE]
            unpacked = struct.unpack(cls.AIRCRAFT_STRUCT_FMT, record)
            aircraft_list.append({
                'icao': unpacked[0],
                'lat': unpacked[1],
                'lon': unpacked[2],
                'alt': unpacked[3],
                'speed': unpacked[4],
                'heading': unpacked[5],
                'ts': unpacked[6],
                'tail': unpacked[7].rstrip(b'\x00').decode('ascii', errors='ignore')
            })
            offset += cls.AIRCRAFT_STRUCT_SIZE
        return cls(aircraft_list)
