"""
Spotify audio analysis integration.
Fetches real-time playback info and audio features from Spotify API.
"""
import time
import threading
from typing import Optional, Callable
from dataclasses import dataclass, field

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    SPOTIPY_AVAILABLE = True
except ImportError:
    SPOTIPY_AVAILABLE = False


@dataclass
class AudioFeatures:
    """Audio features for the current track."""
    # Core features (0-1 scale)
    energy: float = 0.5
    danceability: float = 0.5
    valence: float = 0.5  # Musical positivity
    acousticness: float = 0.5
    instrumentalness: float = 0.5
    liveness: float = 0.5
    speechiness: float = 0.5
    
    # Loudness (dB, typically -60 to 0)
    loudness: float = -10.0
    
    # Tempo
    tempo: float = 120.0  # BPM
    
    # Key and mode
    key: int = 0  # 0-11 (C, C#, D, etc.)
    mode: int = 1  # 0=minor, 1=major
    
    # Time signature
    time_signature: int = 4


@dataclass
class TrackInfo:
    """Information about the current track."""
    track_id: str = ""
    name: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: int = 0
    progress_ms: int = 0
    is_playing: bool = False


@dataclass
class AnalysisData:
    """Combined analysis data for visualization."""
    track: TrackInfo = field(default_factory=TrackInfo)
    features: AudioFeatures = field(default_factory=AudioFeatures)
    
    # Derived/computed values for visualization
    beat_position: float = 0.0  # 0-1 within current beat
    bar_position: float = 0.0   # 0-1 within current bar
    section_intensity: float = 0.5  # Current section intensity
    
    # Real-time estimates
    estimated_beat: int = 0
    estimated_bar: int = 0
    
    @property
    def normalized_energy(self) -> float:
        """Get normalized energy (0-1)."""
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


class SpotifyAnalyzer:
    """
    Spotify analyzer that continuously monitors playback and provides audio features.
    """
    
    SCOPES = [
        "user-read-playback-state",
        "user-read-currently-playing",
    ]
    
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str = "http://localhost:8888/callback"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        
        self._spotify: Optional[spotipy.Spotify] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._data = AnalysisData()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[AnalysisData], None]] = []
        self._last_track_id = ""
        self._features_cache: dict[str, AudioFeatures] = {}
        self._poll_interval = 1.0  # seconds
    
    def add_callback(self, callback: Callable[[AnalysisData], None]) -> None:
        """Add a callback to be called when analysis data updates."""
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[AnalysisData], None]) -> None:
        """Remove a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def authenticate(self) -> bool:
        """Authenticate with Spotify."""
        if not SPOTIPY_AVAILABLE:
            print("spotipy not available")
            return False
        
        if not self.client_id or not self.client_secret:
            print("Spotify credentials not configured")
            return False
        
        try:
            auth_manager = SpotifyOAuth(
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri=self.redirect_uri,
                scope=" ".join(self.SCOPES),
                open_browser=True
            )
            self._spotify = spotipy.Spotify(auth_manager=auth_manager)
            # Test the connection
            self._spotify.current_user()
            return True
        except Exception as e:
            print(f"Spotify authentication failed: {e}")
            return False
    
    def start(self) -> bool:
        """Start continuous polling."""
        if self._running:
            return True
        
        if not self._spotify:
            if not self.authenticate():
                return False
        
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        return True
    
    def stop(self) -> None:
        """Stop polling."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
    
    def get_data(self) -> AnalysisData:
        """Get current analysis data."""
        with self._lock:
            return self._data
    
    def _poll_loop(self) -> None:
        """Continuous polling loop."""
        last_update = 0.0
        
        while self._running:
            now = time.time()
            
            # Update from API periodically
            if now - last_update >= self._poll_interval:
                self._update_from_api()
                last_update = now
            
            # Update beat/bar position estimates between API calls
            self._update_estimates()
            
            # Notify callbacks
            with self._lock:
                data = self._data
            for callback in self._callbacks:
                try:
                    callback(data)
                except Exception as e:
                    print(f"Callback error: {e}")
            
            time.sleep(0.05)  # 20 Hz update rate for smooth visualization
    
    def _update_from_api(self) -> None:
        """Update data from Spotify API."""
        if not self._spotify:
            return
        
        try:
            playback = self._spotify.current_playback()
            
            if not playback or not playback.get('item'):
                with self._lock:
                    self._data.track.is_playing = False
                return
            
            item = playback['item']
            track_id = item['id']
            
            # Update track info
            track = TrackInfo(
                track_id=track_id,
                name=item['name'],
                artist=", ".join(a['name'] for a in item['artists']),
                album=item['album']['name'],
                duration_ms=item['duration_ms'],
                progress_ms=playback.get('progress_ms', 0),
                is_playing=playback.get('is_playing', False)
            )
            
            # Get audio features if track changed
            if track_id != self._last_track_id:
                self._last_track_id = track_id
                features = self._get_features(track_id)
            else:
                features = self._data.features
            
            with self._lock:
                self._data.track = track
                self._data.features = features
                
        except Exception as e:
            print(f"API update error: {e}")
    
    def _get_features(self, track_id: str) -> AudioFeatures:
        """Get audio features for a track (with caching)."""
        if track_id in self._features_cache:
            return self._features_cache[track_id]
        
        try:
            features = self._spotify.audio_features([track_id])
            if features and features[0]:
                f = features[0]
                result = AudioFeatures(
                    energy=f.get('energy', 0.5),
                    danceability=f.get('danceability', 0.5),
                    valence=f.get('valence', 0.5),
                    acousticness=f.get('acousticness', 0.5),
                    instrumentalness=f.get('instrumentalness', 0.5),
                    liveness=f.get('liveness', 0.5),
                    speechiness=f.get('speechiness', 0.5),
                    loudness=f.get('loudness', -10.0),
                    tempo=f.get('tempo', 120.0),
                    key=f.get('key', 0),
                    mode=f.get('mode', 1),
                    time_signature=f.get('time_signature', 4)
                )
                self._features_cache[track_id] = result
                return result
        except Exception as e:
            print(f"Failed to get audio features: {e}")
        
        return AudioFeatures()
    
    def _update_estimates(self) -> None:
        """Update beat/bar position estimates based on progress."""
        with self._lock:
            if not self._data.track.is_playing:
                return
            
            progress_ms = self._data.track.progress_ms
            beat_interval = self._data.beat_interval_ms
            
            if beat_interval > 0:
                # Estimate current beat
                total_beats = progress_ms / beat_interval
                self._data.estimated_beat = int(total_beats)
                self._data.beat_position = total_beats - int(total_beats)
                
                # Estimate bar position (assuming 4/4)
                beats_per_bar = self._data.features.time_signature
                if beats_per_bar > 0:
                    bar_beats = self._data.estimated_beat % beats_per_bar
                    self._data.bar_position = (bar_beats + self._data.beat_position) / beats_per_bar
                    self._data.estimated_bar = self._data.estimated_beat // beats_per_bar
            
            # Increment progress estimate (will be corrected on next API call)
            self._data.track.progress_ms += 50  # Match our update rate


