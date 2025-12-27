"""
Real-time audio analyzer that captures system audio and extracts audio features.
Supports both WASAPI loopback (system audio) and microphone input.
Uses PyAudioWPatch for WASAPI loopback capture and aubio for real-time beat/tempo detection.
"""
import logging
import threading
import time
import numpy as np
from typing import Optional, Callable
from dataclasses import dataclass, field
from collections import deque
from typing import List, Tuple

from config import AudioInputMode

logger = logging.getLogger(__name__)

try:
    import pyaudiowpatch as pyaudio
    PYAUDIO_AVAILABLE = True
    PYAUDIO_WPATCH = True
except ImportError:
    PYAUDIO_WPATCH = False
    try:
        import pyaudio
        PYAUDIO_AVAILABLE = True
    except ImportError:
        PYAUDIO_AVAILABLE = False

try:
    import aubio
    AUBIO_AVAILABLE = True
except ImportError:
    AUBIO_AVAILABLE = False

# Fallback to librosa if aubio not available
try:
    import librosa
    LIBROSA_AVAILABLE = True
except ImportError:
    LIBROSA_AVAILABLE = False

if not AUBIO_AVAILABLE and not LIBROSA_AVAILABLE:
    logger.warning("Neither aubio nor librosa available - beat/tempo detection will be disabled")

try:
    from media_info import MediaInfoProvider, MediaInfo
    MEDIA_INFO_AVAILABLE = True
