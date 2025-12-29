"""
Application state management for Music Auto Show web UI.
Provides reactive state for real-time UI updates.
"""
import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Any

from typing import Union, TYPE_CHECKING

from config import ShowConfig, AudioInputMode
from audio_analyzer import AnalysisData, AudioAnalyzer, create_audio_analyzer
from effects_engine import EffectsEngine, FixtureState
from dmx_controller import DMXController, create_dmx_controller

if TYPE_CHECKING:
    from simulators import SimulatedAudioAnalyzer

logger = logging.getLogger(__name__)


@dataclass
class AudioState:
    """Reactive audio analysis state for UI binding."""
    energy: float = 0.0
    bass: float = 0.0
    mid: float = 0.0
    high: float = 0.0
    tempo: float = 120.0
    beat_position: float = 0.0
    danceability: float = 0.5
    valence: float = 0.5
    track_name: str = "No track"
    artist_name: str = ""
    album_colors: list[tuple[int, int, int]] = field(default_factory=list)
    spectrum: list[float] = field(default_factory=list)
    onset_history: list[float] = field(default_factory=list)


@dataclass
class TaskStatus:
    """Background task status for UI display."""
    madmom_status: str = "Idle"
    madmom_processing: bool = False
    madmom_available: bool = False
    progress: float = 0.0
    buffer_duration: float = 0.0
    time_until_next: float = 0.0
    effects_fps: float = 0.0


