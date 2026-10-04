"""Offline tests for victron_hex and the LowVoltageGuard sleep path.
Run: .venv/bin/python tests/test_victron_power.py (needs pyserial)

No hardware: a fake serial port emulates the MPPT's HEX replies."""
import ast, datetime, json, logging, os, sys, tempfile, types

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)
logging.basicConfig(level=logging.CRITICAL)
log = logging.getLogger("t")
ok = True
def expect(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)

# ---------------- victron_hex against a fake controller ----------------
import victron_hex as vh

clock = [1000.0]; sleeps = []
vh.time = types.SimpleNamespace(monotonic=lambda: clock[0],
                                sleep=lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)))

class FakeMPPT:
    regs = {}; writes = []; frames = []; respond = True; glue = []
    def __init__(self, port, baud, timeout):
        assert baud == 19200 and timeout == 1.0
        self.out = []
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def reset_input_buffer(self): pass
    def write(self, data):
        line = data.decode().strip(); FakeMPPT.frames.append(line)
        cmd = int(line[1], 16); body = bytes.fromhex(line[2:])
        assert (cmd + sum(body)) % 256 == 0x55, "bad checksum sent"
        reg = body[0] | body[1] << 8
        if cmd == 8:
            FakeMPPT.writes.append((reg, int.from_bytes(body[3:-1], "little")))
            FakeMPPT.regs[reg] = (int.from_bytes(body[3:-1], "little"), len(body[3:-1]))
        val, n = FakeMPPT.regs[reg]
        payload = [body[0], body[1], 0] + list(val.to_bytes(n, "little"))
        chk = (0x55 - cmd - sum(payload)) % 256
        reply = f":{cmd:X}" + "".join(f"{b:02X}" for b in payload) + f"{chk:02X}"
        # The real MPPT appends the reply to a text-protocol line, observed on
        # lhznbuoy as "Checksum\tU:79CED00880439"; the checksum byte may even be ':'
        glue = FakeMPPT.glue.pop(0) if FakeMPPT.glue else b"Checksum\tU"
        self.out = [b"PID\t0xA07D\r\n", b"V\t13460\r\n"] + ([glue + reply.encode() + b"\n"] if FakeMPPT.respond else [])
    def readline(self): return self.out.pop(0) if self.out else b""

vh.serial = types.SimpleNamespace(Serial=FakeMPPT)

expect("GET frame matches scripts/query_victron.py format", vh._frame(7, [0xAB, 0xED, 0]) == b":7ABED00B6\n")
FakeMPPT.regs = {0xEDAB: (5, 1), 0xED9C: (1120, 2), 0xED9D: (1280, 2)}
v = vh.VictronHex("/dev/fake", log)
expect("GET reads load mode through text-protocol noise", v.get(0xEDAB) == 5)
expect("GET reads disconnect 11.20 V", v.get(0xED9C) == 1120)
expect("commands spaced >= 10 s (AGENTS.md rule 1)", sleeps and min(sleeps) >= 9.99)

FakeMPPT.writes.clear()
expect("backstop applied", v.apply_load_backstop(11.6, 13.4))
expect("mode already 5 is not rewritten (EEPROM wear)", (0xEDAB, 5) not in FakeMPPT.writes)
expect("backstop writes disconnect 11.60 V and reconnect 13.40 V", FakeMPPT.writes == [(0xED9C, 1160), (0xED9D, 1340)])
FakeMPPT.writes.clear()
v.apply_load_backstop(11.6, 13.4)
expect("second backstop pass writes nothing", FakeMPPT.writes == [])
expect("trip sets disconnect 12.30 V", v.trip_load_disconnect(12.3) and FakeMPPT.regs[0xED9C] == (1230, 2))
FakeMPPT.respond = False
expect("unconfirmed SET reports failure", v.trip_load_disconnect(12.2) is False)
expect("missing GET reply returns None", v.get(0xEDAB) is None)
FakeMPPT.respond = True
FakeMPPT.glue = [b"Checksum\t:", b"", b"\x00V"]
expect("reply glued after a ':' checksum byte", v.get(0xEDAB) == 5)
expect("reply on its own line", v.get(0xEDAB) == 5)
expect("reply glued after binary noise (seen as '\\x00V:79CED...')", v.get(0xEDAB) == 5)
expect("captured real line parses to 11.60 V", vh._parse(":79CED00880439") == (7, 0xED9C, 0, b"\x88\x04"))

# ---------------- LowVoltageGuard sleep path ----------------
src = os.path.join(SRC, "sensor_lora_daemon.py")
tree = ast.parse(open(src).read())
keep = [n for n in tree.body if (isinstance(n, ast.ClassDef) and n.name == "LowVoltageGuard")
        or (isinstance(n, ast.Assign) and n.targets[0].id in ("MIN_PLAUSIBLE_BATTERY_V", "RTC_WAKEALARM"))]
calls = []; records = []
ns = {"os": types.SimpleNamespace(system=lambda c: calls.append(c), sync=lambda: calls.append("sync")),
      "time": types.SimpleNamespace(sleep=lambda s: None), "json": json, "datetime": datetime}
exec(compile(ast.Module(body=keep, type_ignores=[]), src, "exec"), ns)
alarm = tempfile.NamedTemporaryFile(delete=False).name; ns["RTC_WAKEALARM"] = alarm
flog = types.SimpleNamespace(info=lambda s: records.append(json.loads(s)))
G = ns["LowVoltageGuard"]
mv = lambda x: {"V": str(x)}

trips = []
def trip(volts): trips.append(volts); calls.append("trip"); return True
g = G(12.0, 3, 3600, log, flog, victron_trip=trip)
for x in (11950, 11900, 11880): g.check(mv(x))
expect("sleep trips Victron with the last low reading", trips == [11.88])
expect("order: RTC armed, sync, Victron trip, then halt",
       open(alarm).read() == "+3600" and calls == ["sync", "trip", "sudo shutdown -h now"])
expect("shutdown record notes Victron trip", records[-1]["victron_trip"] is True)

calls.clear()
def broken(volts): raise OSError("port gone")
g = G(12.0, 1, 3600, log, flog, victron_trip=broken); g.check(mv(11900))
expect("Victron failure still halts (RTC fallback)", calls == ["sync", "sudo shutdown -h now"])

calls.clear(); records.clear()
g = G(12.0, 1, 3600, log, flog); g.check(mv(11900))
expect("no Victron yet: halts with RTC only", calls == ["sync", "sudo shutdown -h now"] and records[-1]["victron_trip"] is False)

calls.clear()
g = G(12.0, 3, 3600, log, flog, victron_trip=trip); g.check({}); g.check(mv(500)); g.check(mv(12100))
expect("bad frames and healthy readings never trip", calls == [])

# trip level as wired in main(): min(v + margin, reconnect - 0.5)
expect("trip level 11.90 V -> 12.20 V", round(min(11.90 + 0.3, 13.4 - 0.5), 2) == 12.2)
os.unlink(alarm)
sys.exit(0 if ok else 1)
