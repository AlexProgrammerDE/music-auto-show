"""
Real-time audio analyzer that captures system audio and extracts audio features.
Uses PyAudioWPatch for WASAPI loopback capture and librosa for beat/tempo detection.
"""
import threading
import time
import numpy as np
from typing import Optional, Callable
from dataclasses import dataclass, field
from collections import deque

try:
    import pyaudiowpatch as pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    try:
        import pyaudio
        PYAUDIO_AVAILABLE = True
    except ImportError:
        PYAUDIO_AVAILABLE = False

try:
    import librosa
    LIBROSA_AVAILABLE = True
except ImportError:
    LIBROSA_AVAILABLE = False


@dataclass
class AudioFeatures:
    """Real-time audio features extracted from system audio."""
    # Energy/loudness (0-1 scale)
    energy: float = 0.0
    rms: float = 0.0
    
    # Frequency bands (0-1 scale)
    bass: float = 0.0      # 20-250 Hz
    mid: float = 0.0       # 250-4000 Hz
    high: float = 0.0      # 4000-20000 Hz
    
    # Beat/tempo
    tempo: float = 120.0   # BPM
    beat_detected: bool = False
    onset_detected: bool = False
    
    # Beat timing
    time_since_beat: float = 0.0  # Seconds since last beat
    beat_confidence: float = 0.0   # 0-1 confidence in tempo
    
    # Derived
    danceability: float = 0.5  # Estimated from beat regularity
    valence: float = 0.5       # Estimated from frequency balance


@dataclass 
class AnalysisData:
    """Combined analysis data for visualization (compatible with effects engine)."""
    features: AudioFeatures = field(default_factory=AudioFeatures)
    
    # Beat position tracking
    beat_position: float = 0.0   # 0-1 within current beat
    bar_position: float = 0.0    # 0-1 within current bar
    section_intensity: float = 0.5
    
    # Beat counters
    estimated_beat: int = 0
    estimated_bar: int = 0
    
    # Track info (optional, for display only)
    track_name: str = "System Audio"
    artist_name: str = ""
    is_playing: bool = True
    
    @property
    def normalized_energy(self) -> float:
        return self.features.energy
    
    @property
    def normalized_tempo(self) -> float:
        """Get tempo normalized to 0-1 (60-180 BPM range)."""
        return max(0.0, min(1.0, (self.features.tempo - 60) / 120))
    
    @property
    def beat_interval_ms(self) -> float:
        """Get beat interval in milliseconds."""
        if self.features.tempo > 0:
            return 60000.0 / self.features.tempo
        return 500.0
    
    # Compatibility properties for TrackInfo-like access
    @property
    def track(self):
        """Compatibility shim for code expecting track.is_playing etc."""
        return self


