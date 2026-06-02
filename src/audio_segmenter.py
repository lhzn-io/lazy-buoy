import os
import time
import array
import math
import wave
import logging
from datetime import datetime

class AudioSegmenter:
    """Monitors raw PCM audio frames, detects squelch breaks, and records wave segments."""
    def __init__(self, output_dir, sample_rate=16000, threshold=300.0, hang_time_seconds=1.5, max_duration_seconds=10.0):
        """Initialize the segmenter.
        
        Args:
            output_dir (str): Directory where completed .wav files are stored.
            sample_rate (int): Sample rate of the incoming audio.
            threshold (float): RMS amplitude threshold to break squelch.
            hang_time_seconds (float): Silence duration in seconds to trigger segment completion.
            max_duration_seconds (float): Maximum allowed duration of a single audio segment.
        """
        self.output_dir = output_dir
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.hang_time_seconds = hang_time_seconds
        self.max_duration_seconds = max_duration_seconds
        
        self.logger = logging.getLogger("AudioSegmenter")
        self.active_segment = []
        self.in_transmission = False
        self.silence_start_time = None
        self.segment_start_time = None
        self.last_frame_time = None
        self.segment_frequency = None
        
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)

    def calculate_rms(self, pcm_bytes):
        """Calculate the Root Mean Square (RMS) energy of 16-bit signed PCM bytes."""
        # Unpack 16-bit signed shorts (little-endian assumed)
        shorts = array.array('h', pcm_bytes)
        if not shorts:
            return 0.0
        
        # Calculate sum of squares
        sum_squares = sum(s * s for s in shorts)
        mean_square = sum_squares / len(shorts)
        return math.sqrt(mean_square)

    def process_frame(self, pcm_bytes, frequency_hz=None):
        """Process a single PCM block and update the active recording state.
        
        Args:
            pcm_bytes (bytes): Raw mono 16-bit signed PCM audio bytes.
            frequency_hz (int, optional): Hardware frequency tuned during this frame.
            
        Returns:
            dict or None: Metadata dictionary of the completed audio segment, 
                         or None if no segment was completed.
        """
        rms = self.calculate_rms(pcm_bytes)
        current_time = time.time()
        self.last_frame_time = current_time
        
        # Squelch threshold check
        is_active = rms > self.threshold
        
        if is_active:
            # Signal active, reset silence timer
            self.silence_start_time = None
            
            if not self.in_transmission:
                # Start new transmission segment
                self.in_transmission = True
                self.segment_start_time = current_time
                self.segment_frequency = frequency_hz
                self.active_segment = [pcm_bytes]
                self.logger.debug(f"Squelch broken (RMS={rms:.1f}). Starting new audio segment.")
            else:
                # Append to current transmission
                self.active_segment.append(pcm_bytes)
                
                # Check if max duration exceeded
                duration = sum(len(f) for f in self.active_segment) / (self.sample_rate * 2)
                if duration >= self.max_duration_seconds:
                    self.logger.info(
                        f"Max segment duration reached ({self.max_duration_seconds}s). "
                        f"Force-flushing segment."
                    )
                    return self._write_segment()
        else:
            # Signal silent / below threshold
            if self.in_transmission:
                self.active_segment.append(pcm_bytes)
                
                # Check if max duration exceeded during trailing silence
                duration = sum(len(f) for f in self.active_segment) / (self.sample_rate * 2)
                if duration >= self.max_duration_seconds:
                    self.logger.info(
                        f"Max segment duration reached ({self.max_duration_seconds}s) during silence hang. "
                        f"Force-flushing segment."
                    )
                    return self._write_segment()
                
                if self.silence_start_time is None:
                    self.silence_start_time = current_time
                elif current_time - self.silence_start_time > self.hang_time_seconds:
                    # Silence exceeded hang time, close and write segment
                    self.logger.debug(
                        f"Squelch closed. Transmission finished. "
                        f"Hang time reached ({self.hang_time_seconds}s)."
                    )
                    return self._write_segment()
            else:
                # Not in transmission, ignore idle noise
                pass
                
        return None

    def _write_segment(self):
        """Flush the accumulated PCM bytes to a standard WAV file on disk."""
        if not self.active_segment:
            self._reset_state()
            return None
            
        # Combine all frames in the segment
        all_pcm = b"".join(self.active_segment)
        duration = len(all_pcm) / (self.sample_rate * 2) # 2 bytes per sample
        
        # Enforce a minimum length (e.g. 0.5s) to filter out spurious clicks
        if duration < 0.5:
            self.logger.debug(f"Spurious segment discarded due to short duration: {duration:.2f}s.")
            self._reset_state()
            return None
            
        # Generate filename using ISO-8601 formatting for ease of sorting
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"trans_raw_{timestamp_str}.wav"
        filepath = os.path.join(self.output_dir, filename)
        
        try:
            # Write standard wave file
            with wave.open(filepath, 'wb') as wf:
                wf.setnchannels(1)             # Mono
                wf.setsampwidth(2)             # 16-bit
                wf.setframerate(self.sample_rate)
                wf.writeframes(all_pcm)
                
            self.logger.info(f"Saved audio segment: {filename} (Duration={duration:.2f}s)")
            
            result = {
                "audio_path": filepath,
                "duration_seconds": round(duration, 2),
                "timestamp": datetime.now().isoformat(),
                "snr_db": round(self._estimate_snr(), 1), # Estimated Signal-to-Noise ratio
                "frequency_hz": self.segment_frequency
            }
            
            self._reset_state()
            return result
        except Exception as e:
            self.logger.error(f"Failed to write audio segment to disk: {e}")
            self._reset_state()
            return None

    def _estimate_snr(self):
        """Mock SNR estimator based on the active vs. background signal level."""
        # A full implementation would compare signal power to trailing noise floors.
        # Below is a representative value for R&D database logging.
        return 18.5

    def _reset_state(self):
        """Clean up buffers and flags for the next segment."""
        self.active_segment = []
        self.in_transmission = False
        self.silence_start_time = None
        self.segment_start_time = None
        self.last_frame_time = None
        self.segment_frequency = None

    def check_timeout(self):
        """Checks if the time since the last frame exceeds the hang time, and flushes if so.
        
        This handles the case where the SDR subprocess squelches and stops sending data,
        causing the queue to run dry.
        """
        if self.in_transmission:
            current_time = time.time()
            # If we haven't received a frame for hang_time_seconds, assume silence has occurred
            last_time = self.last_frame_time if self.last_frame_time else self.segment_start_time
            if last_time and (current_time - last_time > self.hang_time_seconds):
                self.logger.debug(
                    f"Squelch closed due to stream inactivity. "
                    f"Time since last frame: {current_time - last_time:.2f}s."
                )
                return self._write_segment()
        return None