class SimulatedSpotifyAnalyzer:
    """
    Simulated Spotify analyzer for testing without API access.
    Generates synthetic audio features and beat timing.
    """
    
    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._data = AnalysisData()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[AnalysisData], None]] = []
        self._start_time = 0.0
    
    def add_callback(self, callback: Callable[[AnalysisData], None]) -> None:
        """Add a callback."""
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[AnalysisData], None]) -> None:
        """Remove a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def authenticate(self) -> bool:
        """Always succeeds for simulation."""
        return True
    
    def start(self) -> bool:
        """Start simulation."""
        if self._running:
            return True
        
        self._running = True
        self._start_time = time.time()
        
        # Set up simulated track
        with self._lock:
            self._data.track = TrackInfo(
                track_id="sim_001",
                name="Simulated Track",
                artist="Test Artist",
                album="Test Album",
                duration_ms=180000,
                progress_ms=0,
                is_playing=True
            )
            self._data.features = AudioFeatures(
                energy=0.7,
                danceability=0.8,
                valence=0.6,
                tempo=128.0,
                time_signature=4
            )
        
        self._thread = threading.Thread(target=self._simulation_loop, daemon=True)
        self._thread.start()
        return True
    
    def stop(self) -> None:
        """Stop simulation."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
    
    def get_data(self) -> AnalysisData:
        """Get current analysis data."""
        with self._lock:
            return self._data
    
    def _simulation_loop(self) -> None:
        """Simulation loop."""
        import math
        
        while self._running:
            elapsed = time.time() - self._start_time
            elapsed_ms = elapsed * 1000
            
            with self._lock:
                # Update progress
                self._data.track.progress_ms = int(elapsed_ms) % self._data.track.duration_ms
                
                # Calculate beat position
                beat_interval = self._data.beat_interval_ms
                total_beats = elapsed_ms / beat_interval
                self._data.estimated_beat = int(total_beats)
                self._data.beat_position = total_beats - int(total_beats)
                
                # Calculate bar position
                beats_per_bar = self._data.features.time_signature
                bar_beats = self._data.estimated_beat % beats_per_bar
                self._data.bar_position = (bar_beats + self._data.beat_position) / beats_per_bar
                self._data.estimated_bar = self._data.estimated_beat // beats_per_bar
                
                # Vary energy over time (simulate song dynamics)
                base_energy = 0.6
                variation = 0.3 * math.sin(elapsed * 0.1)  # Slow variation
                beat_pulse = 0.1 * (1.0 - self._data.beat_position)  # Pulse on beats
                self._data.section_intensity = max(0, min(1, base_energy + variation + beat_pulse))
            
            # Notify callbacks
            with self._lock:
                data = self._data
            for callback in self._callbacks:
                try:
                    callback(data)
                except Exception:
                    pass
            
            time.sleep(0.025)  # 40 Hz


def create_spotify_analyzer(
    client_id: str = "",
    client_secret: str = "",
    redirect_uri: str = "http://localhost:8888/callback",
    simulate: bool = False
):
    """
    Factory function to create appropriate Spotify analyzer.
    
    Args:
        client_id: Spotify API client ID
        client_secret: Spotify API client secret
        redirect_uri: OAuth redirect URI
        simulate: Use simulated analyzer (for testing)
    
    Returns:
        SpotifyAnalyzer or SimulatedSpotifyAnalyzer
    """
    if simulate or not client_id or not client_secret:
        return SimulatedSpotifyAnalyzer()
    
    return SpotifyAnalyzer(client_id, client_secret, redirect_uri)
