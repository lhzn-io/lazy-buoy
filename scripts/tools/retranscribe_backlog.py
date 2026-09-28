import os
import sqlite3
import sys

sys.path.append("/home/pi/Projects/lhzn-io/lazy-buoy/src")
from database import HybridStore
from transcriber import HailoWhisperTranscriber, VoskFallbackTranscriber

def main():
    print("Initializing Database...")
    db_store = HybridStore(
        sqlite_path="/home/pi/Projects/lhzn-io/lazy-buoy/logs/transcripts.sqlite",
        lancedb_dir="/home/pi/Projects/lhzn-io/lazy-buoy/logs/lancedb"
    )
    db_store.initialize()

    print("Initializing Transcriber...")
    try:
        transcriber = HailoWhisperTranscriber(hef_path="/home/pi/Projects/lhzn-io/lazy-buoy/configs/whisper_tiny_hailo8.hef")
        transcriber.initialize()
    except Exception as e:
        print(f"Hailo unavailable: {e}. Falling back to Vosk.")
        transcriber = VoskFallbackTranscriber(model_path="/home/pi/Projects/lhzn-io/lazy-buoy/vosk-models/vosk-model-small-en-us-0.15")
        transcriber.initialize()

    try:
        cursor = db_store.sqlite_conn.cursor()
        cursor.execute("SELECT id, audio_path FROM vhf_transcripts WHERE snr_db > 5.0")
        records = cursor.fetchall()

        print(f"Found {len(records)} transcripts with discernable chatter (SNR > 5.0).")

        for record_id, audio_path in records:
            if not os.path.exists(audio_path):
                continue
                
            print(f"Retranscribing [{record_id}] {os.path.basename(audio_path)}...")
            text, confidence = transcriber.transcribe_segment(audio_path)
            clean_text = text.strip()
            
            if clean_text:
                print(f"New Transcript: {clean_text} (Conf: {confidence:.2f})")
                cursor.execute("UPDATE vhf_transcripts SET transcript_text = ?, confidence_score = ? WHERE id = ?", 
                              (clean_text, confidence, record_id))
                db_store.sqlite_conn.commit()
    finally:
        print("Closing Transcriber...")
        transcriber.close()

    print("Backlog re-transcription complete.")

if __name__ == "__main__":
    main()
