import subprocess
import wave
import json
import logging
from vosk import Model, KaldiRecognizer
import argparse

# Path to your Vosk model
model_path = "vosk-models/vosk-model-small-en-us-0.15"
model = Model(model_path)
recognizer = KaldiRecognizer(model, 16000)

# Start rtl_fm to tune to NOAA and output raw audio
rtl_cmd = [
    "rtl_fm",
    "-f", "162.550M",  # NOAA frequency
    "-M", "fm",
    '-g', '40',  # Gain level, adjust as needed
    '-l', '20',  # Low cut filter, adjust as needed
    "-s", "22050",
    "-r", "16000",
    "-"
]

# Use sox to convert raw audio to WAV format
sox_cmd = [
    "sox",
    "-t", "raw",
    "-r", "16000",
    "-e", "signed",
    "-b", "16",
    "-c", "1",
    "-",
    "-t", "wav",
    "-"
]

def main():
    parser = argparse.ArgumentParser(description="NOAA Weather Radio VOSK Daemon")
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # Start the pipeline
    rtl_proc = subprocess.Popen(rtl_cmd, stdout=subprocess.PIPE)
    sox_proc = subprocess.Popen(sox_cmd, stdin=rtl_proc.stdout, stdout=subprocess.PIPE)

    logging.info("Listening to NOAA Weather Radio...")

    while True:
        data = sox_proc.stdout.read(4000)
        if not data:
            logging.warning("No audio data received from sox.")
            continue
        logging.debug(f"Read {len(data)} bytes of audio data.")
        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            logging.debug(f"[VOSK] Result: {result}")
            text = result.get("text", "")
            if text:
                logging.info(f"🗣️ {text}")
            else:
                logging.info("[VOSK] No speech recognized in this chunk.")
        else:
            partial = json.loads(recognizer.PartialResult()).get("partial", "")
            if partial:
                logging.debug(f"[VOSK] Partial: {partial}")
            else:
                logging.debug("[VOSK] No partial result.")

if __name__ == "__main__":
    main()

