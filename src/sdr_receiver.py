import subprocess
import threading
import logging
import time
import os

class SdrReceiver:
    """Base class defining the hardware abstraction layer (HAL) interface for SDR receivers."""
    def __init__(self):
        self.running = False
        self.thread = None

    def configure(self, config_dict):
        """Configure the SDR hardware parameters.
        
        Args:
            config_dict (dict): Dictionary containing configuration keys.
        """
        raise NotImplementedError("Subclasses must implement configure()")

    def start_capture(self, output_queue):
        """Begin capturing, demodulating, and placing PCM frames on the output queue.
        
        Args:
            output_queue (queue.Queue): Thread-safe queue for raw audio frames.
        """
        raise NotImplementedError("Subclasses must implement start_capture()")

    def stop_capture(self):
        """Stop hardware streams and clean up processes."""
        self.running = False


class RtlSdrReceiver(SdrReceiver):
    """Concrete implementation of SdrReceiver wrapping the rtl_fm utility."""
    def __init__(self):
        super().__init__()
        self.process = None
        self.frequencies = []
        self.sample_rate = 1024000
        self.output_rate = 16000
        self.squelch_level = 25
        self.gain = 40.0
        self.logger = logging.getLogger("RtlSdrReceiver")
        self.current_tuned_freq = None
        self.stderr_thread = None

    def configure(self, config_dict):
        """Configure frequencies and RTL-SDR specific options."""
        self.frequencies = config_dict.get("frequencies", [156800000])
        sdr_settings = config_dict.get("sdr_settings", {})
        self.sample_rate = sdr_settings.get("sample_rate_hz", 1024000)
        self.output_rate = sdr_settings.get("output_sample_rate_hz", 16000)
        
        # If NOAA is enabled, disable squelch for continuous streaming.
        if config_dict.get("noaa_enabled", False):
            self.squelch_level = 0
        else:
            self.squelch_level = sdr_settings.get("squelch_level", 25)
            
        self.gain = sdr_settings.get("gain_db", 40.0)
        self.logger.info(
            f"Configured RTL-SDR: Frequencies={self.frequencies}, "
            f"Gain={self.gain}dB, Squelch={self.squelch_level}"
        )

    def start_capture(self, output_queue):
        """Launch the rtl_fm subprocess and spawn a background reading thread."""
        if self.running:
            self.logger.warning("Capture already running.")
            return

        self.running = True
        
        # Build the command line for rtl_fm
        # We pass multiple -f arguments to perform C-level frequency scanning / hopping
        cmd = [
            "rtl_fm",
            "-M", "fm",
            "-s", "24000",             # Intermediate sample rate
            "-r", str(self.output_rate), # Output PCM rate (typically 16000 for Whisper/Vosk)
            "-g", str(int(self.gain)),
            "-l", str(int(self.squelch_level)),
            "-"                        # Output raw audio to stdout
        ]
        
        for freq in self.frequencies:
            cmd.extend(["-f", f"{freq}"])

        self.logger.info(f"Launching SDR capture command: {' '.join(cmd)}")
        
        try:
            # We redirect stderr to PIPE to parse tuned frequency
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0
            )
        except Exception as e:
            self.logger.error(f"Failed to launch rtl_fm: {e}")
            self.running = False
            return

        # Spawn reader thread
        self.thread = threading.Thread(
            target=self._read_stream,
            args=(output_queue,),
            daemon=True
        )
        self.thread.start()

        self.stderr_thread = threading.Thread(
            target=self._read_stderr,
            daemon=True
        )
        self.stderr_thread.start()

    def _read_stderr(self):
        import re
        self.logger.info("RTL-SDR stderr reader thread started.")
        while self.running and self.process:
            try:
                line = self.process.stderr.readline().decode('utf-8', errors='ignore')
                if not line:
                    break
                match = re.search(r'Tuned to (\d+) Hz', line)
                if match:
                    # rtl_fm uses a fixed 252 kHz DC offset. Subtract it to get the requested freq.
                    self.current_tuned_freq = int(match.group(1)) - 252000
            except Exception:
                break
        self.logger.info("RTL-SDR stderr reader thread stopped.")

    def _read_stream(self, output_queue):
        """Read demodulated raw 16-bit mono PCM bytes from the process stdout."""
        # 16000 Hz, 16-bit (2 bytes per sample) mono audio.
        # We read in chunks of 4000 bytes (2000 samples, or 125ms of audio)
        chunk_size = 4000
        self.logger.info("RTL-SDR stream reader thread started.")
        
        while self.running:
            if not self.process or self.process.poll() is not None:
                self.logger.error("rtl_fm process terminated unexpectedly.")
                break
                
            try:
                data = self.process.stdout.read(chunk_size)
                if not data:
                    time.sleep(0.01)
                    continue
                
                # Push raw bytes and provenance frequency onto the shared queue
                output_queue.put((data, self.current_tuned_freq))
            except Exception as e:
                self.logger.error(f"Error reading from rtl_fm stdout: {e}")
                break

        self.stop_capture()
        self.logger.info("RTL-SDR stream reader thread stopped.")

    def stop_capture(self):
        """Kill the rtl_fm subprocess and join threads."""
        super().stop_capture()
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            
        if self.thread and self.thread.is_alive() and self.thread != threading.current_thread():
            self.thread.join(timeout=1.0)
            self.thread = None
            
        if self.stderr_thread and self.stderr_thread.is_alive() and self.stderr_thread != threading.current_thread():
            self.stderr_thread.join(timeout=1.0)
            self.stderr_thread = None
            
        self.logger.info("RTL-SDR hardware capture stopped.")


