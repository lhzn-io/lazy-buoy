"""Offline test of LowVoltageGuard. Run: python tests/test_low_voltage_guard.py

Extracts the class without importing the sensor module, which initializes
I2C hardware at import time."""
import ast, datetime, json, logging, os, sys, tempfile, time, types

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
src_path = os.path.join(SRC, "sensor_lora_daemon.py")
tree = ast.parse(open(src_path).read())
keep = [n for n in tree.body
        if (isinstance(n, ast.ClassDef) and n.name == "LowVoltageGuard")
        or (isinstance(n, ast.Assign) and n.targets[0].id in ("MIN_PLAUSIBLE_BATTERY_V", "RTC_WAKEALARM"))]
alarm = tempfile.NamedTemporaryFile(delete=False, suffix="_wakealarm").name

calls = []
fake_os = types.SimpleNamespace(system=lambda cmd: calls.append(cmd), sync=lambda: calls.append("sync"))
fake_time = types.SimpleNamespace(sleep=lambda s: None)
ns = {"os": fake_os, "time": fake_time, "json": json, "datetime": datetime}
exec(compile(ast.Module(body=keep, type_ignores=[]), src_path, "exec"), ns)
ns["RTC_WAKEALARM"] = alarm

logging.basicConfig(level=logging.CRITICAL)
log = logging.getLogger("t")
records = []
flog = types.SimpleNamespace(info=lambda s: records.append(json.loads(s)))
G = ns["LowVoltageGuard"]

def fresh():
    calls.clear(); records.clear()
    return G(11.5, 3, 3600, log, flog)

def v(mv): return {"V": str(mv)}
ok = True
def expect(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= cond

g = fresh()
expect("healthy reading returns volts, no halt", g.check(v(13570)) == 13.57 and not calls)
expect("missing V field ignored (old code read 0 V)", g.check({"I": "-40"}) is None and not calls)
expect("non-numeric V ignored", g.check({"V": "---"}) is None and not calls)
expect("implausible 0.5 V ignored", g.check(v(500)) is None and not calls and g.low_count == 0)

g = fresh()
g.check(v(11400)); g.check(v(11400))
expect("two low readings do not halt", not calls and g.low_count == 2)
g.check(v(11600))
expect("recovery resets the counter", g.low_count == 0)

g = fresh()
g.check(v(11400)); g.check({"V": None}); g.check(v(11400))
expect("bad frame between lows neither halts nor resets", not calls and g.low_count == 2)
g.check(v(11300))
expect("third consecutive low halts", "sudo shutdown -h now" in calls)
expect("filesystem synced before halt", calls.index("sync") < calls.index("sudo shutdown -h now"))
expect("wake alarm armed +3600", open(alarm).read() == "+3600")
expect("shutdown event logged with wake time", records and records[-1]["event"] == "shutdown" and records[-1]["wake_after_s"] == 3600)

g = G(11.5, 1, 0, log, flog); calls.clear(); records.clear(); open(alarm, "w").write("")
g.check(v(11000))
expect("--wake-after 0 halts without arming", "sudo shutdown -h now" in calls and open(alarm).read() == "" and records[-1]["wake_after_s"] is None)

ns["RTC_WAKEALARM"] = "/nonexistent/dir/wakealarm"
g = fresh(); g.check(v(11000)); g.check(v(11000)); g.check(v(11000))
expect("unwritable RTC still halts (fails safe)", "sudo shutdown -h now" in calls and records[-1]["wake_after_s"] is None)

os.unlink(alarm)
sys.exit(0 if ok else 1)