class AudioAnalyzer:
    """
    Real-time audio analyzer that captures system audio via WASAPI loopback
    and extracts audio features using librosa and FFT analysis.
    """
    
    def __init__(self, 
                 buffer_size: int = 1024,
                 sample_rate: int = 44100,
                 device_index: Optional[int] = None):
        """
        Initialize the audio analyzer.
        
        Args:
            buffer_size: Audio buffer size (smaller = lower latency, higher CPU)
            sample_rate: Sample rate for audio capture
            device_index: Specific audio device index, or None for default loopback
        """
        self.buffer_size = buffer_size
        self.sample_rate = sample_rate
        self.device_index = device_index
        
        self._pyaudio: Optional[pyaudio.PyAudio] = None
        self._stream = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        self._data = AnalysisData()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[AnalysisData], None]] = []
        
        # Librosa-based onset detection (we accumulate audio for periodic analysis)
        self._audio_buffer = deque(maxlen=sample_rate * 2)  # 2 seconds of audio
        self._onset_envelope = None
        self._last_tempo_update = 0.0
        self._tempo_update_interval = 1.0  # Update tempo every second
        
        # Beat tracking state
        self._last_beat_time = 0.0
        self._beat_count = 0
        self._tempo_history = deque(maxlen=8)  # Rolling tempo estimates
        self._onset_history = deque(maxlen=16)  # Recent onset times for tempo calc
        self._current_tempo = 120.0  # Current estimated tempo
        
        # Onset detection state (uses normalized 0-1 values)
        self._prev_onset_strength = 0.0
        self._onset_cooldown = 0.0
        self._min_onset_interval = 0.08  # Minimum 80ms between onsets (faster response)
        
        # Adaptive energy scaling (auto-gain)
        self._max_rms_observed = 0.01  # Start with small value, will grow
        self._rms_decay = 0.9995  # Slowly decay max to adapt to quieter sections
        
        # Per-band adaptive scaling (each band normalized independently)
        self._max_bass = 0.01
        self._max_mid = 0.01
        self._max_high = 0.01
        self._band_decay = 0.999  # Decay rate for band maxes
        
        # Energy smoothing
        self._energy_history = deque(maxlen=10)
        self._bass_history = deque(maxlen=5)
        self._mid_history = deque(maxlen=5)
        self._high_history = deque(maxlen=5)
        
        # FFT setup
        self._fft_size = buffer_size
        self._freq_bins = None
        
    def add_callback(self, callback: Callable[[AnalysisData], None]) -> None:
        """Add a callback to be called when analysis data updates."""
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[AnalysisData], None]) -> None:
        """Remove a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def get_loopback_device(self) -> Optional[dict]:
        """Find the default WASAPI loopback device."""
        if not PYAUDIO_AVAILABLE:
            return None
            
        try:
            p = pyaudio.PyAudio()
            
            # Try to get WASAPI loopback device (PyAudioWPatch method)
            if hasattr(p, 'get_default_wasapi_loopback'):
                device = p.get_default_wasapi_loopback()
                p.terminate()
                return device
            
            # Fallback: search for loopback device manually
            wasapi_info = None
            for i in range(p.get_host_api_count()):
                info = p.get_host_api_info_by_index(i)
                if 'WASAPI' in info.get('name', ''):
                    wasapi_info = info
                    break
            
            if wasapi_info:
                # Find loopback device
                for i in range(p.get_device_count()):
                    dev = p.get_device_info_by_index(i)
                    if dev.get('hostApi') == wasapi_info.get('index'):
                        if dev.get('maxInputChannels', 0) > 0:
                            # Check if it's a loopback device (name contains output device name)
                            if 'loopback' in dev.get('name', '').lower():
                                p.terminate()
                                return dev
            
            p.terminate()
            return None
        except Exception as e:
            print(f"Error finding loopback device: {e}")
            return None
    
    def list_devices(self) -> list[dict]:
        """List available audio input devices."""
        devices = []
        if not PYAUDIO_AVAILABLE:
            return devices
            
        try:
            p = pyaudio.PyAudio()
            for i in range(p.get_device_count()):
                dev = p.get_device_info_by_index(i)
                if dev.get('maxInputChannels', 0) > 0:
                    devices.append({
                        'index': i,
                        'name': dev.get('name', 'Unknown'),
                        'channels': dev.get('maxInputChannels', 0),
                        'sample_rate': int(dev.get('defaultSampleRate', 44100))
                    })
            p.terminate()
        except Exception as e:
            print(f"Error listing devices: {e}")
        return devices
    
    def start(self) -> bool:
        """Start audio capture and analysis."""
        if self._running:
            return True
        
        if not PYAUDIO_AVAILABLE:
            print("PyAudio not available. Install with: pip install PyAudioWPatch")
            return False
        
        if not LIBROSA_AVAILABLE:
            print("Librosa not available. Install with: pip install librosa")
            return False
        
        try:
            self._pyaudio = pyaudio.PyAudio()
            
            # Find loopback device if not specified
            device = None
            if self.device_index is not None:
                device = self._pyaudio.get_device_info_by_index(self.device_index)
            else:
                # Try to get default WASAPI loopback
                if hasattr(self._pyaudio, 'get_default_wasapi_loopback'):
                    try:
                        device = self._pyaudio.get_default_wasapi_loopback()
                        print(f"Using WASAPI loopback: {device.get('name', 'Unknown')}")
                    except Exception:
                        pass
                
                if not device:
                    # Fall back to default input
                    device = self._pyaudio.get_default_input_device_info()
                    print(f"Using default input: {device.get('name', 'Unknown')}")
            
            if not device:
                print("No audio input device found")
                return False
            
            self.device_index = device.get('index')
            channels = min(device.get('maxInputChannels', 2), 2)
            self.sample_rate = int(device.get('defaultSampleRate', 44100))
            
            # Setup FFT frequency bins
            self._freq_bins = np.fft.rfftfreq(self._fft_size, 1.0 / self.sample_rate)
            
            # Open audio stream
            self._stream = self._pyaudio.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self.buffer_size,
                stream_callback=self._audio_callback
            )
            
            self._running = True
            self._stream.start_stream()
            
            # Start analysis thread
            self._thread = threading.Thread(target=self._analysis_loop, daemon=True)
            self._thread.start()
            
            return True
            
        except Exception as e:
            print(f"Failed to start audio analyzer: {e}")
            self._cleanup()
            return False
    
    def stop(self) -> None:
        """Stop audio capture and analysis."""
        self._running = False
        
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        
        self._cleanup()
    
    def _cleanup(self) -> None:
        """Clean up audio resources."""
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        
        if self._pyaudio:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
            self._pyaudio = None
    
    def _audio_callback(self, in_data, frame_count, time_info, status):
        """PyAudio callback - process incoming audio data."""
        if not self._running:
            return (None, pyaudio.paComplete)
        
        try:
            # Convert bytes to numpy array
            audio_data = np.frombuffer(in_data, dtype=np.float32)
            
            # Convert stereo to mono if needed
            if len(audio_data) > frame_count:
                audio_data = audio_data.reshape(-1, 2).mean(axis=1)
            
            # Process audio
            self._process_audio(audio_data)
            
        except Exception as e:
            # Don't print errors in callback to avoid spam
            pass
        
        return (None, pyaudio.paContinue)
    
    def _process_audio(self, audio_data: np.ndarray) -> None:
        """Process audio buffer and extract features."""
        current_time = time.time()
        
        # Ensure correct size
        if len(audio_data) != self.buffer_size:
            if len(audio_data) > self.buffer_size:
                audio_data = audio_data[:self.buffer_size]
            else:
                audio_data = np.pad(audio_data, (0, self.buffer_size - len(audio_data)))
        
        # Add to audio buffer for librosa analysis
        self._audio_buffer.extend(audio_data.tolist())
        
        # Calculate RMS energy first (needed for onset detection)
        rms = np.sqrt(np.mean(audio_data ** 2))
        
        # Update adaptive scaling - track max RMS with slow decay
        if rms > self._max_rms_observed:
            self._max_rms_observed = rms
        else:
            self._max_rms_observed *= self._rms_decay  # Slowly decay to adapt to quieter sections
        self._max_rms_observed = max(0.01, self._max_rms_observed)  # Prevent division by zero
        
        # Real-time onset detection using normalized energy derivative
        onset_detected = False
        # Use normalized RMS for onset detection (0-1 range)
        normalized_rms = rms / self._max_rms_observed
        if current_time > self._onset_cooldown:
            # Detect onset when normalized energy rises sharply
            if normalized_rms > 0.3 and normalized_rms > self._prev_onset_strength * 1.3:
                onset_detected = True
                self._onset_history.append(current_time)
                self._onset_cooldown = current_time + self._min_onset_interval
        self._prev_onset_strength = normalized_rms * 0.7 + self._prev_onset_strength * 0.3  # Smooth
        
        # Beat detection based on tempo prediction
        beat_detected = False
        if self._current_tempo > 0:
            beat_interval = 60.0 / self._current_tempo
            time_since_beat = current_time - self._last_beat_time
            
            # Predict beat based on tempo, with onset confirmation
            if time_since_beat >= beat_interval * 0.9:
                if onset_detected or time_since_beat >= beat_interval:
                    beat_detected = True
                    self._last_beat_time = current_time
                    self._beat_count += 1
        
        # Periodically update tempo using librosa (expensive, so do less often)
        if current_time - self._last_tempo_update > self._tempo_update_interval:
            self._update_tempo_librosa()
            self._last_tempo_update = current_time
        self._energy_history.append(rms)
        
        # FFT for frequency analysis
        if len(audio_data) >= self._fft_size:
            # Apply window function to reduce spectral leakage
            windowed = audio_data[:self._fft_size] * np.hanning(self._fft_size)
            fft_data = np.abs(np.fft.rfft(windowed))
            
            # Calculate raw frequency band power
            bass_raw = self._get_band_energy_normalized(fft_data, 20, 250)
            mid_raw = self._get_band_energy_normalized(fft_data, 250, 4000)
            high_raw = self._get_band_energy_normalized(fft_data, 4000, 16000)
            
            # Normalize each band independently for balanced display
            bass, mid, high = self._normalize_bands(bass_raw, mid_raw, high_raw)
            
            self._bass_history.append(bass)
            self._mid_history.append(mid)
            self._high_history.append(high)
        
        # Update features with smoothing
        with self._lock:
            features = self._data.features
            
            # Smooth energy with adaptive normalization
            avg_energy = np.mean(list(self._energy_history)) if self._energy_history else 0
            features.rms = rms
            # Normalize energy relative to observed max (adaptive auto-gain)
            normalized_energy = avg_energy / self._max_rms_observed
            features.energy = min(1.0, normalized_energy * 1.2)  # Slight boost, cap at 1.0
            
            # Smooth frequency bands
            features.bass = np.mean(list(self._bass_history)) if self._bass_history else 0
            features.mid = np.mean(list(self._mid_history)) if self._mid_history else 0
            features.high = np.mean(list(self._high_history)) if self._high_history else 0
            
            # Tempo (use current estimate with smoothing)
            features.tempo = self._current_tempo
            
            features.beat_detected = beat_detected
            features.onset_detected = onset_detected
            features.time_since_beat = current_time - self._last_beat_time
            
            # Estimate danceability from beat regularity and bass energy
            if len(self._onset_history) >= 3:
                intervals = np.diff(list(self._onset_history))
                if len(intervals) > 0 and np.mean(intervals) > 0:
                    # Regularity: how consistent are the intervals (lower std = more regular)
                    coefficient_of_variation = np.std(intervals) / np.mean(intervals)
                    regularity = 1.0 - min(1.0, coefficient_of_variation)
                    # Combine regularity with bass presence for danceability
                    bass_factor = features.bass * 0.3
                    features.danceability = min(1.0, regularity * 0.7 + bass_factor + 0.1)
            else:
                # Fallback: estimate from bass and energy
                features.danceability = min(1.0, features.bass * 0.5 + features.energy * 0.3 + 0.2)
            
            # Estimate valence from frequency balance (brighter = happier)
            if features.bass + features.mid + features.high > 0:
                brightness = (features.mid + features.high * 2) / (features.bass + features.mid + features.high + 0.001)
                features.valence = min(1.0, brightness)
            
            # Update beat position
            if features.tempo > 0:
                beat_duration = 60.0 / features.tempo
                self._data.beat_position = min(1.0, features.time_since_beat / beat_duration)
                
                # Bar position (assuming 4/4)
                beats_per_bar = 4
                bar_beat = self._beat_count % beats_per_bar
                self._data.bar_position = (bar_beat + self._data.beat_position) / beats_per_bar
                
                self._data.estimated_beat = self._beat_count
                self._data.estimated_bar = self._beat_count // beats_per_bar
            
            # Section intensity based on energy and beat
            beat_pulse = 1.0 - self._data.beat_position if beat_detected else 0
            self._data.section_intensity = features.energy * 0.7 + beat_pulse * 0.3
    
    def _update_tempo_librosa(self) -> None:
        """Update tempo estimate using librosa beat tracking."""
        if len(self._audio_buffer) < self.sample_rate:  # Need at least 1 second
            return
        
        try:
            # Convert buffer to numpy array
            audio_array = np.array(list(self._audio_buffer), dtype=np.float32)
            
            # Use librosa to estimate tempo
            tempo, _ = librosa.beat.beat_track(
                y=audio_array,
                sr=self.sample_rate,
                units='time'
            )
            
            # librosa may return an array, get scalar
            if hasattr(tempo, '__len__'):
                tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
            else:
                tempo = float(tempo)
            
            # Sanity check tempo range (60-200 BPM typical)
            if 60 <= tempo <= 200:
                self._tempo_history.append(tempo)
                # Smooth tempo updates
                if self._tempo_history:
                    self._current_tempo = float(np.median(list(self._tempo_history)))
        except Exception:
            # If librosa fails, fall back to onset-based tempo estimation
            if len(self._onset_history) >= 4:
                intervals = np.diff(list(self._onset_history))
                if len(intervals) > 0:
                    avg_interval = np.mean(intervals)
                    if avg_interval > 0:
                        estimated_tempo = 60.0 / avg_interval
                        if 60 <= estimated_tempo <= 200:
                            self._current_tempo = estimated_tempo
    
    def _get_band_energy_normalized(self, fft_data: np.ndarray, low_freq: float, high_freq: float) -> float:
        """Get energy in a frequency band from FFT data (raw, not normalized)."""
        if self._freq_bins is None or len(fft_data) == 0:
            return 0.0
        
        # Find indices for frequency range
        low_idx = np.searchsorted(self._freq_bins, low_freq)
        high_idx = np.searchsorted(self._freq_bins, high_freq)
        
        if high_idx <= low_idx:
            return 0.0
        
        # Use sum of squared magnitudes (power) for better energy representation
        band_power = np.sum(fft_data[low_idx:high_idx] ** 2)
        return float(band_power)
    
    def _normalize_bands(self, bass_raw: float, mid_raw: float, high_raw: float) -> tuple[float, float, float]:
        """Normalize frequency bands with adaptive per-band scaling."""
        # Update max values with decay
        if bass_raw > self._max_bass:
            self._max_bass = bass_raw
        else:
            self._max_bass = max(0.01, self._max_bass * self._band_decay)
        
        if mid_raw > self._max_mid:
            self._max_mid = mid_raw
        else:
            self._max_mid = max(0.01, self._max_mid * self._band_decay)
            
        if high_raw > self._max_high:
            self._max_high = high_raw
        else:
            self._max_high = max(0.01, self._max_high * self._band_decay)
        
        # Normalize each band independently
        bass = min(1.0, (bass_raw / self._max_bass) * 1.1)
        mid = min(1.0, (mid_raw / self._max_mid) * 1.1)
        high = min(1.0, (high_raw / self._max_high) * 1.1)
        
        return bass, mid, high
    
    def _analysis_loop(self) -> None:
        """Main analysis loop - notifies callbacks."""
        while self._running:
            # Get current data
            with self._lock:
                data = AnalysisData(
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
                    estimated_bar=self._data.estimated_bar
                )
                # Reset beat detected flag after reading
                self._data.features.beat_detected = False
                self._data.features.onset_detected = False
            
            # Notify callbacks
            for callback in self._callbacks:
                try:
                    callback(data)
                except Exception:
                    pass
            
            time.sleep(0.025)  # 40 Hz update rate
    
    def get_data(self) -> AnalysisData:
        """Get current analysis data."""
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
                estimated_bar=self._data.estimated_bar
            )


class SimulatedAudioAnalyzer:
    """Simulated audio analyzer for testing without audio hardware."""
    
    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._data = AnalysisData()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[AnalysisData], None]] = []
        self._start_time = 0.0
        self._beat_count = 0
    
    def add_callback(self, callback: Callable[[AnalysisData], None]) -> None:
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[AnalysisData], None]) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def start(self) -> bool:
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
        return True
    
    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
    
    def get_data(self) -> AnalysisData:
        with self._lock:
            return self._data
    
    def _simulation_loop(self) -> None:
        import math
        
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
                self._data.features.tempo = tempo
                self._data.features.beat_detected = beat_detected
                self._data.features.time_since_beat = time_since_beat
                
                # Simulate frequency bands
                self._data.features.bass = 0.6 + 0.3 * math.sin(elapsed * 0.5)
                self._data.features.mid = 0.5 + 0.2 * math.sin(elapsed * 0.7)
                self._data.features.high = 0.4 + 0.2 * math.sin(elapsed * 1.1)
                
                # Beat position
                self._data.beat_position = time_since_beat / beat_interval
                self._data.bar_position = ((self._beat_count % 4) + self._data.beat_position) / 4
                self._data.estimated_beat = self._beat_count
                self._data.estimated_bar = self._beat_count // 4
                self._data.section_intensity = base_energy + beat_pulse * 0.5
            
            # Notify callbacks
            for callback in self._callbacks:
                try:
                    callback(self._data)
                except Exception:
                    pass
            
            time.sleep(0.025)  # 40 Hz


def create_audio_analyzer(simulate: bool = False, device_index: Optional[int] = None):
    """
    Factory function to create an audio analyzer.
    
    Args:
        simulate: Use simulated analyzer (for testing)
        device_index: Specific audio device index, or None for auto-detect
    
    Returns:
        AudioAnalyzer or SimulatedAudioAnalyzer
    """
    if simulate:
        return SimulatedAudioAnalyzer()
    
    return AudioAnalyzer(device_index=device_index)
