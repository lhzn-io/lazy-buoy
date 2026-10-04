import os
import sys
import json
import queue
import time
import signal
import logging
import argparse
import threading
import glob
from datetime import datetime

# Local module imports
from sdr_receiver import RtlSdrReceiver, HackRfReceiver
from audio_segmenter import AudioSegmenter
from transcriber import HailoWhisperTranscriber, VoskFallbackTranscriber
from database import HybridStore


class VhfTranscriberDaemon:
    """Main system coordinator daemon orchestrating SDR capture and speech transcription."""
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = {}
        self.running = False
        self.logger = logging.getLogger("VhfTranscriberDaemon")
        
        self.audio_queue = queue.Queue()
        self.sdr_receiver = None
        self.segmenter = None
        self.transcriber = None
        self.db_store = None
        
        self.last_prune_time = 0
        self.transcription_thread = None
        self.exit_code = 0

    def _get_current_power_state(self):
        """Read the latest Victron telemetry from sensor logs to determine power state."""
        log_dir = self.config.get("database", {}).get("audio_storage_dir", "logs/audio").replace("audio", "")
        log_files = sorted(glob.glob(os.path.join(log_dir, "sensor_data_*.log")))
        if not log_files:
            return 13.0, 10.0  # Safe defaults if no telemetry exists

        try:
            with open(log_files[-1], "r") as f:
                lines = f.readlines()
                if lines:
                    last_record = json.loads(lines[-1])
                    sensors = last_record.get("sensors", {})
                    vedirect = sensors.get("vedirect", {})
                    if vedirect:
                        voltage_v = float(vedirect.get("V", 0)) / 1000.0
                        power_w = float(vedirect.get("PPV", 0))
                        return voltage_v, power_w
        except Exception as e:
            self.logger.error(f"Error reading power telemetry: {e}")
            
        return 13.0, 10.0

    def _transcription_worker_loop(self):
        """Background thread that consumes the transcription queue when power allows."""
        self.logger.info("Transcription worker thread started.")
        while self.running:
            try:
                # Check power state
                voltage_v, power_w = self._get_current_power_state()
                
                # Power Logic: Need > 12.4V OR (12.0V + 10W Solar)
                if voltage_v > 12.4 or (voltage_v > 12.0 and power_w > 10.0):
                    item = self.db_store.get_next_pending_audio()
                    if item:
                        self.logger.info(f"[Power OK: {voltage_v:.2f}V {power_w:.1f}W] Transcribing queue item {item['id']}")
                        try:
                            text, confidence = self.transcriber.transcribe_segment(item["audio_path"])
                            clean_text = text.strip()
                            self.logger.info(f"Transcription Text: \"{clean_text}\" (Confidence={confidence:.2f})")
                            
                            # Construct full database record
                            db_record = {
                                "timestamp": item["timestamp"],
                                "channel_name": item["channel_name"],
                                "frequency_hz": item["frequency_hz"],
                                "duration_seconds": item["duration_seconds"],
                                "audio_path": item["audio_path"],
                                "transcript_text": clean_text,
                                "confidence_score": confidence,
                                "snr_db": item["snr_db"],
                                "hardware_node": item["hardware_node"],
                                "sdr_device": item["sdr_device"]
                            }
                            
                            # Index record in both SQLite WAL and LanceDB Vector database
                            self.db_store.insert_transcript(db_record)
                            self.db_store.mark_audio_processed(item["id"])
                        except Exception as e:
                            self.logger.error(f"Error transcribing queue item {item['id']}: {e}")
                            self.db_store.mark_audio_failed(item["id"])
                    else:
                        time.sleep(1.0)
                else:
                    # Low power mode
                    time.sleep(5.0)
            except Exception as e:
                self.logger.error(f"Transcription worker error: {e}")
                time.sleep(2.0)
        self.logger.info("Transcription worker thread stopped.")

    def load_configuration(self):
        """Load JSON settings from the configuration directory."""
        if not os.path.exists(self.config_path):
            self.logger.error(f"Configuration file not found at: {self.config_path}")
            sys.exit(1)
            
        try:
            with open(self.config_path, "r") as f:
                self.config = json.load(f)
            self.logger.info(f"Successfully loaded configuration from {self.config_path}")
        except Exception as e:
            self.logger.error(f"Failed to parse configuration: {e}")
            sys.exit(1)

    def get_target_frequencies(self):
        """Build the target frequency list from config, applying noaa_enabled, emergency_enabled, and fm_radio_enabled filters."""
        noaa_val = self.db_store.get_setting("noaa_enabled", default="1")
        noaa_enabled = (noaa_val == "1")
        emergency_val = self.db_store.get_setting("emergency_enabled", default="1")
        emergency_enabled = (emergency_val == "1")
        fm_val = self.db_store.get_setting("fm_radio_enabled", default="0")
        fm_enabled = (fm_val == "1")
        
        if noaa_enabled:
            return [162550000]
        if fm_enabled:
            return [98100000]
            
        target = []
        for f in self.config.get("scan_frequencies", []):
            freq = f["frequency_hz"]
            name = f.get("name", "")
            if not noaa_enabled and ("NOAA" in name or freq == 162550000):
                continue
            if not emergency_enabled and (freq in [154280000, 155310000, 155550000] or "Emergency" in name or "WCPD" in name):
                continue
            target.append(freq)
        return target

    def initialize_components(self):
        """Initialize SDR hardware, audio segmenter, hybrid database, and speech engines."""
        self.logger.info("Initializing system components...")
        
        # 1. Initialize Hybrid Storage (SQLite + LanceDB)
        db_settings = self.config.get("database", {})
        self.db_store = HybridStore(
            sqlite_path=db_settings.get("sqlite_path", "logs/transcripts.sqlite"),
            lancedb_dir=db_settings.get("lancedb_dir", "logs/lancedb"),
            embedding_model_name=self.config.get("transcriber", {}).get("embedding_model", "all-MiniLM-L6-v2")
        )
        self.db_store.initialize()

        # 2. Initialize Audio Segmenter
        sdr_settings = self.config.get("sdr_settings", {})
        max_duration_str = self.db_store.get_setting("max_segment_duration_seconds", default="10.0")
        try:
            max_duration = float(max_duration_str)
        except (ValueError, TypeError):
            max_duration = 10.0
            
        self.segmenter = AudioSegmenter(
            output_dir=db_settings.get("audio_storage_dir", "logs/audio"),
            sample_rate=sdr_settings.get("output_sample_rate_hz", 16000),
            threshold=sdr_settings.get("audio_threshold", 300.0),
            hang_time_seconds=1.5,
            max_duration_seconds=max_duration
        )

        # 3. Initialize Speech Transcription Engine with Dynamic Fallback
        t_settings = self.config.get("transcriber", {})
        engine_type = t_settings.get("engine", "hailo")
        
        self.logger.info(f"Attempting to initialize primary speech engine: {engine_type}")
        
        if engine_type == "hailo":
            try:
                self.transcriber = HailoWhisperTranscriber(
                    hef_path=t_settings.get("hailo_hef_path", "configs/whisper_tiny_hailo8.hef")
                )
                self.transcriber.initialize()
            except Exception as e:
                self.logger.error(
                    f"Primary Hailo-8 transcription setup failed: {e}. "
                    f"Activating Vosk fallback transcription engine..."
                )
                self._load_vosk_fallback(t_settings)
        else:
            self._load_vosk_fallback(t_settings)

        # 4. Initialize SDR Hardware Abstraction
        sdr_device_type = sdr_settings.get("device_type", "rtl-sdr")
        self.current_scan_frequencies = self.get_target_frequencies()
        
        if sdr_device_type == "hackrf":
            self.sdr_receiver = HackRfReceiver()
        elif sdr_device_type == "rtl-airband":
            from sdr_receiver import RtlAirbandReceiver
            self.sdr_receiver = RtlAirbandReceiver()
        else:
            self.sdr_receiver = RtlSdrReceiver()
            
        noaa_val = self.db_store.get_setting("noaa_enabled", default="1")
        emergency_val = self.db_store.get_setting("emergency_enabled", default="1")
        sdr_config = {
            "frequencies": self.current_scan_frequencies,
            "sdr_settings": sdr_settings,
            "noaa_enabled": noaa_val == "1",
            "emergency_enabled": emergency_val == "1"
        }
        self.sdr_receiver.configure(sdr_config)

    def _load_vosk_fallback(self, t_settings):
        """Helper to initialize the CPU-based Vosk model as a fallback."""
        try:
            self.transcriber = VoskFallbackTranscriber(
                model_path=t_settings.get("vosk_model_path", "vosk-models/vosk-model-small-en-us-0.15")
            )
            self.transcriber.initialize()
        except Exception as e:
            self.logger.critical(f"Critical System Failure: Vosk fallback engine also failed: {e}")
            sys.exit(1)

    def handle_signals(self, signum, frame):
        """Capture signal terminations and trigger graceful shutdowns."""
        self.logger.info(f"Received termination signal ({signum}). Initiating graceful shutdown...")
        self.running = False

    def run(self):
        """Core execution loop consuming raw captures and processing audio segments."""
        self.running = True
        self.logger.info("Starting Marine VHF Transcription Service Daemon.")
        
        # Start Background Transcription Worker
        self.transcription_thread = threading.Thread(target=self._transcription_worker_loop, daemon=True)
        self.transcription_thread.start()
        
        # Start SDR Hardware Capture
        self.sdr_receiver.start_capture(self.audio_queue)
        self.last_prune_time = time.time()
        last_settings_check = 0
        
        sdr_settings = self.config.get("sdr_settings", {})
        sdr_device_type = sdr_settings.get("device_type", "rtl-sdr")
        retention_days = self.config.get("database", {}).get("retention_days", 14)
        
        while self.running:
            if hasattr(self.sdr_receiver, "running") and not self.sdr_receiver.running:
                self.logger.error("SDR receiver capture thread stopped running. Shutting down daemon.")
                # Non-zero exit so systemd records a failure and Restart= brings capture back
                self.exit_code = 1
                self.running = False
                break
                
            current_time = time.time()
            
            # Periodically sync dynamic settings from the database (every 5 seconds)
            if current_time - last_settings_check > 5.0:
                try:
                    val = self.db_store.get_setting("max_segment_duration_seconds")
                    if val is not None:
                        self.segmenter.max_duration_seconds = float(val)
                        
                    # Check if NOAA toggle or scan frequencies changed
                    target_freqs = self.get_target_frequencies()
                    if target_freqs != self.current_scan_frequencies:
                        self.logger.info(
                            f"Scan frequencies changed from {self.current_scan_frequencies} "
                            f"to {target_freqs}. Restarting SDR capture..."
                        )
                        # Stop SDR capture
                        self.sdr_receiver.stop_capture()
                        
                        # Reconfigure SDR
                        noaa_val = self.db_store.get_setting("noaa_enabled", default="1")
                        emergency_val = self.db_store.get_setting("emergency_enabled", default="1")
                        sdr_config = {
                            "frequencies": target_freqs,
                            "sdr_settings": sdr_settings,
                            "noaa_enabled": noaa_val == "1",
                            "emergency_enabled": emergency_val == "1"
                        }
                        self.sdr_receiver.configure(sdr_config)
                        
                        # Clear old data from capture queue
                        while not self.audio_queue.empty():
                            try:
                                self.audio_queue.get_nowait()
                            except queue.Empty:
                                break
                                
                        # Restart SDR capture
                        self.sdr_receiver.start_capture(self.audio_queue)
                        self.current_scan_frequencies = target_freqs
                        self.logger.info("SDR capture successfully restarted with new frequencies.")
                except Exception as e:
                    self.logger.error(f"Failed to sync dynamic settings: {e}")
                last_settings_check = current_time
                
            segment_info = None
            try:
                # Read raw PCM chunk or completed segment details
                queue_item = self.audio_queue.get(timeout=1.0)
                if isinstance(queue_item, dict) and queue_item.get("type") == "completed_segment":
                    segment_info = queue_item
                else:
                    if isinstance(queue_item, tuple):
                        pcm_frame, current_freq = queue_item
                    else:
                        pcm_frame = queue_item
                        current_freq = None
                    segment_info = self.segmenter.process_frame(pcm_frame, current_freq)
            except queue.Empty:
                segment_info = self.segmenter.check_timeout()
                
            if segment_info:
                try:
                    # Squelch close and audio save complete
                    self.logger.info(f"Enqueuing new audio segment: {segment_info['audio_path']}")
                    
                    # Determine frequency from true hardware provenance
                    frequency_hz = segment_info.get("frequency_hz")
                    if not frequency_hz:
                        frequency_hz = self.current_scan_frequencies[0] if self.current_scan_frequencies else 156800000
                        
                    # Map to channel name based on closest frequency (within 10 kHz tolerance)
                    channel_name = "Unknown"
                    closest_diff = 10000
                    for f in self.config.get("scan_frequencies", []):
                        diff = abs(f["frequency_hz"] - frequency_hz)
                        if diff < closest_diff:
                            closest_diff = diff
                            channel_name = f.get("name", "Unknown")
                            
                    if channel_name == "Unknown":
                        channel_name = f"Unknown-{(frequency_hz / 1000000):.3f}M"

                    # Add to transcription queue
                    self.db_store.enqueue_audio(segment_info, channel_name, "lhznbuoy", sdr_device_type)
                    
                except Exception as e:
                    self.logger.error(f"Error enqueuing segment: {e}")
                
            self._run_periodic_tasks(retention_days)

        self.cleanup()

    def _run_periodic_tasks(self, retention_days):
        """Execute cleanup tasks (like record auto-pruning) at standard intervals."""
        current_time = time.time()
        # Run pruning every 6 hours (21600 seconds)
        if current_time - self.last_prune_time > 21600:
            try:
                self.db_store.prune_old_records(retention_days)
            except Exception as e:
                self.logger.error(f"Periodic pruning task failed: {e}")
            self.last_prune_time = current_time

    def cleanup(self):
        """Gracefully close outstanding hardware interfaces and database connections."""
        self.logger.info("Halting active services and closing hardware channels...")
        if self.sdr_receiver:
            self.sdr_receiver.stop_capture()
        # Let an in-flight transcription finish before releasing the pipeline under it
        if self.transcription_thread and self.transcription_thread.is_alive():
            self.transcription_thread.join(timeout=15.0)
        # The Hailo pipeline runs a non-daemon thread that otherwise keeps the process
        # alive until systemd's stop timeout. Close it, but never wait more than 10 s.
        if self.transcriber:
            closer = threading.Thread(target=self.transcriber.close, daemon=True)
            closer.start()
            closer.join(timeout=10.0)
            if closer.is_alive():
                self.logger.warning("Transcriber close() did not return within 10 s; forcing exit.")
        if self.db_store:
            self.db_store.close()
        self.logger.info("VHF Transcription Service stopped cleanly.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Marine VHF Transcription Daemon Service")
    parser.add_argument(
        "--config", 
        default="/home/pi/Projects/lhzn-io/lazy-buoy/configs/vhf_transcriber.json",
        help="Path to configuration JSON file"
    )
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()

    # Configure logging guidelines (objectively formatted, no emojis or spurious characters)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    daemon = VhfTranscriberDaemon(args.config)
    daemon.load_configuration()
    daemon.initialize_components()

    # Bind OS termination signals for graceful system cleanups
    signal.signal(signal.SIGTERM, daemon.handle_signals)
    signal.signal(signal.SIGINT, daemon.handle_signals)

    daemon.run()

    # Exit even if a vendor library left a non-daemon thread running; cleanup()
    # has already closed the SDR, transcriber, and databases.
    logging.shutdown()
    os._exit(daemon.exit_code)
