import os
import sys
import logging

# Ensure local source imports are in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from transcriber import VoskFallbackTranscriber

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

def main():
    model_path = "/home/pi/Projects/lhzn-io/lazy-buoy/vosk-models/vosk-model-small-en-us-0.15"
    audio_path = "/home/pi/Projects/lhzn-io/lazy-buoy/logs/noaa_test.wav"
    
    print("Initializing Vosk transcriber...")
    transcriber = VoskFallbackTranscriber(model_path)
    transcriber.initialize()
    
    print(f"Transcribing {audio_path}...")
    text, confidence = transcriber.transcribe_segment(audio_path)
    
    print("\n--- TRANSCRIPTION RESULTS ---")
    print(f"Confidence: {confidence:.2f}")
    print(f"Transcript: \"{text}\"")
    print("-----------------------------\n")

if __name__ == "__main__":
    main()
