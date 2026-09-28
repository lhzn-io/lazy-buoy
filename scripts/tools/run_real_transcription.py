import os
import sys
import numpy as np

# Add STT_hailo_whisper and app directories to sys.path
sys.path.append("/home/pi/Projects/lhzn-io/lazy-buoy/STT_hailo_whisper")
sys.path.append("/home/pi/Projects/lhzn-io/lazy-buoy/STT_hailo_whisper/app")

from app.hailo_whisper_pipeline import HailoWhisperPipeline
from common.preprocessing import preprocess
import common.audio_utils

def main():
    audio_path = "/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio/VHF_156800000_20260605_090609_156800000.wav"
    encoder_hef = "/home/pi/Projects/lhzn-io/lazy-buoy/STT_hailo_whisper/app/hefs/h8l/tiny/tiny-whisper-encoder-10s_15dB_h8l.hef"
    decoder_hef = "/home/pi/Projects/lhzn-io/lazy-buoy/STT_hailo_whisper/app/hefs/h8l/tiny/tiny-whisper-decoder-fixed-sequence-matmul-split_h8l.hef"

    print("Checking paths...")
    print("Audio file exists:", os.path.exists(audio_path))
    print("Encoder HEF exists:", os.path.exists(encoder_hef))
    print("Decoder HEF exists:", os.path.exists(decoder_hef))

    print("\nLoading audio...")
    audio = common.audio_utils.load_audio(audio_path)
    print(f"Loaded {len(audio)} samples.")

    print("\nPreprocessing audio...")
    mel_spectrograms = preprocess(
        audio,
        is_nhwc=True,
        chunk_length=10.0,
        chunk_offset=0,
        max_duration=60
    )
    print(f"Generated {len(mel_spectrograms)} mel-spectrogram chunks.")

    print("\nInitializing HailoWhisperPipeline...")
    pipeline = HailoWhisperPipeline(
        encoder_model_path=encoder_hef,
        decoder_model_path=decoder_hef,
        variant="tiny"
    )

    print("\nRunning inference...")
    full_transcription = []
    for i, mel in enumerate(mel_spectrograms):
        print(f"Processing chunk {i+1}/{len(mel_spectrograms)}...")
        pipeline.send_data(mel)
        transcription = pipeline.get_transcription()
        print(f"Chunk {i+1} result: {transcription}")
        full_transcription.append(transcription)

    pipeline.stop()
    print("\n--- Final Transcription ---")
    print(" ".join(full_transcription))

if __name__ == "__main__":
    main()
