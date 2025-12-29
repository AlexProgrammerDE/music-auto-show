"""
Real-time audio analyzer that captures system audio and extracts audio features.
Supports both WASAPI loopback (system audio) and microphone input.
Uses PyAudioWPatch for WASAPI loopback capture and madmom for neural network beat/tempo detection.

Threading model:
- PyAudio callback thread: Captures audio, does lightweight FFT processing
- Madmom worker thread: Runs heavy neural network beat detection asynchronously
- Main thread: Reads analysis results without blocking
"""
import logging
import threading
import queue
import time
import numpy as np
from typing import Optional, Callable
from dataclasses import dataclass, field
from collections import deque
from typing import List, Tuple
from concurrent.futures import ThreadPoolExecutor, Future

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

# Madmom for neural network beat tracking (RNN + DBN)
try:
    from madmom.features.beats import RNNBeatProcessor, DBNBeatTrackingProcessor
    from madmom.audio.signal import Signal
    MADMOM_AVAILABLE = True
    MADMOM_ERROR = None
except ImportError as e:
    MADMOM_AVAILABLE = False
    MADMOM_ERROR = f"madmom not available (ImportError: {e})"
    logger.error(MADMOM_ERROR)
except Exception as e:
    # Catch other exceptions (e.g., pkg_resources issues in Python 3.13+)
    MADMOM_AVAILABLE = False
    MADMOM_ERROR = f"madmom failed to load ({type(e).__name__}: {e})"
    logger.error(MADMOM_ERROR)

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
    tempo: float = 0.0   # BPM (0 = no music detected)
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
        
        # Madmom neural network beat tracking (RNN + DBN)
        # Runs in a separate thread to avoid blocking audio processing
        self._madmom_beat_processor = None
        self._madmom_dbn_processor = None
        self._madmom_fps = 100  # Frames per second for madmom processing
        self._madmom_process_interval = 2.0  # Process every N seconds
        self._madmom_last_process_time = 0.0
        self._madmom_audio_accumulator: list[float] = []  # Accumulate audio for batch processing
        self._madmom_lock = threading.Lock()  # Protects audio accumulator
        self._madmom_executor: Optional[ThreadPoolExecutor] = None  # Thread pool for async processing
        self._madmom_future: Optional[Future] = None  # Current processing task
        self._madmom_processing = False  # Flag to prevent overlapping processing
        if MADMOM_AVAILABLE:
            try:
                # RNNBeatProcessor processes full audio signals and outputs beat activations
                self._madmom_beat_processor = RNNBeatProcessor(fps=self._madmom_fps)
                # DBNBeatTrackingProcessor decodes activations into beat times
                self._madmom_dbn_processor = DBNBeatTrackingProcessor(
                    min_bpm=60,
                    max_bpm=200,
                    fps=self._madmom_fps
                )
                # Create thread pool for async madmom processing (single worker to serialize)
                self._madmom_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="madmom")
                logger.info(f"Madmom beat tracker initialized: fps={self._madmom_fps}, async=True")
            except Exception as e:
                logger.warning(f"Failed to initialize madmom processors: {e}")
                self._madmom_beat_processor = None
                self._madmom_dbn_processor = None
        
        # Beat tracking state
        self._last_beat_time = 0.0
        self._beat_count = 0
        self._onset_history: deque[float] = deque(maxlen=32)  # Recent onset times
        self._current_tempo = 0.0  # Current estimated tempo (0 until detected)
        
        # Beat activation history for visualization
        self._beat_activations: deque[float] = deque(maxlen=self._madmom_fps * 4)  # 4 sec of activations
        self._tempo_history: deque[float] = deque(maxlen=16)  # Recent tempo estimates
        
        # Onset detection state (derived from beat activations)
        self._prev_onset_strength = 0.0
        self._onset_strength_history: deque[float] = deque(maxlen=64)  # For visualization
        self._onset_threshold = 0.3  # Threshold for onset detection from activations
        
        # Audio gain (sensitivity multiplier, can be adjusted via set_gain())
        self._gain = 1.0
        
        # Adaptive max tracking for normalization (with very slow decay)
        # These represent "what is loud for this audio source" and decay very slowly
        self._max_rms = 0.01
        self._max_bass = 0.01
        self._max_mid = 0.01
        self._max_high = 0.01
        self._band_decay = 0.99995  # Very slow decay (~30 sec to halve)
        
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
    
    def set_gain(self, gain: float) -> None:
        """Set the audio input gain/sensitivity multiplier (0.1 to 5.0)."""
        self._gain = max(0.1, min(5.0, gain))
    
    def get_gain(self) -> float:
        """Get the current audio input gain."""
        return self._gain
    
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
            logger.error(f"Error finding loopback device: {e}")
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
            logger.error("PyAudio not available. Install with: pip install PyAudioWPatch")
            return False
        
        if not MADMOM_AVAILABLE:
            logger.error(f"madmom is required for beat/tempo detection. {MADMOM_ERROR}")
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
            logger.error(f"Failed to start audio analyzer: {e}")
            self._cleanup()
            return False
    
    def stop(self) -> None:
        """Stop audio capture and analysis."""
        self._running = False
        
        if self._media_info_provider:
            self._media_info_provider.stop()
        
        # Shutdown madmom executor
        if self._madmom_executor:
            self._madmom_executor.shutdown(wait=False)
            self._madmom_executor = None
        
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
        
        # Beat and onset detection
        beat_detected = False
        onset_detected = False
        
        # Accumulate audio for madmom processing (thread-safe)
        with self._madmom_lock:
            self._madmom_audio_accumulator.extend(audio_data.tolist())
        
        # Schedule async madmom processing periodically
        if (self._madmom_beat_processor is not None and 
            self._madmom_executor is not None and
            not self._madmom_processing and
            current_time - self._madmom_last_process_time >= self._madmom_process_interval):
            
            with self._madmom_lock:
                if len(self._madmom_audio_accumulator) >= self.sample_rate * 2:  # Need at least 2 seconds
                    # Copy audio data for async processing
                    audio_copy = self._madmom_audio_accumulator.copy()
                    self._madmom_processing = True
                    self._madmom_last_process_time = current_time
                    # Submit to thread pool (non-blocking)
                    self._madmom_future = self._madmom_executor.submit(
                        self._process_madmom_beat_detection_async, audio_copy
                    )
        
        # Beat detection: use madmom activations or predict based on tempo
        if self._current_tempo > 0:
            beat_interval = 60.0 / self._current_tempo
            time_since_beat = current_time - self._last_beat_time
            
            # Check if madmom detected a beat, or predict based on tempo
            if time_since_beat >= beat_interval * 0.95:  # Small tolerance for timing
                beat_detected = True
                self._last_beat_time = current_time
                self._beat_count += 1
        
        # Onset detection from beat activations (madmom provides this)
        if self._beat_activations:
            # Use the latest activation value for onset strength
            onset_strength = self._beat_activations[-1] if self._beat_activations else 0.0
            normalized_strength = min(1.0, float(onset_strength))
            self._onset_strength_history.append(normalized_strength)
            self._prev_onset_strength = normalized_strength
            
            # Detect onset when activation exceeds threshold
            if normalized_strength > self._onset_threshold:
                onset_detected = True
                self._onset_history.append(current_time)
        else:
            # Fallback: simple energy-based detection if madmom not processing yet
            normalized_rms = min(1.0, (rms / max(0.01, self._max_rms)) * self._gain)
            self._onset_strength_history.append(normalized_rms)
            self._prev_onset_strength = normalized_rms
        
        # Update adaptive max for energy with decay
        if rms > self._max_rms:
            self._max_rms = rms
        else:
            self._max_rms = max(0.01, self._max_rms * self._band_decay)
        
        # Store raw RMS in history (normalization and gain applied at read time)
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
            
            # Check if we have meaningful audio
            total_raw = bass_raw + mid_raw + high_raw
            if total_raw < 0.0001:
                # Silent - reset adaptive scaling
                self._max_bass = 0.01
                self._max_mid = 0.01
                self._max_high = 0.01
                self._bass_history.clear()
                self._mid_history.clear()
                self._high_history.clear()
            else:
                # Update adaptive max values (only grow, slow decay)
                # This establishes a reference level, not a per-frame normalization
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
                
                # Store raw values - normalization and gain applied at read time
                self._bass_history.append(bass_raw)
                self._mid_history.append(mid_raw)
                self._high_history.append(high_raw)
        
        # Update features with smoothing
        with self._lock:
            features = self._data.features
            
            # Normalize energy against adaptive max and apply gain
            avg_energy = np.mean(list(self._energy_history)) if self._energy_history else 0
            features.rms = rms
            features.energy = min(1.0, (avg_energy / self._max_rms) * self._gain) if self._max_rms > 0 else 0
            
            # Smooth frequency bands and apply gain
            avg_bass = np.mean(list(self._bass_history)) if self._bass_history else 0
            avg_mid = np.mean(list(self._mid_history)) if self._mid_history else 0
            avg_high = np.mean(list(self._high_history)) if self._high_history else 0
            
            # Use adaptive max as the reference for "full scale", gain adjusts sensitivity
            # At gain=1.0, reaching the adaptive max = 1.0 output
            # At gain=0.5, you need 2x the adaptive max to reach 1.0
            # At gain=2.0, you only need 0.5x the adaptive max to reach 1.0
            features.bass = min(1.0, (avg_bass / self._max_bass) * self._gain) if self._max_bass > 0 else 0
            features.mid = min(1.0, (avg_mid / self._max_mid) * self._gain) if self._max_mid > 0 else 0
            features.high = min(1.0, (avg_high / self._max_high) * self._gain) if self._max_high > 0 else 0
            
            # Tempo (use current estimate with smoothing)
            features.tempo = self._current_tempo
            
            features.beat_detected = beat_detected
            features.onset_detected = onset_detected
            features.time_since_beat = current_time - self._last_beat_time
            
            # Estimate danceability from beat regularity and bass energy
            if len(self._onset_history) >= 3 and features.energy > 0:
                intervals = np.diff(list(self._onset_history))
                if len(intervals) > 0 and np.mean(intervals) > 0:
                    # Regularity: how consistent are the intervals (lower std = more regular)
                    coefficient_of_variation = np.std(intervals) / np.mean(intervals)
                    regularity = 1.0 - min(1.0, coefficient_of_variation)
                    # Combine regularity with bass presence for danceability, scaled by energy
                    bass_factor = features.bass * 0.3
                    raw_danceability = regularity * 0.7 + bass_factor
                    features.danceability = min(1.0, raw_danceability * (0.5 + features.energy * 0.5))
                else:
                    features.danceability = 0.0
            else:
                # Fallback: estimate from bass and energy (no constant offset)
                features.danceability = min(1.0, features.bass * 0.5 + features.energy * 0.5)
            
            # Estimate valence from frequency balance (brighter = happier)
            # When no audio (all bands zero), valence goes to 0
            if features.bass + features.mid + features.high > 0:
                brightness = (features.mid + features.high * 2) / (features.bass + features.mid + features.high + 0.001)
                features.valence = min(1.0, brightness)
            else:
                features.valence = 0.0
            
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
                    # Normalize with adaptive max and gain
                    normalized = min(1.0, (max_val / max(0.01, self._max_rms * 3)) * self._gain)
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
                    # Normalize with adaptive max and gain
                    normalized = min(1.0, (band_energy / max(0.0001, self._max_rms ** 2 * 10)) * self._gain)
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
    
    def _correct_tempo_octave(self, raw_bpm: float, confidence: float = 0.0) -> float:
        """
        Correct octave errors in tempo estimation.
        
        Aubio's beat tracker can sometimes detect at half or double the actual tempo,
        especially when there are strong off-beat elements (like hi-hats or background drums
        on every second beat). This uses aggressive correction based on confidence.
        
        Args:
            raw_bpm: The raw BPM value from aubio
            confidence: Beat detection confidence (0-1)
            
        Returns:
            Corrected BPM value
        """
        bpm = raw_bpm
        
        # Correct extreme values outside normal music range
        while bpm < 60:
            bpm *= 2
        while bpm > 200:
            bpm /= 2
        
        # Aggressive half-time correction: if BPM is low and confidence is high,
        # it's likely detecting half-time (e.g., snare on 2 and 4 instead of every beat)
        # Most popular music is 100-130 BPM, so double anything below 100 with high confidence
        if bpm < 100 and confidence > 0.5:
            bpm *= 2
        
        return bpm
    
    def _process_madmom_beat_detection_async(self, audio_samples: list[float]) -> None:
        """
        Process audio with madmom's RNN beat processor and DBN tracking.
        Runs in a background thread to avoid blocking audio capture.
        
        Args:
            audio_samples: Copy of audio data to process
        """
        if self._madmom_beat_processor is None or self._madmom_dbn_processor is None:
            self._madmom_processing = False
            return
        
        try:
            # Use last 8 seconds for better tempo estimation
            max_samples = self.sample_rate * 8
            if len(audio_samples) > max_samples:
                audio_samples = audio_samples[-max_samples:]
            
            # Convert to numpy array (madmom expects float32)
            audio_signal = np.array(audio_samples, dtype=np.float32)
            
            # Create a Signal object that madmom can process
            # madmom expects mono audio at its expected sample rate
            signal = Signal(audio_signal, sample_rate=self.sample_rate, num_channels=1)
            
            # Get beat activations from RNN (this is the heavy computation)
            activations = self._madmom_beat_processor(signal)
            
            if activations is not None and len(activations) > 0:
                # Store recent activations for visualization (thread-safe append to deque)
                for act in activations[-self._madmom_fps:]:  # Last 1 second
                    self._beat_activations.append(float(act))
                
                # Use DBN processor to decode beats from activations
                beats = self._madmom_dbn_processor(activations)
                
                # Update tempo from detected beats
                if beats is not None and len(beats) >= 2:
                    # Calculate tempo from beat intervals
                    intervals = np.diff(beats)
                    if len(intervals) > 0:
                        # Filter out outliers
                        valid_intervals = intervals[(intervals > 0.25) & (intervals < 2.0)]
                        if len(valid_intervals) > 0:
                            avg_interval = np.median(valid_intervals)
                            tempo = 60.0 / avg_interval
                            # Apply octave correction
                            tempo = self._correct_tempo_octave(tempo, 0.5)
                            
                            if 60 <= tempo <= 200:
                                self._tempo_history.append(tempo)
                                self._current_tempo = float(np.median(list(self._tempo_history)))
                                logger.info(f"Madmom detected tempo: {tempo:.1f} BPM (current: {self._current_tempo:.1f} BPM)")
            
            # Trim accumulator to keep memory bounded (keep last 10 seconds)
            with self._madmom_lock:
                max_keep = self.sample_rate * 10
                if len(self._madmom_audio_accumulator) > max_keep:
                    self._madmom_audio_accumulator = self._madmom_audio_accumulator[-max_keep:]
            
        except Exception as e:
            logger.warning(f"Madmom beat detection failed: {e}")
            import traceback
            logger.debug(traceback.format_exc())
        finally:
            self._madmom_processing = False
    
    def _normalize_bands(self, bass_raw: float, mid_raw: float, high_raw: float) -> tuple[float, float, float]:
        """Normalize frequency bands with adaptive max values."""
        # Normalize each band against its adaptive max, apply gain
        bass = min(1.0, (bass_raw / max(0.01, self._max_bass)) * self._gain)
        mid = min(1.0, (mid_raw / max(0.01, self._max_mid)) * self._gain)
        high = min(1.0, (high_raw / max(0.01, self._max_high)) * self._gain)
        
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
    
    def get_task_status(self) -> dict:
        """
        Get status of background processing tasks.
        Returns dict with madmom task info for GUI display.
        """
        current_time = time.time()
        
        # Calculate buffer duration
        with self._madmom_lock:
            buffer_samples = len(self._madmom_audio_accumulator)
        buffer_duration = buffer_samples / self.sample_rate if self.sample_rate > 0 else 0
        
        # Calculate time until next madmom run
        time_since_last = current_time - self._madmom_last_process_time
        time_until_next = max(0, self._madmom_process_interval - time_since_last)
        
        # Calculate progress (0-1) towards next run
        progress = min(1.0, time_since_last / self._madmom_process_interval) if self._madmom_process_interval > 0 else 0
        
        # Determine status
        if not MADMOM_AVAILABLE:
            status = "Unavailable"
        elif self._madmom_processing:
            status = "Processing..."
        elif buffer_duration < 2.0:
            status = f"Buffering ({buffer_duration:.1f}s)"
        else:
            status = "Ready"
        
        return {
            "madmom_status": status,
            "madmom_processing": self._madmom_processing,
            "madmom_available": MADMOM_AVAILABLE,
            "buffer_duration": buffer_duration,
            "time_until_next": time_until_next,
            "progress": progress,
            "current_tempo": self._current_tempo,
        }


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
