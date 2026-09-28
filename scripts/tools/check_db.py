import sqlite3
import sys

def main():
    db_path = "/home/pi/Projects/lhzn-io/lazy-buoy/logs/transcripts.sqlite"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, timestamp, duration_seconds, transcript_text, confidence_score FROM vhf_transcripts ORDER BY id DESC LIMIT 10")
        rows = cursor.fetchall()
        print("Recent database records:")
        for r in rows:
            print(f"ID={r[0]} | Time={r[1]} | Dur={r[2]:.2f}s | Text='{r[3]}' | Conf={r[4]:.2f}")
        conn.close()
    except Exception as e:
        print(f"Error querying database: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