class AppState:
    """
    Central application state manager.
    Holds all state needed for the UI and coordinates background processing.
    """
    
    def __init__(self):
        # Configuration
        self.config: ShowConfig = ShowConfig()
        
        # Runtime state
        self.running: bool = False
        self.simulate_dmx: bool = False
        self.simulate_audio: bool = False
        self.audio_input_mode: AudioInputMode = AudioInputMode.AUTO
        
        # Components (initialized when show starts)
        self.dmx_controller: Optional[DMXController] = None
        self.dmx_interface: Optional[Any] = None
        self.audio_analyzer: Optional[Union[AudioAnalyzer, "SimulatedAudioAnalyzer"]] = None
        self.effects_engine: Optional[EffectsEngine] = None
        
        # Reactive state for UI
        self.audio_state: AudioState = AudioState()
        self.task_status: TaskStatus = TaskStatus()
        self.fixture_states: dict[str, FixtureState] = {}
        self.current_analysis: Optional[AnalysisData] = None
        self.dmx_channels: list[int] = [0] * 512
        
        # Status
        self.status_message: str = "Stopped"
        self.is_blackout: bool = False
        
        # Thread safety
        self._state_lock = threading.Lock()
        self._effects_thread: Optional[threading.Thread] = None
        
        # FPS tracking
        self._effects_frame_count: int = 0
        self._effects_fps_time: float = time.time()
        self._effects_fps_count: int = 0
        
        # Callbacks for UI updates
        self._on_state_change: list[Callable[[], None]] = []
    
    def add_state_listener(self, callback: Callable[[], None]) -> None:
        """Add a callback to be called when state changes."""
        self._on_state_change.append(callback)
    
    def remove_state_listener(self, callback: Callable[[], None]) -> None:
        """Remove a state change callback."""
        if callback in self._on_state_change:
            self._on_state_change.remove(callback)
    
    def _notify_state_change(self) -> None:
        """Notify all listeners of state change."""
        for callback in self._on_state_change:
            try:
                callback()
            except Exception as e:
                logger.error(f"State listener error: {e}")
    
    def start_show(self) -> bool:
        """Start the light show."""
        logger.info("=" * 50)
        logger.info("STARTING SHOW")
        logger.info("=" * 50)
        
        if not self.config.fixtures:
            logger.warning("No fixtures configured!")
            self.status_message = "No fixtures configured!"
            return False
        
        # Initialize DMX
        logger.info(f"Simulate DMX: {self.simulate_dmx}")
        logger.info(f"DMX Port: {self.config.dmx.port or '(auto-detect)'}")
        
        self.dmx_controller, self.dmx_interface = create_dmx_controller(
            port=self.config.dmx.port,
            simulate=self.simulate_dmx,
            fps=self.config.dmx.fps
        )
        
        if not self.dmx_interface.open():
            logger.error("DMX connection failed!")
            self.status_message = "DMX connection failed!"
            return False
        
        if not self.dmx_controller.start():
            logger.error("DMX start failed!")
            self.status_message = "DMX start failed!"
            return False
        
        # Initialize audio
        logger.info(f"Simulate Audio: {self.simulate_audio}")
        logger.info(f"Audio Input Mode: {self.audio_input_mode.value}")
        
        self.audio_analyzer = create_audio_analyzer(
            simulate=self.simulate_audio,
            input_mode=self.audio_input_mode
        )
        
        if not self.audio_analyzer.start():
            logger.error("Audio capture failed!")
            self.status_message = "Audio capture failed!"
            self.dmx_controller.stop()
            self.dmx_interface.close()
            return False
        
        # Apply audio gain
        self.audio_analyzer.set_gain(self.config.effects.audio_gain)
        
        # Initialize effects engine
        self.effects_engine = EffectsEngine(self.dmx_controller, self.config)
        
        # Start effects thread
        self.running = True
        self._effects_thread = threading.Thread(target=self._effects_loop, daemon=True)
        self._effects_thread.start()
        
        self.status_message = "Running"
        self.is_blackout = False
        logger.info("SHOW RUNNING")
        
        return True
    
    def stop_show(self) -> None:
        """Stop the light show."""
        self.running = False
        
        # Wait for effects thread
        if self._effects_thread:
            self._effects_thread.join(timeout=1.0)
            self._effects_thread = None
        
        # Blackout and cleanup
        if self.effects_engine:
            self.effects_engine.blackout()
            self.effects_engine = None
        
        if self.audio_analyzer:
            self.audio_analyzer.stop()
            self.audio_analyzer = None
        
        if self.dmx_controller:
            self.dmx_controller.stop()
            self.dmx_controller = None
        
        if self.dmx_interface:
            self.dmx_interface.close()
            self.dmx_interface = None
        
        self.status_message = "Stopped"
        self.fixture_states = {}
        self.dmx_channels = [0] * 512
        logger.info("Show stopped")
    
    def toggle_blackout(self) -> bool:
        """Toggle blackout mode. Returns new blackout state."""
        if self.effects_engine:
            self.is_blackout = self.effects_engine.toggle_blackout()
            self.status_message = "BLACKOUT" if self.is_blackout else "Running"
            return self.is_blackout
        return False
    
    def _effects_loop(self) -> None:
        """Effects processing loop - runs in dedicated thread."""
        last_debug_time = time.time()
        
        while self.running:
            if self.effects_engine and self.audio_analyzer:
                # Get audio analysis
                data = self.audio_analyzer.get_data()
                
                # Process effects
                fixture_states = self.effects_engine.process(data)
                
                # Update shared state (thread-safe)
                with self._state_lock:
                    self.current_analysis = data
                    self.fixture_states = fixture_states
                    
                    # Update audio state for UI binding
                    self.audio_state.energy = data.features.energy
                    self.audio_state.bass = data.features.bass
                    self.audio_state.mid = data.features.mid
                    self.audio_state.high = data.features.high
                    self.audio_state.tempo = data.features.tempo
                    self.audio_state.beat_position = data.beat_position
                    self.audio_state.danceability = data.features.danceability
                    self.audio_state.valence = data.features.valence
                    self.audio_state.track_name = data.track_name or "System Audio"
                    self.audio_state.artist_name = data.artist_name or ""
                    self.audio_state.album_colors = data.album_colors or []
                    self.audio_state.spectrum = list(data.spectrum) if data.spectrum else []
                    self.audio_state.onset_history = list(data.onset_history) if data.onset_history else []
                    
                    # Update DMX channels
                    if self.dmx_controller:
                        self.dmx_channels = list(self.dmx_controller.get_all_channels())
                    
                    # Update task status
                    task_status = self.audio_analyzer.get_task_status()
                    self.task_status.madmom_status = task_status.get("madmom_status", "Unknown")
                    self.task_status.madmom_processing = task_status.get("madmom_processing", False)
                    self.task_status.madmom_available = task_status.get("madmom_available", False)
                    self.task_status.progress = task_status.get("progress", 0.0)
                    self.task_status.buffer_duration = task_status.get("buffer_duration", 0.0)
                    self.task_status.time_until_next = task_status.get("time_until_next", 0.0)
                
                # FPS tracking
                self._effects_fps_count += 1
                now = time.time()
                if now - self._effects_fps_time >= 1.0:
                    self.task_status.effects_fps = self._effects_fps_count / (now - self._effects_fps_time)
                    self._effects_fps_time = now
                    self._effects_fps_count = 0
                
                # Debug logging every 5 seconds
                if now - last_debug_time >= 5.0:
                    logger.info(f"Audio: energy={data.features.energy:.2f}, bass={data.features.bass:.2f}, "
                               f"tempo={data.features.tempo:.0f} BPM")
                    last_debug_time = now
            
            time.sleep(0.025)  # 40 FPS
    
    def get_fixture_state(self, name: str) -> FixtureState:
        """Get fixture state by name (thread-safe)."""
        with self._state_lock:
            return self.fixture_states.get(name, FixtureState())
    
    def update_effects_config(self) -> None:
        """Update effects engine with current config."""
        if self.effects_engine:
            self.effects_engine.update_config(self.config)
    
    def set_audio_gain(self, gain: float) -> None:
        """Set audio gain."""
        self.config.effects.audio_gain = gain
        if self.audio_analyzer:
            self.audio_analyzer.set_gain(gain)


# Global application state instance
app_state = AppState()
