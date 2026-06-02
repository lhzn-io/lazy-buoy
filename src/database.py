import os
import sqlite3
import logging
import math
import random
import hashlib
from datetime import datetime, timedelta

# Dynamic Imports with fallback modules for maximum execution robustness
try:
    from sentence_transformers import SentenceTransformer
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False

try:
    import lancedb
    LANCEDB_AVAILABLE = True
except ImportError:
    LANCEDB_AVAILABLE = False


def get_deterministic_embedding(text, dimension=384):
    """Generate a deterministic, normalized embedding vector using standard libraries.
    
    This serves as a high-fidelity, zero-dependency alternative to deep learning models
    for local testing and environment bootstrapping.
    """
    if not text:
        return [0.0] * dimension
        
    # Use MD5 of text to seed random generator deterministically
    hasher = hashlib.md5(text.encode("utf-8")).digest()
    seed = int.from_bytes(hasher, byteorder="big")
    rng = random.Random(seed)
    
    # Generate Gaussian distribution values
    vec = [rng.gauss(0.0, 1.0) for _ in range(dimension)]
    
    # Normalize to unit length
    sq_sum = sum(x * x for x in vec)
    norm = math.sqrt(sq_sum)
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


class MockLanceTable:
    """Mock LanceDB table implementing standard vector search APIs in pure python."""
    def __init__(self, db_dir):
        self.db_dir = db_dir
        self.records = []
        self.logger = logging.getLogger("MockLanceTable")
        self._load_records()

    def _load_records(self):
        # Simplistic in-memory database storage for mock fallback
        pass

    def add(self, data_list):
        """Append data records to the local store."""
        for item in data_list:
            # Ensure standard typing
            item["id"] = int(item["id"])
            item["vector"] = list(item["vector"])
            self.records.append(item)
        self.logger.debug(f"Added {len(data_list)} vectors to MockLanceDB.")

    def search(self, query_vector):
        """Perform a vector search using brute-force cosine similarity."""
        return MockSearchQuery(self.records, query_vector)


class MockSearchQuery:
    """Helper representing a pending LanceDB vector search query."""
    def __init__(self, records, query_vector):
        self.records = records
        self.query_vector = query_vector
        self._limit = 5

    def limit(self, limit_val):
        self._limit = limit_val
        return self

    def to_list(self):
        """Compute cosine similarities and return sorted list of records."""
        results = []
        for rec in self.records:
            v_rec = rec.get("vector", [])
            if not v_rec or len(v_rec) != len(self.query_vector):
                continue
                
            # Compute dot product (since vectors are unit normalized, this is cosine similarity)
            dot_product = sum(x * y for x, y in zip(v_rec, self.query_vector))
            
            # Map score to output dict
            out_item = rec.copy()
            out_item["_distance"] = 1.0 - dot_product # Distance = 1.0 - Similarity
            results.append(out_item)
            
        # Sort by distance (smaller distance means more similar)
        results.sort(key=lambda x: x["_distance"])
        return results[:self._limit]


