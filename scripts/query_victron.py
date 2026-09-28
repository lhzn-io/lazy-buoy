import serial
import time
import sys

def calculate_checksum(command_bytes):
    total = sum(command_bytes)
    checksum = (0x55 - total) % 256
    return checksum

def send_get_command(ser, register_id):
    reg_lo = register_id & 0xFF
    reg_hi = (register_id >> 8) & 0xFF
    cmd_bytes = [7, reg_lo, reg_hi, 0]
    chk = calculate_checksum(cmd_bytes)
    
    hex_str = f":7{reg_lo:02X}{reg_hi:02X}00{chk:02X}\n"
    print(f"Sending: {hex_str.strip()}")
    ser.write(hex_str.encode('ascii'))

def parse_response(response_str):
    if not response_str.startswith(':'):
        return None
    content = response_str[1:].strip()
    if len(content) < 9:
        return None
        
    reply_type_char = content[0]
    reply_type = int(reply_type_char, 16)
    
    try:
        res_bytes = bytes.fromhex(content[1:])
    except ValueError:
        return None
        
    total = reply_type + sum(res_bytes)
    if total % 256 != 0x55:
        print(f"Warning: Response checksum mismatch (sum % 256 = {total % 256:02X})")
        
    if len(res_bytes) < 4:
        return None
        
    reg_lo = res_bytes[0]
    reg_hi = res_bytes[1]
    flags = res_bytes[2]
    data = res_bytes[3:-1]
    
    register_id = reg_lo | (reg_hi << 8)
    return reply_type, register_id, flags, data

def get_register_value(ser, register_id, max_attempts=100):
    reg_lo = register_id & 0xFF
    reg_hi = (register_id >> 8) & 0xFF
    
    # Flush input buffer to clear old data
    ser.reset_input_buffer()
    
    # Send Get command
    send_get_command(ser, register_id)
    
    # Read responses in a loop
    for attempt in range(max_attempts):
        line = ser.readline()
        if not line:
            # Timeout
            break
        try:
            line_str = line.decode('ascii', errors='ignore').strip()
        except Exception:
            continue
            
        if not line_str.startswith(':'):
            # Skip text mode lines
            continue
            
        parsed = parse_response(line_str)
        if parsed:
            reply_type, reg, flags, data = parsed
            if reg == register_id and reply_type == 7:
                # Found the matching reply!
                return reply_type, reg, flags, data
            
    return None

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Query Victron MPPT registers.")
    parser.add_argument("port", nargs="?", default="/dev/ttyUSB0", help="Serial port to use (default: /dev/ttyUSB0)")
    args = parser.parse_args()
    
    port = args.port
    print(f"Opening port {port}...")
    ser = serial.Serial(port, 19200, timeout=1)
    
    # Clear the initial buffer
    ser.reset_input_buffer()
    
    # Send ping first to make sure it responds
    ser.write(b":154\n")
    time.sleep(0.2)
    response = ser.readline().decode('ascii', errors='ignore')
    print(f"Ping Response: {response.strip()}")
    
    registers = {
        0xEDAB: "Load output control",
        0xEDA8: "Load output state",
        0xED9D: "Load switch high level (reconnect)",
        0xED9C: "Load switch low level (disconnect)",
        0xEDAD: "Load current",
        0xEDA9: "Load output voltage",
        0xED8D: "Battery voltage"
    }
    
    for reg, name in registers.items():
        parsed = get_register_value(ser, reg)
        if parsed:
            reply_type, register_id, flags, data = parsed
            if len(data) == 1:
                val = data[0]
            elif len(data) == 2:
                val = data[0] | (data[1] << 8)
            elif len(data) == 4:
                val = data[0] | (data[1] << 8) | (data[2] << 16) | (data[3] << 24)
            else:
                val = data.hex()
            
            # Format voltage registers which have scale 0.01V
            if isinstance(val, (int, float)):
                if register_id in (0xED9D, 0xED9C, 0xEDA9, 0xED8D):
                    print(f"  Register {register_id:04X} ({name}): value={val / 100.0:.2f} V, flags={flags:02X}")
                else:
                    print(f"  Register {register_id:04X} ({name}): value={val}, flags={flags:02X}")
            else:
                print(f"  Register {register_id:04X} ({name}): value=Raw({val}), flags={flags:02X}")
        else:
            print(f"  Register {reg:04X} ({name}): Failed to receive response")
            
        time.sleep(0.5)
            
    ser.close()

if __name__ == "__main__":
    main()
