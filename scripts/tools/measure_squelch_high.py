import subprocess
import time

def test_squelch(freq, squelch_level, duration=3.0):
    cmd = [
        "rtl_fm",
        "-M", "fm",
        "-f", str(freq),
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
        bytes_received = len(stdout)
        return bytes_received
    except Exception as e:
        print(f"Error testing squelch {squelch_level}: {e}")
        return -1

def main():
    freq = 156800000 # Marine 16
    print("Scanning high squelch levels (150 to 3000) to find the static cutoff...")
    for level in [150, 200, 300, 450, 600, 800, 1000, 1200, 1500, 2000, 2500, 3000]:
        bytes_rec = test_squelch(freq, level)
        print(f"Squelch {level}: received {bytes_rec} bytes.")
        if bytes_rec == 0:
            print(f"-> SUCCESS: Squelch level {level} successfully blocked the static noise!")
            break
    else:
         print("-> WARNING: Static noise was not blocked even at squelch 3000.")

if __name__ == "__main__":
    main()
