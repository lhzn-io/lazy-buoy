import subprocess
import time
import sys

def test_squelch(freq, squelch_level, duration=4.0):
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
    print(f"Testing squelch -l {squelch_level} on {freq} Hz...")
    try:
        p = subprocess.Popen(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        stdout, _ = p.communicate()
        bytes_received = len(stdout)
        print(f"Squelch {squelch_level}: Received {bytes_received} bytes in {duration} seconds.")
        return bytes_received
    except Exception as e:
        print(f"Error testing squelch {squelch_level}: {e}")
        return -1

def main():
    freq = 156800000 # Marine 16
    print("Finding the squelch threshold to block static on Marine-16...")
    for level in [25, 40, 60, 80, 100, 120, 150]:
        bytes_rec = test_squelch(freq, level)
        if bytes_rec == 0:
            print(f"-> SUCCESS: Squelch level {level} successfully blocked the static noise!")
            break
        else:
            print(f"-> FAILED: Squelch level {level} was too low, static broke through.")

if __name__ == "__main__":
    main()
