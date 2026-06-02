import os
import sys
import time
from datetime import datetime, timedelta

# Ensure local source imports are in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import HybridStore

LOG_DIR = "/home/pi/Projects/lhzn-io/lazy-buoy/logs"
sqlite_path = os.path.join(LOG_DIR, "transcripts.sqlite")
lancedb_dir = os.path.join(LOG_DIR, "lancedb")

def seed_data():
    print("Connecting to Hybrid Store...")
    db_store = HybridStore(sqlite_path=sqlite_path, lancedb_dir=lancedb_dir)
    db_store.initialize()
    
    # Highly realistic transcripts spread across the last 2 hours
    base_time = datetime.now()
    
    mock_transcripts = [
        {
            "offset_minutes": 105,
            "channel_name": "NOAA-WX1",
            "frequency_hz": 162550000,
            "duration_seconds": 15.4,
            "audio_path": "/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio/trans_raw_noaa.wav",
            "transcript_text": "the marine forecast for western Long Island Sound: west winds 5 to 10 knots, waves 1 foot or less, slight chance of rain late tonight",
            "confidence_score": 0.94,
            "snr_db": 18.5
        },
        {
            "offset_minutes": 90,
            "channel_name": "Marine-16",
            "frequency_hz": 156800000,
            "duration_seconds": 8.2,
            "audio_path": "/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio/trans_raw_ch16_1.wav",
            "transcript_text": "Securite Securite Securite. This is New York Harbor Coast Guard. Notice to Mariners: dredging operations active in east channel near light buoy 4",
            "confidence_score": 0.91,
            "snr_db": 22.1
        },
        {
            "offset_minutes": 75,
            "channel_name": "Marine-68",
            "frequency_hz": 156425000,
            "duration_seconds": 6.8,
            "audio_path": "/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio/trans_raw_ch68_1.wav",
            "transcript_text": "yeah we got a good group of striped bass near the rocks, wind is picking up though, heading back to shore soon",
            "confidence_score": 0.85,
            "snr_db": 14.2
        },
        {
            "offset_minutes": 60,
            "channel_name": "Marine-69",
            "frequency_hz": 156475000,
            "duration_seconds": 9.1,
            "audio_path": "/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio/trans_raw_ch69_1.wav",
            "transcript_text": "hey Tommy, you seeing any activity near the reef? We have been trolling for two hours with nothing but weed",
            "confidence_score": 0.88,
            "snr_db": 16.4
        },
        {
            "offset_minutes": 45,
            "channel_name": "Local-Emergency",
            "frequency_hz": 154280000,
            "duration_seconds": 12.0,
            "audio_path": "/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio/trans_raw_emergency.wav",
            "transcript_text": "Dispatcher, Engine 4 is en-route to the harbor for a reported fuel spill on dock B near the commercial slip",
            "confidence_score": 0.89,
            "snr_db": 19.8
        },
        {
            "offset_minutes": 35,
            "channel_name": "Marine-68",
            "frequency_hz": 156425000,
            "duration_seconds": 5.4,
            "audio_path": "/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio/trans_raw_ch68_2.wav",
            "transcript_text": "roger that Bill, we are about half a mile south of the lighthouse, catching some bluefish here on jig lures",
            "confidence_score": 0.86,
            "snr_db": 15.1
        },
        {
            "offset_minutes": 25,
            "channel_name": "Marine-69",
            "frequency_hz": 156475000,
            "duration_seconds": 7.5,
            "audio_path": "/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio/trans_raw_ch69_2.wav",
            "transcript_text": "nothing yet Tommy, the current is pulling hard to the east, might try moving closer to the sandy hook bay",
            "confidence_score": 0.87,
            "snr_db": 13.8
        },
        {
            "offset_minutes": 15,
            "channel_name": "Marine-16",
            "frequency_hz": 156800000,
            "duration_seconds": 4.2,
            "audio_path": "/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio/trans_raw_ch16_2.wav",
            "transcript_text": "Coast Guard this is motor vessel Blue Marlin, we are standing by Channel 16, over",
            "confidence_score": 0.95,
            "snr_db": 24.5
        },
        {
            "offset_minutes": 5,
            "channel_name": "NOAA-WX1",
            "frequency_hz": 162550000,
            "duration_seconds": 14.8,
            "audio_path": "/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio/trans_raw_noaa2.wav",
            "transcript_text": "wind warnings active for western Long Island Sound: southwest winds 15 to 20 knots, waves 2 to 3 feet in the afternoon",
            "confidence_score": 0.93,
            "snr_db": 17.9
        }
    ]
    
    print(f"Inserting {len(mock_transcripts)} mock transcripts into database...")
    for t in mock_transcripts:
        # Calculate fresh timestamp
        time_val = base_time - timedelta(minutes=t["offset_minutes"])
        
        db_record = {
            "timestamp": time_val.isoformat(),
            "channel_name": t["channel_name"],
            "frequency_hz": t["frequency_hz"],
            "duration_seconds": t["duration_seconds"],
            "audio_path": t["audio_path"],
            "transcript_text": t["transcript_text"],
            "confidence_score": t["confidence_score"],
            "snr_db": t["snr_db"],
            "hardware_node": "lhznbuoy",
            "sdr_device": "rtl-sdr"
        }
        
        db_store.insert_transcript(db_record)
        print(f"Indexed: [{t['channel_name']}] \"{t['transcript_text'][:40]}...\"")
        
    db_store.close()
    print("Database seeding completed successfully.")

if __name__ == "__main__":
    seed_data()
