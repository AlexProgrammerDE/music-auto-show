"""
Simulators for Music Auto Show.
Provides simulated audio analyzer and DMX interface for testing without hardware.
"""
import logging
import math
import threading
import time
from typing import Optional, Callable, List, Tuple, TYPE_CHECKING
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

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
    
    def add_callback(self, callback: Callable) -> None:
        """Add a callback to be called when analysis data updates."""
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable) -> None:
        """Remove a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
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
    
    def get_data(self):
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
                onset_history=list(self._data.onset_history) if hasattr(self._data, 'onset_history') else []
            )
    
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
                
                # Simulate onset history (64 points)
                self._data.onset_history = self._generate_onset_history(elapsed, time_since_beat, beat_interval)
            
            # Notify callbacks
            for callback in self._callbacks:
                try:
                    callback(self._data)
                except Exception:
                    pass
            
            time.sleep(0.025)  # 40 Hz
    
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
            
            # Add wave variation
            wave = 0.2 * math.sin(elapsed * 5 + i * 0.5)
            
            # Add randomness
            noise = random.uniform(-0.05, 0.05)
            
            value = (base + wave + noise) * beat_factor
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


class DMXInterface(ABC):
    """Abstract base class for DMX interfaces."""
    
    @abstractmethod
    def open(self) -> bool:
        """Open the DMX interface."""
        pass
    
    @abstractmethod
    def close(self) -> None:
        """Close the DMX interface."""
        pass
    
    @abstractmethod
    def send(self, data: bytes) -> bool:
        """Send DMX data."""
        pass
    
    @abstractmethod
    def is_open(self) -> bool:
        """Check if interface is open."""
        pass
    
    def get_stats(self) -> dict:
        """Get interface statistics. Override in subclasses."""
        return {}


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
            "send_count": self._send_count,
            "is_open": self._is_open
        }