except ImportError:
    MEDIA_INFO_AVAILABLE = False


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
    
    # Album art colors (RGB tuples extracted from cover art)
    album_colors: List[Tuple[int, int, int]] = field(default_factory=list)
    
    # Waveform data for visualization (downsampled to ~100 points)
    waveform: List[float] = field(default_factory=list)
    
    # Frequency spectrum for visualization (bass/mid/high bands, ~32 bins)
    spectrum: List[float] = field(default_factory=list)
    
    # Onset strength history for beat detection visualization
    onset_history: List[float] = field(default_factory=list)
    
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
    Real-time audio analyzer that captures audio and extracts features.
    Supports both system audio loopback (WASAPI) and microphone input.
    """
    
    def __init__(self, 
                 buffer_size: int = 1024,
                 sample_rate: int = 44100,
                 device_index: Optional[int] = None,
                 input_mode: AudioInputMode = AudioInputMode.AUTO):
        """
        Initialize the audio analyzer.
        
        Args:
            buffer_size: Audio buffer size (smaller = lower latency, higher CPU)
            sample_rate: Sample rate for audio capture
            device_index: Specific audio device index, or None for auto-detect
            input_mode: Audio input source (loopback, microphone, or auto)
        """
        self.buffer_size = buffer_size
        self.sample_rate = sample_rate
        self.device_index = device_index
        self.input_mode = input_mode
        self._actual_input_mode: Optional[AudioInputMode] = None  # What we actually used
        
        self._pyaudio: Optional[pyaudio.PyAudio] = None
        self._stream = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        self._data = AnalysisData()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[AnalysisData], None]] = []
        
        # Aubio-based real-time tempo and onset detection
        self._aubio_tempo = None
        self._aubio_onset = None
        if AUBIO_AVAILABLE:
            # hop_size must match buffer_size since that's what we receive from audio callback
            # win_size should be 2x hop_size for good frequency resolution
            hop_size = buffer_size
            win_size = buffer_size * 2
            self._aubio_tempo = aubio.tempo("default", win_size, hop_size, sample_rate)
            self._aubio_onset = aubio.onset("default", win_size, hop_size, sample_rate)
            logger.info(f"Aubio initialized: method=default, hop_size={hop_size}, win_size={win_size}, sr={sample_rate}")
        
        # Beat tracking state
        self._last_beat_time = 0.0
        self._beat_count = 0
        self._tempo_history = deque(maxlen=32)  # Rolling tempo estimates (increased for stability)
        self._onset_history = deque(maxlen=32)  # Recent onset times
        self._current_tempo = 120.0  # Current estimated tempo
        self._raw_tempo_history = deque(maxlen=64)  # Raw BPM values for octave error correction
        self._beat_intervals: deque[float] = deque(maxlen=16)  # Recent beat intervals for validation
        
        # Onset detection state
        self._prev_onset_strength = 0.0
        self._onset_strength_history: deque[float] = deque(maxlen=64)  # For visualization
        
        # Adaptive energy scaling (auto-gain)
        self._max_rms_observed = 0.01  # Start with small value, will grow
        self._rms_decay = 0.9995  # Slowly decay max to adapt to quieter sections
        
        # Per-band adaptive scaling (each band normalized independently)
        self._max_bass = 0.01
        self._max_mid = 0.01
        self._max_high = 0.01
        self._band_decay = 0.999  # Decay rate for band maxes
        
        # Media info provider (for track name/artist display)
        self._media_info_provider = None
        if MEDIA_INFO_AVAILABLE:
            try:
                self._media_info_provider = MediaInfoProvider()
            except Exception:
                pass
        
        # Current track info (updated by analysis loop)
        self._track_name = "System Audio"
        self._artist_name = ""
        self._is_playing = True
        self._album_colors: List[Tuple[int, int, int]] = []
        
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
    
    def list_devices(self, input_only: bool = True) -> list[dict]:
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
                        'sample_rate': int(dev.get('defaultSampleRate', 44100)),
                        'is_loopback': 'loopback' in str(dev.get('name', '')).lower()
                    })
            p.terminate()
        except Exception as e:
            logger.error(f"Error listing devices: {e}")
        return devices
    
    def list_microphones(self) -> list[dict]:
        """List available microphone devices (excludes loopback devices)."""
        all_devices = self.list_devices()
        return [d for d in all_devices if not d.get('is_loopback', False)]
    
    def get_default_microphone(self) -> Optional[dict]:
        """Get the default microphone device."""
        if not PYAUDIO_AVAILABLE:
            return None
        
        try:
            p = pyaudio.PyAudio()
            default_input = p.get_default_input_device_info()
            p.terminate()
            
            # Check if it's a loopback device
            name = str(default_input.get('name', ''))
            if 'loopback' in name.lower():
                # Try to find a non-loopback device
                mics = self.list_microphones()
                if mics:
                    return mics[0]
                return None
            
            return {
                'index': default_input.get('index'),
                'name': name,
                'channels': default_input.get('maxInputChannels', 0),
                'sample_rate': int(default_input.get('defaultSampleRate', 44100)),
                'is_loopback': False
            }
        except Exception as e:
            logger.error(f"Error getting default microphone: {e}")
            return None
    
    def get_input_mode_used(self) -> Optional[AudioInputMode]:
        """Get the actual input mode that was used after start()."""
        return self._actual_input_mode
    
    def start(self) -> bool:
        """Start audio capture and analysis."""
        if self._running:
            return True
        
        if not PYAUDIO_AVAILABLE:
            print("PyAudio not available. Install with: pip install PyAudioWPatch")
            return False
        
        if not AUBIO_AVAILABLE and not LIBROSA_AVAILABLE:
            print("Neither aubio nor librosa available. Install with: pip install aubio (or librosa as fallback)")
            return False
        
        try:
            self._pyaudio = pyaudio.PyAudio()
            
            # Select audio device based on input mode
            device = None
            
            if self.device_index is not None:
                # Explicit device index specified
                device = self._pyaudio.get_device_info_by_index(self.device_index)
                device_name = device.get('name', 'Unknown')
                is_loopback = 'loopback' in str(device_name).lower()
                self._actual_input_mode = AudioInputMode.LOOPBACK if is_loopback else AudioInputMode.MICROPHONE
                logger.info(f"Using specified device: {device_name}")
            
            elif self.input_mode == AudioInputMode.MICROPHONE:
                # Microphone mode - use default input (not loopback)
                device = self._pyaudio.get_default_input_device_info()
                device_name = str(device.get('name', ''))
                
                # If default is a loopback, try to find a real microphone
                if 'loopback' in device_name.lower():
                    logger.info("Default input is loopback, searching for microphone...")
                    for i in range(self._pyaudio.get_device_count()):
                        dev = self._pyaudio.get_device_info_by_index(i)
                        dev_name = str(dev.get('name', ''))
                        if dev.get('maxInputChannels', 0) > 0 and 'loopback' not in dev_name.lower():
                            device = dev
                            logger.info(f"Found microphone: {dev_name}")
                            break
                
                if device:
                    logger.info(f"Using microphone: {device.get('name', 'Unknown')}")
                    self._actual_input_mode = AudioInputMode.MICROPHONE
                else:
                    logger.warning("No microphone found, falling back to default input")
                    device = self._pyaudio.get_default_input_device_info()
                    self._actual_input_mode = AudioInputMode.MICROPHONE
            
            elif self.input_mode == AudioInputMode.LOOPBACK:
                # Loopback mode - try WASAPI loopback first
                if PYAUDIO_WPATCH and hasattr(self._pyaudio, 'get_default_wasapi_loopback'):
                    try:
                        device = self._pyaudio.get_default_wasapi_loopback()
                        logger.info(f"Using WASAPI loopback: {device.get('name', 'Unknown')}")
                        self._actual_input_mode = AudioInputMode.LOOPBACK
                    except Exception as e:
                        logger.warning(f"WASAPI loopback not available: {e}")
                
                if not device:
                    logger.warning("Loopback not available, falling back to default input")
                    device = self._pyaudio.get_default_input_device_info()
                    self._actual_input_mode = AudioInputMode.MICROPHONE
            
            else:  # AUTO mode
                # Try loopback first, fall back to microphone
                if PYAUDIO_WPATCH and hasattr(self._pyaudio, 'get_default_wasapi_loopback'):
                    try:
                        device = self._pyaudio.get_default_wasapi_loopback()
                        logger.info(f"Using WASAPI loopback: {device.get('name', 'Unknown')}")
                        self._actual_input_mode = AudioInputMode.LOOPBACK
                    except Exception:
                        pass
                
                if not device:
                    device = self._pyaudio.get_default_input_device_info()
                    logger.info(f"Using default input: {device.get('name', 'Unknown')}")
                    self._actual_input_mode = AudioInputMode.MICROPHONE
            
            if not device:
                logger.error("No audio input device found")
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
            
            # Start media info provider (for track name display)
            if self._media_info_provider:
                self._media_info_provider.start()
            
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
        
        if self._media_info_provider:
            self._media_info_provider.stop()
        
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
        
        # Calculate RMS energy first
        rms = np.sqrt(np.mean(audio_data ** 2))
        
        # Update adaptive scaling - track max RMS with slow decay
        if rms > self._max_rms_observed:
            self._max_rms_observed = rms
        else:
            self._max_rms_observed *= self._rms_decay  # Slowly decay to adapt to quieter sections
        self._max_rms_observed = max(0.01, self._max_rms_observed)  # Prevent division by zero
        
        # Aubio-based beat and onset detection
        beat_detected = False
        onset_detected = False
        
        if self._aubio_tempo is not None:
            # Feed audio to aubio tempo detector (expects float32)
            signal = audio_data.astype(np.float32)
            
            # aubio.tempo returns 1 if beat detected, 0 otherwise
            is_beat = self._aubio_tempo(signal)
            if is_beat:
                beat_detected = True
                
                # Track beat intervals to calculate BPM from actual detected beats
                if self._last_beat_time > 0:
                    interval = current_time - self._last_beat_time
                    if 0.2 < interval < 2.0:  # Reasonable interval (30-300 BPM range)
                        self._beat_intervals.append(interval)
                
                self._last_beat_time = current_time
                self._beat_count += 1
                
                # Calculate BPM from actual beat intervals (more accurate than aubio's get_bpm)
                if len(self._beat_intervals) >= 2:
                    # Use median of recent intervals for stability
                    median_interval = float(np.median(list(self._beat_intervals)))
                    interval_bpm = 60.0 / median_interval
                    
                    # Apply octave error correction for extreme values
                    corrected_bpm = self._correct_tempo_octave(interval_bpm)
                    
                    self._tempo_history.append(corrected_bpm)
                    if len(self._tempo_history) >= 4:
                        self._current_tempo = float(np.median(list(self._tempo_history)))
            
            # Onset detection
            if self._aubio_onset is not None:
                is_onset = self._aubio_onset(signal)
                if is_onset:
                    onset_detected = True
                    self._onset_history.append(current_time)
                
                # Get onset strength for visualization
                onset_strength = self._aubio_onset.get_descriptor()
                # Normalize to 0-1 range
                normalized_strength = min(1.0, float(onset_strength) / 10.0)
                self._onset_strength_history.append(normalized_strength)
                self._prev_onset_strength = normalized_strength
        else:
            # Fallback: simple energy-based detection if aubio not available
            normalized_rms = rms / self._max_rms_observed
            self._onset_strength_history.append(normalized_rms)
            self._prev_onset_strength = normalized_rms
        
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
                # Use modulo to wrap beat position smoothly (handles missed beats)
                time_in_beat = features.time_since_beat % beat_duration
                self._data.beat_position = time_in_beat / beat_duration
                
                # Bar position (assuming 4/4)
                beats_per_bar = 4
                bar_beat = self._beat_count % beats_per_bar
                self._data.bar_position = (bar_beat + self._data.beat_position) / beats_per_bar
                
                self._data.estimated_beat = self._beat_count
                self._data.estimated_bar = self._beat_count // beats_per_bar
            
            # Section intensity based on energy and beat
            beat_pulse = 1.0 - self._data.beat_position if beat_detected else 0
            self._data.section_intensity = features.energy * 0.7 + beat_pulse * 0.3
            
            # Generate waveform data for visualization (downsample to ~100 points)
            self._data.waveform = self._get_waveform_display(audio_data)
            
            # Generate spectrum data for visualization
            self._data.spectrum = self._get_spectrum_display(audio_data)
            
            # Store onset strength history for beat visualization
            self._data.onset_history = list(self._onset_strength_history)
    
    def _get_waveform_display(self, audio_data: np.ndarray, num_points: int = 100) -> List[float]:
        """
        Downsample audio data to a fixed number of points for waveform display.
        Returns values in range [-1, 1].
        """
        if len(audio_data) == 0:
            return [0.0] * num_points
        
        # Downsample by taking max absolute value in each chunk (envelope)
        chunk_size = max(1, len(audio_data) // num_points)
        waveform = []
        
        for i in range(num_points):
            start = i * chunk_size
            end = min(start + chunk_size, len(audio_data))
            if start < len(audio_data):
                chunk = audio_data[start:end]
                # Use max absolute value for a more visible waveform
                max_val = float(np.max(np.abs(chunk)))
                # Apply some compression for better visibility
                if max_val > 0:
                    # Normalize and apply slight compression
                    normalized = min(1.0, max_val / max(0.01, self._max_rms_observed * 3))
                    waveform.append(normalized)
                else:
                    waveform.append(0.0)
            else:
                waveform.append(0.0)
        
        return waveform
    
    def _get_spectrum_display(self, audio_data: np.ndarray, num_bands: int = 32) -> List[float]:
        """
        Get frequency spectrum for visualization.
        Returns num_bands values in range [0, 1] representing energy in each frequency band.
        Bands are logarithmically spaced to match human perception.
        """
        if len(audio_data) < self._fft_size:
            return [0.0] * num_bands
        
        try:
            # Apply window and compute FFT
            windowed = audio_data[:self._fft_size] * np.hanning(self._fft_size)
            fft_data = np.abs(np.fft.rfft(windowed))
            
            # Logarithmically spaced frequency bands (20Hz to 16kHz)
            min_freq = 20
            max_freq = 16000
            
            # Create logarithmic frequency bands
            freq_bands = np.logspace(np.log10(min_freq), np.log10(max_freq), num_bands + 1)
            
            spectrum = []
            for i in range(num_bands):
                low_freq = freq_bands[i]
                high_freq = freq_bands[i + 1]
                
                # Find indices for this frequency range
                low_idx = int(low_freq * self._fft_size / self.sample_rate)
                high_idx = int(high_freq * self._fft_size / self.sample_rate)
                
                low_idx = max(0, min(low_idx, len(fft_data) - 1))
                high_idx = max(low_idx + 1, min(high_idx, len(fft_data)))
                
                # Get energy in this band
                if high_idx > low_idx:
                    band_energy = np.mean(fft_data[low_idx:high_idx] ** 2)
                    # Normalize with adaptive scaling
                    normalized = min(1.0, band_energy / max(0.001, self._max_rms_observed ** 2 * 10))
                    spectrum.append(float(normalized))
                else:
                    spectrum.append(0.0)
            
            return spectrum
        except Exception:
            return [0.0] * num_bands
    

    
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
    
    def _correct_tempo_octave(self, raw_bpm: float) -> float:
        """
        Correct octave errors in tempo estimation.
        
        Aubio's beat tracker can sometimes detect at half or double the actual tempo,
        especially when there are strong off-beat elements (like hi-hats or background drums
        on every second beat). This function corrects only extreme cases.
        
        Args:
            raw_bpm: The raw BPM value from aubio
            
        Returns:
            Corrected BPM value
        """
        bpm = raw_bpm
        
        # Only correct extreme values outside normal music range (60-200 BPM)
        # This is conservative to avoid incorrectly "correcting" valid tempos
        while bpm < 60:
            bpm *= 2
        while bpm > 200:
            bpm /= 2
        
        return bpm
    
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
        loop_count = 0
        while self._running:
            loop_count += 1
            
            # Get media info if available
            track_name = "System Audio"
            artist_name = ""
            media_is_playing = True
            
            album_colors: List[Tuple[int, int, int]] = []
            
            if self._media_info_provider:
                try:
                    media_info = self._media_info_provider.get_info()
                    if media_info.title:
                        track_name = media_info.title
                        artist_name = media_info.artist
                        media_is_playing = media_info.is_playing
                        album_colors = media_info.colors
                    
                    # Log media info periodically (every 200 loops = ~5 seconds)
                    if loop_count % 200 == 1:
                        logger.debug(f"Media info: title='{media_info.title}', artist='{media_info.artist}', "
                                    f"colors={len(album_colors)}, playing={media_info.is_playing}")
                except Exception as e:
                    if loop_count % 200 == 1:
                        logger.warning(f"Failed to get media info: {e}")
            
            # Store track info for get_data() access
            self._track_name = track_name
            self._artist_name = artist_name
            self._is_playing = media_is_playing
            self._album_colors = album_colors
            
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
                    estimated_bar=self._data.estimated_bar,
                    track_name=track_name,
                    artist_name=artist_name,
                    is_playing=media_is_playing,
                    album_colors=album_colors
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
                estimated_bar=self._data.estimated_bar,
                track_name=self._track_name,
                artist_name=self._artist_name,
                is_playing=self._is_playing,
                album_colors=list(self._album_colors)
            )


def create_audio_analyzer(
    simulate: bool = False, 
    device_index: Optional[int] = None,
    input_mode: AudioInputMode = AudioInputMode.AUTO
):
    """
    Factory function to create an audio analyzer.
    
    Args:
        simulate: Use simulated analyzer (for testing)
        device_index: Specific audio device index, or None for auto-detect
        input_mode: Audio input source (loopback, microphone, or auto)
    
    Returns:
        AudioAnalyzer or SimulatedAudioAnalyzer
    """
    if simulate:
        from simulators import SimulatedAudioAnalyzer
        return SimulatedAudioAnalyzer()
    
    return AudioAnalyzer(device_index=device_index, input_mode=input_mode)
