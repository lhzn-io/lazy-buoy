import subprocess
import time

def test_noaa(squelch_level, duration=4.0):
    cmd = [
        "rtl_fm",
        "-M", "fm",
        "-f", "162550000",
        "-s", "24000",
        "-r", "16000",
        "-g", "40",
        "-l", str(squelch_level),
        "-"
    ]
    full_cmd = ["timeout", str(duration)] + cmd
    try:
        p = subprocess.Popen(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        stdout, _ = p.communicate()
        return len(stdout)
    except Exception as e:
        print(f"Error testing NOAA squelch {squelch_level}: {e}")
        return -1

def main():
    print("Testing NOAA reception at various squelch levels...")
    for lvl in [0, 50, 100, 150, 200, 300, 400, 500, 600]:
        bytes_rec = test_noaa(lvl)
        print(f"Squelch {lvl}: received {bytes_rec} bytes.")

if __name__ == "__main__":
    main()
