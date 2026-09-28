import subprocess
import time
import array
import math
import sys

def calculate_rms(pcm_bytes):
    shorts = array.array('h', pcm_bytes)
    if not shorts:
        return 0.0
    sum_squares = sum(s * s for s in shorts)
    mean_square = sum_squares / len(shorts)
    return math.sqrt(mean_square)

def measure(freq, duration=5.0):
    cmd = [
        "rtl_fm",
        "-M", "fm",
        "-f", str(freq),
        "-s", "24000",
        "-r", "16000",
        "-g", "40",
        "-"
    ]
    # We run with timeout to terminate the process
    full_cmd = ["timeout", str(duration)] + cmd
    print(f"Running: {' '.join(full_cmd)}")
    try:
        p = subprocess.Popen(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate()
        rms = calculate_rms(stdout)
        print(f"Freq: {freq} Hz | Bytes read: {len(stdout)} | RMS: {rms:.2f}")
    except Exception as e:
        print(f"Failed to measure: {e}")

def main():
    print("Starting signal power measurements...")
    measure(162550000) # NOAA WX1
    measure(156800000) # Marine 16
    print("Measurements completed.")

if __name__ == "__main__":
    main()