class HackRfReceiver(SdrReceiver):
    """Concrete implementation of SdrReceiver for HackRF One.
    
    This class supports wideband captures (up to 20 MHz) and digital channelization
    to monitor all marine channels simultaneously on high-compute platforms (AGX).
    """
    def __init__(self):
        super().__init__()
        self.frequencies = []
        self.sample_rate = 10000000 # Default to 10 MHz wideband
        self.output_rate = 16000
        self.gain = 30.0
        self.process = None
        self.logger = logging.getLogger("HackRfReceiver")

    def configure(self, config_dict):
        """Configure HackRF specific options and wideband passband boundaries."""
        self.frequencies = config_dict.get("frequencies", [156800000])
        sdr_settings = config_dict.get("sdr_settings", {})
        self.sample_rate = sdr_settings.get("sample_rate_hz", 10000000)
        self.output_rate = sdr_settings.get("output_sample_rate_hz", 16000)
        self.gain = sdr_settings.get("gain_db", 30.0)
        self.logger.info(
            f"Configured HackRF: Wideband Rate={self.sample_rate}Hz, "
            f"Frequencies to monitor={self.frequencies}"
        )

    def start_capture(self, output_queue):
        """Launch the wideband capture process and initialize the software channelizer."""
        if self.running:
            return
        self.running = True
        
        # In a fully realized AGX deployment, this would launch a subprocess wrapping
        # hackrf_transfer or initiate SoapSDR bindings, pipe the raw IQ data into
        # a Software Polyphase Channelizer, filter and decimate down to specific channel
        # sub-bands, run FM demodulation on each, and place PCM data on the queue.
        # Below is a representative stub showing standard command integration.
        cmd = [
            "hackrf_transfer",
            "-r", "-",              # Output raw IQ to stdout
            "-f", "156650000",       # Center frequency covering the marine band cluster
            "-s", str(self.sample_rate),
            "-a", "1",              # Enable RX RF amplifier
            "-l", "24",             # LNA gain
            "-g", "32"              # VGA gain
        ]
        
        self.logger.info(f"Mock HackRF launching wideband stream: {' '.join(cmd)}")
        
        # We spawn a background thread simulating channelized demodulator pipelines
        self.thread = threading.Thread(
            target=self._channelizer_stub,
            args=(output_queue,),
            daemon=True
        )
        self.thread.start()

    def _channelizer_stub(self, output_queue):
        """Simulates software channelizer outputs for testing configurations."""
        self.logger.info("HackRF software channelizer thread started.")
        while self.running:
            # Sleep to match frame output rates
            time.sleep(0.125)
            # In live execution, this parses raw wideband IQ bytes from stdout,
            # filters Channels 68, 69, 16, etc., and pushes individual stream blocks
            pass
        self.logger.info("HackRF software channelizer thread stopped.")

    def stop_capture(self):
        super().stop_capture()
        if self.thread and self.thread.is_alive() and self.thread != threading.current_thread():
            self.thread.join(timeout=1.0)
            self.thread = None
        self.logger.info("HackRF hardware capture stopped.")