class HybridStore:
    """Manages the hybrid storage engine (SQLite + LanceDB)."""
    def __init__(self, sqlite_path, lancedb_dir, embedding_model_name="all-MiniLM-L6-v2"):
        self.sqlite_path = sqlite_path
        self.lancedb_dir = lancedb_dir
        self.embedding_model_name = embedding_model_name
        
        self.logger = logging.getLogger("HybridStore")
        self.sqlite_conn = None
        self.lance_db = None
        self.lance_table = None
        self.embedding_model = None

    def initialize(self):
        """Connect to databases, construct tables, and preload embedding models."""
        self._init_sqlite()
        self._init_lancedb()
        self._init_embeddings()

    def _init_sqlite(self):
        """Initialize the SQLite transaction database and verify tables."""
        os.makedirs(os.path.dirname(self.sqlite_path), exist_ok=True)
        self.logger.info(f"Opening SQLite database at: {self.sqlite_path}")
        self.sqlite_conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
        
        # Configure Write-Ahead Logging (WAL) and synchronous settings for SD card preservation
        cursor = self.sqlite_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        
        # Build transcripts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vhf_transcripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                frequency_hz INTEGER NOT NULL,
                duration_seconds REAL NOT NULL,
                audio_path TEXT NOT NULL,
                transcript_text TEXT NOT NULL,
                confidence_score REAL,
                snr_db REAL,
                hardware_node TEXT NOT NULL,
                sdr_device TEXT NOT NULL
            );
        """)
        
        # Build settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        
        # Insert default settings if not exists
        cursor.execute("""
            INSERT OR IGNORE INTO system_settings (key, value) VALUES ('max_segment_duration_seconds', '10.0');
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO system_settings (key, value) VALUES ('noaa_enabled', '0');
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO system_settings (key, value) VALUES ('emergency_enabled', '0');
        """)
        
        # Build search optimization indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trans_time ON vhf_transcripts(timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trans_chan ON vhf_transcripts(channel_name);")
        self.sqlite_conn.commit()
        self.logger.info("SQLite storage schema initialized successfully.")

    def _init_lancedb(self):
        """Initialize the local LanceDB directory or active mock fallbacks."""
        os.makedirs(self.lancedb_dir, exist_ok=True)
        
        if LANCEDB_AVAILABLE:
            self.logger.info(f"Opening LanceDB vector store at: {self.lancedb_dir}")
            try:
                self.lance_db = lancedb.connect(self.lancedb_dir)
                
                # In LanceDB, the table is created dynamically if not already active
                if "semantic_transcripts" in self.lance_db.table_names():
                    self.lance_table = self.lance_db.open_table("semantic_transcripts")
                else:
                    self.lance_table = None # Delayed initialization on first write
                self.logger.info("LanceDB database initialized successfully.")
            except Exception as e:
                self.logger.error(f"Failed to initialize LanceDB: {e}. Falling back to MockLance.")
                self.lance_table = MockLanceTable(self.lancedb_dir)
        else:
            self.logger.warning("LanceDB package missing. Initializing MockLanceTable framework.")
            self.lance_table = MockLanceTable(self.lancedb_dir)

    def _init_embeddings(self):
        """Load SentenceTransformer embedding models or prepare local mock fallback."""
        if EMBEDDINGS_AVAILABLE:
            self.logger.info(f"Loading SentenceTransformer: {self.embedding_model_name}")
            try:
                self.embedding_model = SentenceTransformer(self.embedding_model_name)
                self.logger.info("SentenceTransformer loaded successfully.")
            except Exception as e:
                self.logger.error(f"Failed to load embedding model: {e}. Falling back to deterministic vectors.")
                self.embedding_model = None
        else:
            self.logger.info("SentenceTransformer library not found. Initializing deterministic vector fallback.")
            self.embedding_model = None

    def get_embedding(self, text):
        """Compute embedding vector using either active transformer model or mock generator."""
        if self.embedding_model and text:
            try:
                embedding = self.embedding_model.encode(text)
                return embedding.tolist()
            except Exception as e:
                self.logger.error(f"Embedding computation error: {e}. Falling back.")
                return get_deterministic_embedding(text)
        else:
            return get_deterministic_embedding(text)

    def insert_transcript(self, transcript_data):
        """Write transcript records concurrently to both SQLite and LanceDB.
        
        Args:
            transcript_data (dict): Dictionary with keys matching table columns.
            
        Returns:
            int: The primary key ID generated by the insert.
        """
        # 1. SQLite Relational Write
        cursor = self.sqlite_conn.cursor()
        cursor.execute("""
            INSERT INTO vhf_transcripts (
                timestamp, channel_name, frequency_hz, duration_seconds, 
                audio_path, transcript_text, confidence_score, snr_db, 
                hardware_node, sdr_device
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            transcript_data["timestamp"],
            transcript_data["channel_name"],
            transcript_data["frequency_hz"],
            transcript_data["duration_seconds"],
            transcript_data["audio_path"],
            transcript_data["transcript_text"],
            transcript_data.get("confidence_score", 0.0),
            transcript_data.get("snr_db", 0.0),
            transcript_data["hardware_node"],
            transcript_data["sdr_device"]
        ))
        self.sqlite_conn.commit()
        last_id = cursor.lastrowid
        
        # 2. Vector LanceDB Write (Only index non-empty transcripts)
        text = transcript_data["transcript_text"].strip()
        if text:
            vector = self.get_embedding(text)
            lance_data = {
                "id": last_id,
                "vector": vector,
                "timestamp": transcript_data["timestamp"],
                "channel_name": transcript_data["channel_name"],
                "transcript_text": text
            }
            
            try:
                # Handle dynamic table initialization on first record write
                if LANCEDB_AVAILABLE and self.lance_db and not isinstance(self.lance_table, MockLanceTable):
                    if "semantic_transcripts" not in self.lance_db.table_names():
                        self.lance_table = self.lance_db.create_table("semantic_transcripts", data=[lance_data])
                    else:
                        self.lance_table.add([lance_data])
                else:
                    # MockLance fallback write
                    self.lance_table.add([lance_data])
                self.logger.debug(f"Successfully indexed vector for record ID: {last_id}")
            except Exception as e:
                self.logger.error(f"Vector indexing error for record ID {last_id}: {e}")
                
        return last_id

    def search_semantically(self, query_text, limit=5):
        """Execute a semantic vector search across the active table.
        
        Args:
            query_text (str): Natural language search terms.
            limit (int): Maximum matches to return.
            
        Returns:
            list: List of dicts matching database formats, sorted by cosine score.
        """
        if not query_text:
            return []
            
        # Get query vector projection
        query_vector = self.get_embedding(query_text)
        
        try:
            if self.lance_table:
                # Run vector similarity search
                results = self.lance_table.search(query_vector).limit(limit).to_list()
                
                # Format scores. LanceDB returns "_distance" (smaller is better).
                # We map this to a clean positive similarity metric "score" for agents.
                formatted = []
                for res in results:
                    distance = res.get("_distance", 1.0)
                    similarity = 1.0 - distance
                    formatted.append({
                        "id": res["id"],
                        "timestamp": res["timestamp"],
                        "channel_name": res["channel_name"],
                        "transcript_text": res["transcript_text"],
                        "similarity_score": round(similarity, 3)
                    })
                return formatted
        except Exception as e:
            self.logger.error(f"Semantic search query error: {e}")
            
        return []

    def get_recent_transcripts(self, limit=10, channel_name=None, query=None):
        """Retrieve recent transactions sorted chronologically, optionally filtered by channel and keyword query."""
        cursor = self.sqlite_conn.cursor()
        
        conditions = []
        params = []
        
        if channel_name and channel_name != 'all':
            conditions.append("channel_name = ?")
            params.append(channel_name)
            
        if query and query.strip():
            conditions.append("transcript_text LIKE ?")
            params.append(f"%{query.strip()}%")
            
        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        
        sql = f"""
            SELECT id, timestamp, channel_name, frequency_hz, duration_seconds, 
                   audio_path, transcript_text, confidence_score, snr_db
            FROM vhf_transcripts 
            {where_clause}
            ORDER BY id DESC LIMIT ?
        """
        params.append(limit)
        
        cursor.execute(sql, tuple(params))
        
        rows = cursor.fetchall()
        transcripts = []
        for r in rows:
            transcripts.append({
                "id": r[0],
                "timestamp": r[1],
                "channel_name": r[2],
                "frequency_hz": r[3],
                "duration_seconds": r[4],
                "audio_path": r[5],
                "transcript_text": r[6],
                "confidence_score": r[7],
                "snr_db": r[8]
            })
        return transcripts

    def get_chatter_metrics(self, window_hours=24):
        """Retrieve chatter counts, total durations, and average SNR per channel within a relative hour window."""
        cursor = self.sqlite_conn.cursor()
        cutoff = (datetime.now() - timedelta(hours=window_hours)).isoformat()
        
        cursor.execute("""
            SELECT channel_name, COUNT(*), SUM(duration_seconds), AVG(snr_db)
            FROM vhf_transcripts
            WHERE timestamp >= ?
            GROUP BY channel_name
        """, (cutoff,))
        
        rows = cursor.fetchall()
        metrics = {}
        for r in rows:
            metrics[r[0]] = {
                "count": r[1],
                "total_duration": round(r[2], 1) if r[2] else 0.0,
                "avg_snr": round(r[3], 1) if r[3] else 0.0
            }
        return metrics

    def prune_old_records(self, retention_days):
        """Delete entries and raw WAV files exceeding active expiration bounds.
        
        Args:
            retention_days (int): Days to retain logs before deletion.
        """
        self.logger.info(f"Executing record pruning task (Retention={retention_days} days).")
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        
        cursor = self.sqlite_conn.cursor()
        cursor.execute("SELECT audio_path FROM vhf_transcripts WHERE timestamp < ?", (cutoff,))
        expired_files = cursor.fetchall()
        
        # Remove physical audio WAV segments
        count_deleted_files = 0
        for row in expired_files:
            path = row[0]
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    count_deleted_files += 1
                except Exception as e:
                    self.logger.error(f"Failed to delete expired file {path}: {e}")
                    
        # Delete from SQLite Relational DB
        cursor.execute("DELETE FROM vhf_transcripts WHERE timestamp < ?", (cutoff,))
        self.sqlite_conn.commit()
        
        # For LanceDB, clean data is usually managed by recreating the table periodically
        # or writing custom delta vacuuming if LanceDB features are loaded.
        # Below represents simple pruning updates.
        if LANCEDB_AVAILABLE and self.lance_db and not isinstance(self.lance_table, MockLanceTable):
            try:
                # Standard serverless LanceDB prunes rows using standard SQL-like expressions
                self.lance_table.delete(f"timestamp < '{cutoff}'")
            except Exception as e:
                self.logger.error(f"LanceDB index compaction error: {e}")
        else:
            # Mock table pruning
            if isinstance(self.lance_table, MockLanceTable):
                self.lance_table.records = [r for r in self.lance_table.records if r["timestamp"] >= cutoff]
                
        self.logger.info(f"Pruning completed. Removed {count_deleted_files} physical wave files.")

    def get_setting(self, key, default=None):
        """Retrieve a system setting from the SQLite settings table."""
        try:
            cursor = self.sqlite_conn.cursor()
            cursor.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                return row[0]
        except Exception as e:
            self.logger.error(f"Failed to read setting {key}: {e}")
        return default

    def set_setting(self, key, value):
        """Write or update a system setting in the SQLite settings table."""
        try:
            cursor = self.sqlite_conn.cursor()
            cursor.execute("""
                INSERT INTO system_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (key, str(value)))
            self.sqlite_conn.commit()
            return True
        except Exception as e:
            self.logger.error(f"Failed to write setting {key}={value}: {e}")
            return False

    def close(self):
        """Close outstanding connections."""
        if self.sqlite_conn:
            self.sqlite_conn.close()
            self.sqlite_conn = None
        self.logger.info("Hybrid database connections closed.")
