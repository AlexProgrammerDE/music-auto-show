"""
Simulators for Music Auto Show.
Provides simulated audio analyzer and DMX interface for testing without hardware.
"""
import logging
import math
import threading
import time
import base64
import io
import wave
from typing import Optional, Callable, List, Tuple, TYPE_CHECKING

import numpy as np

from dmx_controller import DMXInterface

logger = logging.getLogger(__name__)

# Import dataclasses lazily to avoid circular imports
if TYPE_CHECKING:
    from audio_analyzer import AnalysisData, AudioFeatures


def _get_analysis_classes():
    """Lazy import of AnalysisData and AudioFeatures to avoid circular imports."""
    from audio_analyzer import AnalysisData, AudioFeatures
    return AnalysisData, AudioFeatures


class SimulatedAudioAnalyzer:
    """Simulated audio analyzer for testing without audio hardware."""
    
    def __init__(self):
        AnalysisData, _ = _get_analysis_classes()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._data = AnalysisData()
        self._lock = threading.Lock()
        self._callbacks: list[Callable] = []
        self._start_time = 0.0
        self._beat_count = 0
        self.sample_rate = 44100
        self._spectrogram_history: list[list[float]] = []
        self._spectrogram_max_frames = 300
        self._last_spectrogram_time = 0.0
        self._recording_lock = threading.Lock()
        self._recording = False
        self._recording_samples: list[np.ndarray] = []
        self._recording_sample_count = 0
        self._recording_sum_squares = 0.0
        self._recording_peak = 0.0
        self._recording_clipped_samples = 0
        self._recording_max_seconds = 30.0
    
    def add_callback(self, callback: Callable) -> None:
        """Add a callback to be called when analysis data updates."""
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable) -> None:
        """Remove a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def set_gain(self, gain: float) -> None:
        """Set the audio input gain (no-op for simulation)."""
        pass  # Simulation doesn't use gain
    
    def get_gain(self) -> float:
        """Get the current audio input gain."""
        return 1.0  # Always return default for simulation

    def get_input_mode_used(self):
        """Get the simulated input mode."""
        from config import AudioInputMode
        return AudioInputMode.AUTO

    def get_runtime_status(self) -> dict:
        """Get simulated audio input status for UI display."""
        return {
            "configured_device_name": "",
            "configured_mode": "auto",
            "actual_mode": "simulated",
            "device_index": None,
            "device_name": "Simulated audio generator",
            "device_type": "simulated",
            "host_api": "Simulation",
            "channels": 1,
            "sample_rate": self.sample_rate,
            "selection_reason": "simulated",
            "missing_device_name": "",
            "running": self._running,
            "last_error": "",
            "simulated": True,
        }

    def start_recording(self) -> bool:
        """Start recording simulated audio."""
        if not self._running:
            return False

        with self._recording_lock:
            self._recording = True
            self._recording_samples = []
            self._recording_sample_count = 0
            self._recording_sum_squares = 0.0
            self._recording_peak = 0.0
            self._recording_clipped_samples = 0
        return True

    def stop_recording(self) -> dict:
        """Stop recording simulated audio."""
        with self._recording_lock:
            self._recording = False
        return self.get_recording_status()

    def clear_recording(self) -> None:
        """Clear the simulated diagnostic recording."""
        with self._recording_lock:
            self._recording = False
            self._recording_samples = []
            self._recording_sample_count = 0
            self._recording_sum_squares = 0.0
            self._recording_peak = 0.0
            self._recording_clipped_samples = 0

    def get_recording_status(self) -> dict:
        """Get simulated recording status."""
        with self._recording_lock:
            duration = self._recording_sample_count / self.sample_rate if self.sample_rate > 0 else 0.0
            rms = (
                float(np.sqrt(self._recording_sum_squares / self._recording_sample_count))
                if self._recording_sample_count > 0
                else 0.0
            )
            return {
                "recording": self._recording,
                "has_recording": self._recording_sample_count > 0,
                "duration": duration,
                "max_duration": self._recording_max_seconds,
                "sample_rate": self.sample_rate,
                "channels": 1,
                "peak": self._recording_peak,
                "rms": rms,
                "clipped_samples": self._recording_clipped_samples,
                "source": "Simulated audio generator",
            }

    def get_recording_wav_bytes(self) -> bytes:
        """Return the simulated recording as WAV bytes."""
        with self._recording_lock:
            if not self._recording_samples:
                return b""
            samples = np.concatenate(self._recording_samples)

        samples = np.clip(samples, -1.0, 1.0)
        pcm = (samples * 32767.0).astype(np.int16)
        output = io.BytesIO()
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm.tobytes())
        return output.getvalue()

    def get_recording_data_url(self) -> str:
        """Return the simulated recording as a browser-playable data URL."""
        wav_bytes = self.get_recording_wav_bytes()
        if not wav_bytes:
            return ""
        encoded = base64.b64encode(wav_bytes).decode("ascii")
        return f"data:audio/wav;base64,{encoded}"
    
    def start(self) -> bool:
        """Start simulated audio analysis."""
        if self._running:
            return True
        
        self._running = True
        self._start_time = time.time()
        
        # Set up simulated data
        with self._lock:
            self._data.features.tempo = 128.0
            self._data.features.energy = 0.7
            self._data.track_name = "Simulated Audio"
        
        self._thread = threading.Thread(target=self._simulation_loop, daemon=True)
        self._thread.start()
        
        logger.info("Simulated audio analyzer started")
        return True
    
    def stop(self) -> None:
        """Stop simulated audio analysis."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("Simulated audio analyzer stopped")
    
    def get_data(self, include_spectrogram: bool = True):
        """Get current analysis data."""
        AnalysisData, AudioFeatures = _get_analysis_classes()
        with self._lock:
            return AnalysisData(
                features=AudioFeatures(
                    energy=self._data.features.energy,
                    rms=self._data.features.rms,
                    bass=self._data.features.bass,
                    mid=self._data.features.mid,
                    high=self._data.features.high,
                    tempo=self._data.features.tempo,
                    beat_detected=self._data.features.beat_detected,
                    onset_detected=self._data.features.onset_detected,
                    time_since_beat=self._data.features.time_since_beat,
                    beat_confidence=self._data.features.beat_confidence,
                    danceability=self._data.features.danceability,
                    valence=self._data.features.valence
                ),
                beat_position=self._data.beat_position,
                bar_position=self._data.bar_position,
                section_intensity=self._data.section_intensity,
                estimated_beat=self._data.estimated_beat,
                estimated_bar=self._data.estimated_bar,
                track_name=self._data.track_name,
                artist_name=self._data.artist_name,
                is_playing=self._data.is_playing,
                album_colors=list(self._data.album_colors),
                waveform=list(self._data.waveform) if hasattr(self._data, 'waveform') else [],
                spectrum=list(self._data.spectrum) if hasattr(self._data, 'spectrum') else [],
                spectrogram=(
                    [list(frame) for frame in self._data.spectrogram]
                    if include_spectrogram and hasattr(self._data, 'spectrogram')
                    else []
                ),
                onset_history=list(self._data.onset_history) if hasattr(self._data, 'onset_history') else []
            )
    
    def get_task_status(self) -> dict:
        """Get status of background processing tasks (simulated)."""
        return {
            "madmom_status": "Simulated",
            "madmom_processing": False,
            "madmom_available": True,
            "buffer_duration": 5.0,
            "time_until_next": 0.0,
            "progress": 1.0,
            "current_tempo": 128.0,
        }
    
    def _simulation_loop(self) -> None:
        """Main simulation loop generating fake audio analysis data."""
        tempo = 128.0
        beat_interval = 60.0 / tempo
        last_beat_time = self._start_time
        
        while self._running:
            current_time = time.time()
            elapsed = current_time - self._start_time
            
            # Check for beat
            time_since_beat = current_time - last_beat_time
            beat_detected = False
            if time_since_beat >= beat_interval:
                beat_detected = True
                last_beat_time = current_time
                self._beat_count += 1
                time_since_beat = 0
            
            with self._lock:
                # Simulate varying energy
                base_energy = 0.5 + 0.3 * math.sin(elapsed * 0.2)
                beat_pulse = 0.2 * (1.0 - time_since_beat / beat_interval)
                
                self._data.features.energy = base_energy + beat_pulse
                self._data.features.rms = self._data.features.energy * 0.1
                self._data.features.tempo = tempo
                self._data.features.beat_detected = beat_detected
                self._data.features.onset_detected = beat_detected
                self._data.features.time_since_beat = time_since_beat
                self._data.features.beat_confidence = 0.9
                
                # Simulate frequency bands
                self._data.features.bass = 0.6 + 0.3 * math.sin(elapsed * 0.5)
                self._data.features.mid = 0.5 + 0.2 * math.sin(elapsed * 0.7)
                self._data.features.high = 0.4 + 0.2 * math.sin(elapsed * 1.1)
                
                # Simulate danceability and valence
                self._data.features.danceability = 0.7 + 0.1 * math.sin(elapsed * 0.1)
                self._data.features.valence = 0.6 + 0.2 * math.sin(elapsed * 0.15)
                
                # Beat position
                self._data.beat_position = time_since_beat / beat_interval
                self._data.bar_position = ((self._beat_count % 4) + self._data.beat_position) / 4
                self._data.estimated_beat = self._beat_count
                self._data.estimated_bar = self._beat_count // 4
                self._data.section_intensity = base_energy + beat_pulse * 0.5
                
                # Track info
                self._data.track_name = "Simulated Audio"
                self._data.artist_name = ""
                self._data.is_playing = True
                
                # Simulate album colors (cycle through some nice colors)
                hue = (elapsed * 0.1) % 1.0
                self._data.album_colors = self._generate_palette(hue)
                
                # Simulate waveform (100 points with beat-synced dynamics)
                self._data.waveform = self._generate_waveform(elapsed, time_since_beat, beat_interval, base_energy)
                
                # Simulate spectrum (32 frequency bands)
                self._data.spectrum = self._generate_spectrum(elapsed, time_since_beat, beat_interval,
                                                               self._data.features.bass,
                                                               self._data.features.mid,
                                                               self._data.features.high)

                audio_samples = self._generate_audio_samples(elapsed, base_energy, beat_pulse)
                self._capture_recording_samples(audio_samples)
                if current_time - self._last_spectrogram_time >= 0.1:
                    self._spectrogram_history.append(self._generate_spectrogram_frame(audio_samples))
                    if len(self._spectrogram_history) > self._spectrogram_max_frames:
                        self._spectrogram_history = self._spectrogram_history[-self._spectrogram_max_frames:]
                    self._last_spectrogram_time = current_time
                    self._data.spectrogram = [list(frame) for frame in self._spectrogram_history]
                
                # Simulate onset history (64 points)
                self._data.onset_history = self._generate_onset_history(elapsed, time_since_beat, beat_interval)
            
            # Notify callbacks
            for callback in self._callbacks:
                try:
                    callback(self._data)
                except Exception:
                    pass
            
            time.sleep(0.025)  # 40 Hz

    def _generate_audio_samples(self, elapsed: float, energy: float, beat_pulse: float) -> np.ndarray:
        """Generate a short synthetic audio buffer for recording and spectrograms."""
        frame_count = int(self.sample_rate * 0.025)
        t = (np.arange(frame_count, dtype=np.float32) / self.sample_rate) + elapsed
        amplitude = min(0.85, 0.12 + energy * 0.28 + beat_pulse * 0.5)
        signal = (
            0.48 * np.sin(2 * np.pi * 90 * t)
            + 0.32 * np.sin(2 * np.pi * 440 * t)
            + 0.18 * np.sin(2 * np.pi * 1800 * t)
            + 0.08 * np.sin(2 * np.pi * 6200 * t)
        )
        return np.asarray(signal * amplitude, dtype=np.float32)

    def _capture_recording_samples(self, audio_data: np.ndarray) -> None:
        """Append simulated audio to the current diagnostic recording."""
        with self._recording_lock:
            if not self._recording:
                return

            max_samples = int(self.sample_rate * self._recording_max_seconds)
            remaining = max_samples - self._recording_sample_count
            if remaining <= 0:
                self._recording = False
                return

            chunk = np.asarray(audio_data[:remaining], dtype=np.float32).copy()
            if len(chunk) == 0:
                return

            self._recording_samples.append(chunk)
            self._recording_sample_count += len(chunk)
            self._recording_sum_squares += float(np.sum(chunk ** 2))
            self._recording_peak = max(self._recording_peak, float(np.max(np.abs(chunk))))
            self._recording_clipped_samples += int(np.count_nonzero(np.abs(chunk) >= 0.99))

            if self._recording_sample_count >= max_samples:
                self._recording = False

    def _generate_spectrogram_frame(self, audio_data: np.ndarray, num_bands: int = 64) -> List[float]:
        """Generate a normalized log-frequency spectrogram frame."""
        fft_size = 1024
        if len(audio_data) < fft_size:
            audio_data = np.pad(audio_data, (0, fft_size - len(audio_data)))
        else:
            audio_data = audio_data[:fft_size]

        windowed = audio_data * np.hanning(fft_size)
        fft_data = np.abs(np.fft.rfft(windowed)) ** 2
        freq_bands = np.logspace(np.log10(20.0), np.log10(16000.0), num_bands + 1)
        frame: List[float] = []

        for i in range(num_bands):
            low_idx = int(freq_bands[i] * fft_size / self.sample_rate)
            high_idx = int(freq_bands[i + 1] * fft_size / self.sample_rate)
            low_idx = max(0, min(low_idx, len(fft_data) - 1))
            high_idx = max(low_idx + 1, min(high_idx, len(fft_data)))
            power = float(np.mean(fft_data[low_idx:high_idx])) if high_idx > low_idx else 0.0
            db_value = 10.0 * np.log10(power + 1e-12)
            normalized = (db_value + 92.0) / 74.0
            frame.append(float(max(0.0, min(1.0, normalized))))

        return frame
    
    def _generate_waveform(self, elapsed: float, time_since_beat: float, 
                           beat_interval: float, energy: float) -> List[float]:
        """Generate a simulated waveform for visualization."""
        import random
        num_points = 100
        waveform = []
        
        # Beat pulse effect (stronger at beat, decays)
        beat_factor = 1.0 - (time_since_beat / beat_interval) * 0.5
        
        for i in range(num_points):
            # Base wave with multiple frequencies
            t = elapsed * 10 + i * 0.1
            wave = (
                0.3 * math.sin(t * 2.0) +  # Low frequency
                0.2 * math.sin(t * 5.0) +  # Mid frequency
                0.1 * math.sin(t * 13.0)   # High frequency
            )
            
            # Add some randomness
            wave += random.uniform(-0.1, 0.1)
            
            # Scale by energy and beat
            wave = abs(wave) * energy * beat_factor
            
            # Clamp to 0-1
            waveform.append(max(0.0, min(1.0, wave)))
        
        return waveform
    
    def _generate_spectrum(self, elapsed: float, time_since_beat: float, beat_interval: float,
                          bass: float, mid: float, high: float) -> List[float]:
        """Generate a simulated frequency spectrum (32 bands)."""
        import random
        num_bands = 32
        spectrum = []
        
        # Beat pulse
        beat_factor = 1.0 - (time_since_beat / beat_interval) * 0.3
        
        for i in range(num_bands):
            # Position in spectrum (0=low, 1=high)
            pos = i / (num_bands - 1)
            
            # Blend bass/mid/high based on position
            if pos < 0.33:
                # Bass region
                base = bass * (1.0 - pos * 3) + mid * (pos * 3)
            elif pos < 0.66:
                # Mid region
                blend = (pos - 0.33) * 3
                base = mid * (1.0 - blend) + high * blend
            else:
                # High region
                base = high * (1.0 - (pos - 0.66) * 1.5)
            
            # Add fixed per-band texture without implying frequency bins move over time.
            texture = 0.04 * math.sin(i * 1.7)
            
            # Add randomness
            noise = random.uniform(-0.03, 0.03)
            
            value = (base + texture + noise) * beat_factor
            spectrum.append(max(0.0, min(1.0, value)))
        
        return spectrum
    
    def _generate_onset_history(self, elapsed: float, time_since_beat: float, 
                                beat_interval: float) -> List[float]:
        """Generate simulated onset strength history (64 points)."""
        num_points = 64
        history = []
        
        for i in range(num_points):
            # Simulate past onset values (older to newer)
            # Create peaks at beat intervals
            t = elapsed - (num_points - i) * 0.025  # ~40Hz sampling
            
            # Calculate position within beat for this point
            beat_phase = (t % beat_interval) / beat_interval
            
            # Create onset peak at start of each beat
            if beat_phase < 0.1:
                # Sharp rise at beat
                onset = 0.8 + 0.2 * math.sin(beat_phase * math.pi / 0.1)
            else:
                # Decay after beat
                decay = (beat_phase - 0.1) / 0.9
                onset = 0.3 * (1.0 - decay) + 0.2
            
            # Add some noise
            onset += 0.05 * math.sin(t * 20 + i)
            
            history.append(max(0.0, min(1.0, onset)))
        
        return history
    
    def _generate_palette(self, base_hue: float) -> List[Tuple[int, int, int]]:
        """Generate a color palette based on a base hue."""
        colors = []
        for i in range(5):
            hue = (base_hue + i * 0.15) % 1.0
            r, g, b = self._hsv_to_rgb(hue, 0.8, 0.9)
            colors.append((int(r * 255), int(g * 255), int(b * 255)))
        return colors
    
    def _hsv_to_rgb(self, h: float, s: float, v: float) -> Tuple[float, float, float]:
        """Convert HSV to RGB."""
        if s == 0.0:
            return (v, v, v)
        
        i = int(h * 6.0)
        f = (h * 6.0) - i
        p = v * (1.0 - s)
        q = v * (1.0 - s * f)
        t = v * (1.0 - s * (1.0 - f))
        i = i % 6
        
        if i == 0:
            return (v, t, p)
        elif i == 1:
            return (q, v, p)
        elif i == 2:
            return (p, v, t)
        elif i == 3:
            return (p, q, v)
        elif i == 4:
            return (t, p, v)
        else:
            return (v, p, q)