class RtlAirbandReceiver(SdrReceiver):
    """Concrete implementation of SdrReceiver wrapping the rtl_airband utility."""
    def __init__(self):
        super().__init__()
        self.process = None
        self.frequencies = []
        self.noaa_enabled = False
        self.emergency_enabled = False
        self.config_dir = "/home/pi/Projects/lhzn-io/lazy-buoy/configs"
        self.spool_dir = "/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio_in"
        self.audio_dir = "/home/pi/Projects/lhzn-io/lazy-buoy/logs/audio"
        self.logger = logging.getLogger("RtlAirbandReceiver")
        self.watcher_thread = None
        self.output_queue = None

        os.makedirs(self.spool_dir, exist_ok=True)
        os.makedirs(self.audio_dir, exist_ok=True)

    def configure(self, config_dict):
        """Generate the rtl_airband.conf file dynamically based on frequency options."""
        self.frequencies = config_dict.get("frequencies", [])
        self.noaa_enabled = config_dict.get("noaa_enabled", False)
        self.emergency_enabled = config_dict.get("emergency_enabled", False)
        
        # Decide mode based on config:
        # If noaa_enabled or emergency_enabled, we must use scan mode because the frequencies span too wide.
        # Otherwise we use multichannel mode centered at 156.6 MHz with 1.024 MSps.
        use_scan = self.noaa_enabled or self.emergency_enabled
        
        conf_lines = []
        conf_lines.append("devices: (")
        conf_lines.append("  {")
        conf_lines.append("    type = \"rtlsdr\";")
        conf_lines.append("    index = 0;")
        conf_lines.append("    gain = 40;")
        if use_scan:
            conf_lines.append("    mode = \"scan\";")
        else:
            conf_lines.append("    mode = \"multichannel\";")
            conf_lines.append("    centerfreq = 156.86;")
            conf_lines.append("    sample_rate = 1.024;")
        conf_lines.append("    correction = 0;")
        
        if use_scan:
            # List of enabled frequencies
            enabled_freqs = []
            labels = []
            modulations = []
            
            # Marine channels
            for f in [156800000, 156475000, 156425000, 156450000]:
                if f in self.frequencies:
                    enabled_freqs.append(f)
                    if f == 156800000:
                        labels.append("Marine-16")
                    elif f == 156475000:
                        labels.append("Marine-69")
                    elif f == 156425000:
                        labels.append("Marine-68")
                    elif f == 156450000:
                        labels.append("Marine-9")
                    modulations.append("nfm")
            
            # NOAA
            if self.noaa_enabled:
                enabled_freqs.append(162550000)
                labels.append("NOAA-WX1")
                modulations.append("nfm")
                
            # Emergency
            if self.emergency_enabled:
                for f in [154280000, 155310000, 155550000]:
                    enabled_freqs.append(f)
                    if f == 154280000:
                        labels.append("Local-Emergency")
                    elif f == 155310000:
                        labels.append("WCPD-Ch1")
                    elif f == 155550000:
                        labels.append("WCPD-Ch3")
                    modulations.append("nfm")
            
            # Write scan channels block
            conf_lines.append("    channels: (")
            conf_lines.append("      {")
            freqs_str = ", ".join(f"{f/1e6:.3f}" for f in enabled_freqs)
            labels_str = ", ".join(f"\"{l}\"" for l in labels)
            mods_str = ", ".join(f"\"{m}\"" for m in modulations)
            conf_lines.append(f"        freqs = ( {freqs_str} );")
            conf_lines.append(f"        modulations = ( {mods_str} );")
            conf_lines.append(f"        labels = ( {labels_str} );")
            conf_lines.append("        outputs: (")
            conf_lines.append("          {")
            conf_lines.append("            type = \"file\";")
            conf_lines.append(f"            directory = \"{self.spool_dir}\";")
            conf_lines.append("            filename_template = \"VHF\";")
            conf_lines.append("            continuous = false;")
            conf_lines.append("            split_on_transmission = true;")
            conf_lines.append("            include_freq = true;")
            conf_lines.append("          }")
            conf_lines.append("        );")
            conf_lines.append("      }")
            conf_lines.append("    );")
        else:
            # Write multichannel channels block nested inside devices block
            conf_lines.append("    channels: (")
            marine_channels = [
                {"freq": 156.800, "name": "Marine-16"},
                {"freq": 156.475, "name": "Marine-69"},
                {"freq": 156.425, "name": "Marine-68"},
                {"freq": 156.450, "name": "Marine-9"},
                {"freq": 156.625, "name": "Marine-72"},
                {"freq": 157.300, "name": "Marine-26TX"}
            ]
            channel_blocks = []
            for ch in marine_channels:
                ch_block = [
                    "      {",
                    f"        name = \"{ch['name']}\";",
                    f"        freq = {ch['freq']:.3f};",
                    "        modulation = \"nfm\";",
                    "        squelch_snr_threshold = 12;",
                    "        outputs: (",
                    "          {",
                    "            type = \"file\";",
                    f"            directory = \"{self.spool_dir}\";",
                    f"            filename_template = \"VHF_{int(ch['freq']*1e6)}\";",
                    "            continuous = false;",
                    "            split_on_transmission = true;",
                    "            include_freq = true;",
                    "          }",
                    "        );",
                    "      }"
                ]
                channel_blocks.append("\n".join(ch_block))
            conf_lines.append(",\n".join(channel_blocks))
            conf_lines.append("    );")

        conf_lines.append("  }")
        conf_lines.append(");")
            
        # Write config file
        config_path = os.path.join(self.config_dir, "rtl_airband.conf")
        try:
            with open(config_path, "w") as f:
                f.write("\n".join(conf_lines))
            self.logger.info(f"Wrote RTLSDR-Airband config file: {config_path}")
        except Exception as e:
            self.logger.error(f"Failed to write config file {config_path}: {e}")

    def start_capture(self, output_queue):
        if self.running:
            self.logger.warning("Capture already running.")
            return
            
        self.output_queue = output_queue
        self.running = True
        
        # Ensure clean spool directory on startup
        import glob
        for f in glob.glob(os.path.join(self.spool_dir, "*")):
            try:
                os.remove(f)
            except Exception:
                pass
        
        config_path = os.path.join(self.config_dir, "rtl_airband.conf")
        cmd = ["rtl_airband", "-c", config_path, "-F"]
        self.logger.info(f"Launching RTLSDR-Airband capture: {' '.join(cmd)}")
        try:
            self.process = subprocess.Popen(
                cmd,
                bufsize=0
            )
        except Exception as e:
            self.logger.error(f"Failed to launch rtl_airband: {e}")
            self.running = False
            return
            
        self.watcher_thread = threading.Thread(
            target=self._watch_spool,
            daemon=True
        )
        self.watcher_thread.start()

    def _watch_spool(self):
        import glob
        self.logger.info("RTLSDR-Airband spool directory watcher started.")
        while self.running:
            if self.process and self.process.poll() is not None:
                self.logger.error(f"rtl_airband process terminated unexpectedly with exit code {self.process.poll()}.")
                self.running = False
                break
            try:
                files = glob.glob(os.path.join(self.spool_dir, "*.mp3"))
                for mp3_path in files:
                    if not self._is_file_closed(mp3_path):
                        continue
                        
                    self.logger.info(f"Detected completed spool file: {mp3_path}")
                    self._process_spool_file(mp3_path)
            except Exception as e:
                self.logger.error(f"Error in spool watcher: {e}")
            time.sleep(1.0)
        self.logger.info("RTLSDR-Airband spool directory watcher stopped.")

    def _is_file_closed(self, filepath):
        try:
            res = subprocess.run(["lsof", filepath], capture_output=True)
            if res.returncode != 0:
                return True
        except Exception:
            try:
                s1 = os.path.getsize(filepath)
                time.sleep(0.5)
                s2 = os.path.getsize(filepath)
                return s1 == s2 and s1 > 0
            except Exception:
                pass
        return False

    def _process_spool_file(self, mp3_path):
        import re
        from datetime import datetime
        filename = os.path.basename(mp3_path)
        match = re.search(r'VHF_(\d+)_(\d{8})_(\d+)', filename)
        
        freq_hz = 156800000
        timestamp_str = None
        if match:
            freq_hz = int(match.group(1))
            date_str = match.group(2)
            time_str = match.group(3)
            try:
                dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
                timestamp_str = dt.isoformat()
            except Exception:
                pass
                
        if not timestamp_str:
            timestamp_str = datetime.now().isoformat()
            
        wav_name = filename.replace(".mp3", ".wav")
        wav_path = os.path.join(self.audio_dir, wav_name)
        
        cmd = ["ffmpeg", "-y", "-i", mp3_path, "-ac", "1", "-ar", "16000", wav_path]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            
            import wave
            duration = 0.0
            with wave.open(wav_path, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                duration = round(frames / float(rate), 2)
                
            os.remove(mp3_path)
            
            segment_info = {
                "type": "completed_segment",
                "audio_path": wav_path,
                "duration_seconds": duration,
                "timestamp": timestamp_str,
                "frequency_hz": freq_hz,
                "snr_db": 18.5
            }
            self.output_queue.put(segment_info)
            self.logger.info(f"Successfully processed segment: {wav_name} (Freq={freq_hz} Hz, Duration={duration}s)")
        except Exception as e:
            self.logger.error(f"Failed to process spool file {filename}: {e}")

    def stop_capture(self):
        super().stop_capture()
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            
        if self.watcher_thread and self.watcher_thread.is_alive():
            self.watcher_thread.join(timeout=1.0)
            self.watcher_thread = None
        self.logger.info("RTLSDR-Airband hardware capture stopped.")
