"""Minimal VE.Direct HEX protocol client for the Victron MPPT load output.

The text protocol (read by the vedirect package) is read-only; settings such as
the load-output disconnect/reconnect voltages need HEX GET/SET frames on the
same serial port. Frames are ':' + command nibble + hex bytes + checksum, where
command + all bytes + checksum sum to 0x55 (mod 256).
"""
import time

import serial

CMD_GET = 0x7
CMD_SET = 0x8

REG_LOAD_MODE = 0xEDAB          # 5 = user-defined thresholds
REG_LOAD_LOW_V = 0xED9C         # load switch low level (disconnect), 0.01 V, un16
REG_LOAD_HIGH_V = 0xED9D        # load switch high level (reconnect), 0.01 V, un16
LOAD_MODE_USER_DEFINED = 5

# AGENTS.md rule 1: never query the controller more often than once per 10 s
MIN_COMMAND_INTERVAL_S = 10.0


def _frame(cmd, payload):
    checksum = (0x55 - cmd - sum(payload)) % 256
    return (f":{cmd:X}" + "".join(f"{b:02X}" for b in payload) + f"{checksum:02X}\n").encode("ascii")


def _parse(line):
    """Return (cmd, register, flags, data) for a valid HEX reply line, else None."""
    if not line.startswith(":") or len(line) < 10:
        return None
    try:
        cmd = int(line[1], 16)
        body = bytes.fromhex(line[2:])
    except ValueError:
        return None
    if (cmd + sum(body)) % 256 != 0x55 or len(body) < 4:
        return None
    return cmd, body[0] | (body[1] << 8), body[2], body[3:-1]


class VictronHex:
    def __init__(self, port, logger, timeout=1.0):
        self.port = port
        self.logger = logger
        self.timeout = timeout
        self._last_command = 0.0

    def _transact(self, cmd, register, value_bytes=b"", max_lines=60):
        wait = MIN_COMMAND_INTERVAL_S - (time.monotonic() - self._last_command)
        if wait > 0:
            time.sleep(wait)
        payload = [register & 0xFF, (register >> 8) & 0xFF, 0x00] + list(value_bytes)
        with serial.Serial(self.port, 19200, timeout=self.timeout) as ser:
            ser.reset_input_buffer()
            ser.write(_frame(cmd, payload))
            self._last_command = time.monotonic()
            # Replies arrive interleaved with text-protocol frames, often appended
            # to a text line with no newline in between (e.g. "Checksum\tU:79CED..."),
            # so parse from the last ':'; HEX payloads never contain one.
            for _ in range(max_lines):
                raw = ser.readline()
                if not raw:
                    break
                text = raw.decode("ascii", errors="ignore").strip()
                reply = _parse(text[text.rfind(":"):]) if ":" in text else None
                if reply and reply[0] == cmd and reply[1] == register:
                    return reply[2], reply[3]
        return None

    def get(self, register):
        """Return the register's unsigned little-endian value, or None."""
        reply = self._transact(CMD_GET, register)
        if reply is None or reply[0] != 0:
            self.logger.warning(f"Victron GET 0x{register:04X} failed: {reply}")
            return None
        return int.from_bytes(reply[1], "little")

    def set(self, register, value, nbytes):
        """Write a register and confirm the controller echoed the new value."""
        reply = self._transact(CMD_SET, register, value.to_bytes(nbytes, "little"))
        if reply is None or reply[0] != 0 or int.from_bytes(reply[1], "little") != value:
            self.logger.error(f"Victron SET 0x{register:04X}={value} not confirmed: {reply}")
            return False
        return True

    def ensure(self, register, value, nbytes):
        """Write only if the stored value differs (settings live in EEPROM)."""
        current = self.get(register)
        if current == value:
            return True
        self.logger.info(f"Victron 0x{register:04X}: {current} -> {value}")
        return self.set(register, value, nbytes)

    def apply_load_backstop(self, disconnect_v, reconnect_v):
        """Fixed hardware hysteresis used while the node runs normally."""
        ok = self.ensure(REG_LOAD_MODE, LOAD_MODE_USER_DEFINED, 1)
        ok &= self.ensure(REG_LOAD_LOW_V, round(disconnect_v * 100), 2)
        ok &= self.ensure(REG_LOAD_HIGH_V, round(reconnect_v * 100), 2)
        return ok

    def trip_load_disconnect(self, trip_v):
        """Raise the disconnect level above the present voltage so the
        controller cuts the load output; it restores power only once the
        battery climbs past the reconnect level."""
        return self.set(REG_LOAD_LOW_V, round(trip_v * 100), 2)