class SimulatedDMXInterface(DMXInterface):
    """Simulated DMX interface for testing without hardware."""
    
    def __init__(self):
        self._is_open = False
        self._last_data = bytes(513)
        self._send_count = 0
    
    def open(self) -> bool:
        """Open simulated interface."""
        logger.info("=" * 60)
        logger.info("SIMULATED DMX INTERFACE")
        logger.info("=" * 60)
        logger.info("No actual DMX output - for testing only")
        logger.info("=" * 60)
        self._is_open = True
        return True
    
    def close(self) -> None:
        """Close simulated interface."""
        logger.info("Simulated DMX interface closed")
        self._is_open = False
    
    def send(self, data: bytes) -> bool:
        """Simulate sending DMX data."""
        if self._is_open:
            self._last_data = data
            self._send_count += 1
            if self._send_count == 1:
                logger.info("Simulated DMX: First frame received")
            return True
        return False
    
    def is_open(self) -> bool:
        """Check if simulated interface is open."""
        return self._is_open
    
    def get_last_data(self) -> bytes:
        """Get the last sent data (for visualization)."""
        return self._last_data
    
    def get_stats(self) -> dict:
        """Get interface statistics."""
        return {
            "type": "simulated",
            "port": "Simulated DMX",
            "device_info": "No hardware output",
            "break_method": "simulated",
            "send_count": self._send_count,
            "error_count": 0,
            "consecutive_errors": 0,
            "last_error": None,
            "is_open": self._is_open
        }
