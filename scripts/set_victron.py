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
    print(f"Sending GET: {hex_str.strip()}")
    ser.write(hex_str.encode('ascii'))

def send_set_command(ser, register_id, value, val_len):
    reg_lo = register_id & 0xFF
    reg_hi = (register_id >> 8) & 0xFF
    
    val_bytes = []
    if val_len == 1:
        val_bytes = [value & 0xFF]
    elif val_len == 2:
        val_bytes = [value & 0xFF, (value >> 8) & 0xFF]
    elif val_len == 4:
        val_bytes = [value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF, (value >> 24) & 0xFF]
        
    cmd_bytes = [8, reg_lo, reg_hi, 0] + val_bytes
    chk = calculate_checksum(cmd_bytes)
    
    val_hex = "".join(f"{b:02X}" for b in val_bytes)
    hex_str = f":8{reg_lo:02X}{reg_hi:02X}00{val_hex}{chk:02X}\n"
    print(f"Sending SET: {hex_str.strip()}")
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

def get_register_value(ser, register_id, max_attempts=15):
    reg_lo = register_id & 0xFF
    reg_hi = (register_id >> 8) & 0xFF
    
    ser.reset_input_buffer()
    send_get_command(ser, register_id)
    
    for attempt in range(max_attempts):
        line = ser.readline()
        if not line:
            break
        try:
            line_str = line.decode('ascii', errors='ignore').strip()
        except Exception:
            continue
            
        if not line_str.startswith(':'):
            continue
            
        parsed = parse_response(line_str)
        if parsed:
            reply_type, reg, flags, data = parsed
            if reg == register_id and reply_type == 7:
                return reply_type, reg, flags, data
            
    return None

def set_register_value(ser, register_id, value, val_len, max_attempts=15):
    ser.reset_input_buffer()
    send_set_command(ser, register_id, value, val_len)
    
    for attempt in range(max_attempts):
        line = ser.readline()
        if not line:
            break
        try:
            line_str = line.decode('ascii', errors='ignore').strip()
        except Exception:
            continue
            
        if not line_str.startswith(':'):
            continue
            
        parsed = parse_response(line_str)
        if parsed:
            reply_type, reg, flags, data = parsed
            if reg == register_id and reply_type == 8:
                return reply_type, reg, flags, data
                
    return None

def main():
    port = "/dev/ttyUSB0"
    if len(sys.argv) > 1:
        port = sys.argv[1]
        
    print(f"Opening port {port}...")
    ser = serial.Serial(port, 19200, timeout=1)
    
    # 1. Set Load Output Control (0xEDAB) = 5 (User Defined settings 1)
    print("\n--- Setting Load Output Control to User Defined (5) ---")
    parsed = set_register_value(ser, 0xEDAB, 5, 1)
    if parsed:
        reply_type, register_id, flags, data = parsed
        print(f"Success! Mode set. Reply flags: {flags:02X}, reply data: {data.hex()}")
    else:
        print("Failed to set load control mode.")
        
    # 2. Set Disconnect Voltage (0xED9C) = 1120 (11.20 V)
    print("\n--- Setting Disconnect Voltage to 11.20 V ---")
    parsed = set_register_value(ser, 0xED9C, 1120, 2)
    if parsed:
        reply_type, register_id, flags, data = parsed
        print(f"Success! Disconnect set. Reply flags: {flags:02X}, reply data: {data.hex()}")
    else:
        print("Failed to set disconnect voltage.")
        
    # 3. Set Reconnect Voltage (0xED9D) = 1260 (12.60 V)
    print("\n--- Setting Reconnect Voltage to 12.60 V ---")
    parsed = set_register_value(ser, 0xED9D, 1260, 2)
    if parsed:
        reply_type, register_id, flags, data = parsed
        print(f"Success! Reconnect set. Reply flags: {flags:02X}, reply data: {data.hex()}")
    else:
        print("Failed to set reconnect voltage.")
        
    # 4. Verify everything by reading it back
    print("\n--- Verifying settings ---")
    registers = {
        0xEDAB: "Load output control",
        0xEDA8: "Load output state",
        0xED9D: "Load switch high level (reconnect)",
        0xED9C: "Load switch low level (disconnect)"
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
            
            if register_id in (0xED9D, 0xED9C):
                print(f"  Register {register_id:04X} ({name}): {val / 100.0:.2f} V")
            else:
                print(f"  Register {register_id:04X} ({name}): {val}")
        else:
            print(f"  Register {reg:04X} ({name}): Failed to read back")
            
    ser.close()

if __name__ == "__main__":
    main()
