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

from config import ShowConfig
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
    waveform: list[float] = field(default_factory=list)
    spectrum: list[float] = field(default_factory=list)
    spectrogram: list[list[float]] = field(default_factory=list)
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


@dataclass
class AudioRuntimeStatus:
    """Resolved audio input state for UI display."""
    configured_device_name: str = ""
    configured_mode: str = "auto"
    actual_mode: str = ""
    device_index: Optional[int] = None
    device_name: str = ""
    device_type: str = ""
    host_api: str = ""
    channels: int = 0
    sample_rate: int = 0
    selection_reason: str = "not_started"
    missing_device_name: str = ""
    running: bool = False
    last_error: str = ""
    simulated: bool = False


@dataclass
class DMXRuntimeStatus:
    """Resolved DMX output state for UI display."""
    configured_port: str = ""
    port: str = ""
    device_info: str = ""
    interface_type: str = ""
    break_method: str = ""
    running: bool = False
    is_open: bool = False
    send_count: int = 0
    error_count: int = 0
    consecutive_errors: int = 0
    last_error: str = ""
    simulated: bool = False


@dataclass
class RecordingState:
    """Manual input recording state for UI display."""
    recording: bool = False
    has_recording: bool = False
    duration: float = 0.0
    max_duration: float = 30.0
    sample_rate: int = 0
    channels: int = 1
    peak: float = 0.0
    rms: float = 0.0
    clipped_samples: int = 0
    source: str = ""
    error: str = ""


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
        
        # Components (initialized when show starts)
        self.dmx_controller: Optional[DMXController] = None
        self.dmx_interface: Optional[Any] = None
        self.audio_analyzer: Optional[Union[AudioAnalyzer, "SimulatedAudioAnalyzer"]] = None
        self.effects_engine: Optional[EffectsEngine] = None
        
        # Reactive state for UI
        self.audio_state: AudioState = AudioState()
        self.task_status: TaskStatus = TaskStatus()
        self.audio_runtime_status: AudioRuntimeStatus = AudioRuntimeStatus()
        self.dmx_runtime_status: DMXRuntimeStatus = DMXRuntimeStatus()
        self.recording_state: RecordingState = RecordingState()
        self.fixture_states: dict[str, FixtureState] = {}
        self.current_analysis: Optional[AnalysisData] = None
        self.dmx_channels: list[int] = [0] * 512
        self._last_recording_data_url: str = ""
        
        # Status
        self.status_message: str = "Stopped"
        self.is_blackout: bool = False
        
        # Thread safety
        self._state_lock = threading.Lock()
        self._effects_thread: Optional[threading.Thread] = None
        self._audio_monitor_thread: Optional[threading.Thread] = None
        self._audio_monitoring: bool = False
        self._recording_started_monitor: bool = False
        
        # FPS tracking
        self._effects_frame_count: int = 0
        self._effects_fps_time: float = time.time()
        self._effects_fps_count: int = 0
        self._last_spectrogram_state_copy: float = 0.0
        
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

    def _copy_audio_data_to_state_unlocked(self, data: AnalysisData) -> None:
        """Copy analysis data into UI state. Caller must hold _state_lock."""
        self.current_analysis = data
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
        self.audio_state.waveform = list(data.waveform) if data.waveform else []
        self.audio_state.spectrum = list(data.spectrum) if data.spectrum else []
        now = time.time()
        if data.spectrogram and now - self._last_spectrogram_state_copy >= 0.1:
            self.audio_state.spectrogram = [list(frame) for frame in data.spectrogram]
            self._last_spectrogram_state_copy = now
        self.audio_state.onset_history = list(data.onset_history) if data.onset_history else []

    def _update_task_status_unlocked(self) -> None:
        """Copy analyzer background task status into UI state. Caller must hold _state_lock."""
        if not self.audio_analyzer:
            self.task_status.madmom_status = "Idle"
            self.task_status.madmom_processing = False
            self.task_status.madmom_available = False
            self.task_status.progress = 0.0
            self.task_status.buffer_duration = 0.0
            self.task_status.time_until_next = 0.0
            return

        task_status = self.audio_analyzer.get_task_status()
        self.task_status.madmom_status = task_status.get("madmom_status", "Unknown")
        self.task_status.madmom_processing = task_status.get("madmom_processing", False)
        self.task_status.madmom_available = task_status.get("madmom_available", False)
        self.task_status.progress = task_status.get("progress", 0.0)
        self.task_status.buffer_duration = task_status.get("buffer_duration", 0.0)
        self.task_status.time_until_next = task_status.get("time_until_next", 0.0)

    def _update_runtime_status_unlocked(self) -> None:
        """Copy resolved audio and DMX status into UI state. Caller must hold _state_lock."""
        if self.audio_analyzer and hasattr(self.audio_analyzer, "get_runtime_status"):
            audio_status = self.audio_analyzer.get_runtime_status()
            self.audio_runtime_status = AudioRuntimeStatus(
                configured_device_name=str(audio_status.get("configured_device_name") or self.config.audio.device_name),
                configured_mode=str(audio_status.get("configured_mode") or self.config.audio.fallback_mode.value),
                actual_mode=str(audio_status.get("actual_mode") or ""),
                device_index=audio_status.get("device_index"),
                device_name=str(audio_status.get("device_name") or ""),
                device_type=str(audio_status.get("device_type") or ""),
                host_api=str(audio_status.get("host_api") or ""),
                channels=int(audio_status.get("channels") or 0),
                sample_rate=int(audio_status.get("sample_rate") or 0),
                selection_reason=str(audio_status.get("selection_reason") or "unknown"),
                missing_device_name=str(audio_status.get("missing_device_name") or ""),
                running=bool(audio_status.get("running", False)),
                last_error=str(audio_status.get("last_error") or ""),
                simulated=bool(audio_status.get("simulated", False)),
            )
        else:
            self.audio_runtime_status = AudioRuntimeStatus(
                configured_device_name=self.config.audio.device_name,
                configured_mode=self.config.audio.fallback_mode.value,
                selection_reason="not_started",
                simulated=self.simulate_audio,
            )

        if self.dmx_controller:
            dmx_stats = self.dmx_controller.get_stats()
            interface_stats = dmx_stats.get("interface", {})
            interface_type = str(interface_stats.get("type") or "")
            self.dmx_runtime_status = DMXRuntimeStatus(
                configured_port=self.config.dmx.port,
                port=str(interface_stats.get("port") or self.config.dmx.port or ""),
                device_info=str(interface_stats.get("device_info") or ""),
                interface_type=interface_type,
                break_method=str(interface_stats.get("break_method") or ""),
                running=bool(dmx_stats.get("running", False)),
                is_open=bool(interface_stats.get("is_open", False)),
                send_count=int(interface_stats.get("send_count") or 0),
                error_count=int(interface_stats.get("error_count") or 0),
                consecutive_errors=int(interface_stats.get("consecutive_errors") or 0),
                last_error=str(interface_stats.get("last_error") or ""),
                simulated=interface_type == "simulated" or self.simulate_dmx,
            )
        else:
            self.dmx_runtime_status = DMXRuntimeStatus(
                configured_port=self.config.dmx.port,
                simulated=self.simulate_dmx,
            )

    def _update_recording_state_unlocked(self) -> None:
        """Copy recording status into UI state. Caller must hold _state_lock."""
        if self.audio_analyzer and hasattr(self.audio_analyzer, "get_recording_status"):
            status = self.audio_analyzer.get_recording_status()
            self.recording_state = RecordingState(
                recording=bool(status.get("recording", False)),
                has_recording=bool(status.get("has_recording", False)),
                duration=float(status.get("duration") or 0.0),
                max_duration=float(status.get("max_duration") or 30.0),
                sample_rate=int(status.get("sample_rate") or 0),
                channels=int(status.get("channels") or 1),
                peak=float(status.get("peak") or 0.0),
                rms=float(status.get("rms") or 0.0),
                clipped_samples=int(status.get("clipped_samples") or 0),
                source=str(status.get("source") or ""),
                error="",
            )
        elif not self._last_recording_data_url:
            self.recording_state = RecordingState()

    def _start_audio_monitor(self) -> bool:
        """Start an audio-only monitor for recording and diagnostics."""
        if self.audio_analyzer:
            return True

        logger.info("Starting audio monitor")
        self.audio_analyzer = create_audio_analyzer(
            simulate=self.simulate_audio,
            device_name=self.config.audio.device_name,
            input_mode=self.config.audio.fallback_mode
        )

        if not self.audio_analyzer.start():
            self.status_message = "Audio monitor failed"
            self.audio_analyzer = None
            with self._state_lock:
                self._update_runtime_status_unlocked()
            return False

        self.audio_analyzer.set_gain(self.config.effects.audio_gain)
        self._audio_monitoring = True
        self._audio_monitor_thread = threading.Thread(target=self._audio_monitor_loop, daemon=True)
        self._audio_monitor_thread.start()
        self.status_message = "Audio check running"
        return True

    def _stop_audio_monitor(self) -> None:
        """Stop the audio-only monitor if it is active."""
        self._audio_monitoring = False
        if self._audio_monitor_thread:
            self._audio_monitor_thread.join(timeout=2.0)
            self._audio_monitor_thread = None

        if self.audio_analyzer and not self.running:
            logger.info("Stopping audio monitor")
            self.audio_analyzer.stop()
            self.audio_analyzer = None

        if not self.running:
            self.status_message = "Stopped"
        with self._state_lock:
            self._update_runtime_status_unlocked()

    def _audio_monitor_loop(self) -> None:
        """Audio-only update loop used by the recorder when the show is stopped."""
        while self._audio_monitoring and not self.running:
            if self.audio_analyzer:
                data = self.audio_analyzer.get_data()
                with self._state_lock:
                    self._copy_audio_data_to_state_unlocked(data)
                    self._update_task_status_unlocked()
                    self._update_runtime_status_unlocked()
                    self._update_recording_state_unlocked()
            time.sleep(0.025)

    def refresh_runtime_status(self) -> None:
        """Refresh status snapshots for polling UI components."""
        with self._state_lock:
            self._update_runtime_status_unlocked()
            self._update_recording_state_unlocked()
    
    def start_show(self) -> bool:
        """Start the light show."""
        logger.info("=" * 50)
        logger.info("STARTING SHOW")
        logger.info("=" * 50)
        
        if not self.config.fixtures:
            logger.warning("No fixtures configured!")
            self.status_message = "No fixtures configured!"
            return False

        if self._audio_monitoring:
            self._stop_audio_monitor()
        
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
        logger.info(f"Audio Device: {self.config.audio.device_name or '(auto)'}")
        logger.info(f"Audio Fallback Mode: {self.config.audio.fallback_mode.value}")
        
        self.audio_analyzer = create_audio_analyzer(
            simulate=self.simulate_audio,
            device_name=self.config.audio.device_name,
            input_mode=self.config.audio.fallback_mode
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
        with self._state_lock:
            self._update_runtime_status_unlocked()
            self._update_recording_state_unlocked()
        logger.info("SHOW RUNNING")
        
        return True
    
    def stop_show(self) -> None:
        """Stop the light show with proper blackout and cleanup."""
        logger.info("=" * 50)
        logger.info("STOPPING SHOW")
        logger.info("=" * 50)
        
        self.running = False
        
        # Wait for effects thread to finish
        if self._effects_thread:
            logger.info("Waiting for effects thread to stop...")
            self._effects_thread.join(timeout=2.0)
            if self._effects_thread.is_alive():
                logger.warning("Effects thread did not stop in time")
            self._effects_thread = None
        
        # Send blackout through effects engine (sets all fixture states to 0)
        if self.effects_engine:
            logger.info("Sending blackout through effects engine...")
            self.effects_engine.blackout()
        
        # Also explicitly blackout the DMX controller
        if self.dmx_controller:
            logger.info("Sending blackout to DMX controller...")
            self.dmx_controller.blackout()
            
            # Give time for the blackout frame to be sent
            # The DMX output thread runs at configured FPS, wait for at least 2 frames
            import time
            time.sleep(0.1)  # 100ms should cover 2+ frames at 40 FPS
        
        # Stop audio analyzer first (doesn't need DMX)
        if self.audio_analyzer:
            logger.info("Stopping audio analyzer...")
            self.audio_analyzer.stop()
            self.audio_analyzer = None
        
        # Stop DMX output thread
        if self.dmx_controller:
            logger.info("Stopping DMX controller...")
            self.dmx_controller.stop()
            self.dmx_controller = None
        
        # Close DMX interface (releases serial port lock)
        if self.dmx_interface:
            logger.info("Closing DMX interface...")
            self.dmx_interface.close()
            self.dmx_interface = None
        
        # Clear effects engine reference
        if self.effects_engine:
            self.effects_engine = None
        
        self.status_message = "Stopped"
        self.is_blackout = False
        self.fixture_states = {}
        self.dmx_channels = [0] * 512
        with self._state_lock:
            self._update_runtime_status_unlocked()
        
        logger.info("=" * 50)
        logger.info("SHOW STOPPED")
        logger.info("=" * 50)
    
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
                    self._copy_audio_data_to_state_unlocked(data)
                    self.fixture_states = fixture_states
                    
                    # Update DMX channels
                    if self.dmx_controller:
                        self.dmx_channels = list(self.dmx_controller.get_all_channels())
                    
                    # Update task status
                    self._update_task_status_unlocked()
                    self._update_runtime_status_unlocked()
                    self._update_recording_state_unlocked()
                
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

    def start_audio_recording(self) -> tuple[bool, str]:
        """Start a diagnostic recording from the selected audio input."""
        self._last_recording_data_url = ""
        self._recording_started_monitor = False

        if not self.audio_analyzer:
            if not self._start_audio_monitor():
                with self._state_lock:
                    self.recording_state.error = "Could not start audio input"
                return False, "Could not start audio input"
            self._recording_started_monitor = True

        if not hasattr(self.audio_analyzer, "start_recording"):
            return False, "Audio input does not support recording"

        if not self.audio_analyzer.start_recording():
            with self._state_lock:
                self._update_runtime_status_unlocked()
                self.recording_state.error = "Audio input is not running"
            return False, "Audio input is not running"

        with self._state_lock:
            self._update_recording_state_unlocked()
            self._update_runtime_status_unlocked()
        return True, "Recording started"

    def stop_audio_recording(self) -> tuple[bool, str]:
        """Stop the diagnostic recording and keep a browser-playable WAV."""
        if not self.audio_analyzer or not hasattr(self.audio_analyzer, "stop_recording"):
            return False, "No active audio recording"

        status = self.audio_analyzer.stop_recording()
        data_url = ""
        if hasattr(self.audio_analyzer, "get_recording_data_url"):
            data_url = self.audio_analyzer.get_recording_data_url()
        self._last_recording_data_url = data_url

        source = str(status.get("source") or self.audio_runtime_status.device_name)
        recording_state = RecordingState(
            recording=False,
            has_recording=bool(status.get("has_recording", False)),
            duration=float(status.get("duration") or 0.0),
            max_duration=float(status.get("max_duration") or 30.0),
            sample_rate=int(status.get("sample_rate") or 0),
            channels=int(status.get("channels") or 1),
            peak=float(status.get("peak") or 0.0),
            rms=float(status.get("rms") or 0.0),
            clipped_samples=int(status.get("clipped_samples") or 0),
            source=source,
            error="" if data_url else "No audio was captured",
        )

        if self._recording_started_monitor:
            self._stop_audio_monitor()
            self._recording_started_monitor = False

        with self._state_lock:
            self.recording_state = recording_state
            self._update_runtime_status_unlocked()

        if not data_url:
            return False, "No audio was captured"
        return True, "Recording ready"

    def clear_audio_recording(self) -> None:
        """Clear the current diagnostic recording."""
        if self.audio_analyzer and hasattr(self.audio_analyzer, "clear_recording"):
            self.audio_analyzer.clear_recording()
        self._last_recording_data_url = ""
        self._recording_started_monitor = False
        with self._state_lock:
            self.recording_state = RecordingState()

    def get_audio_recording_data_url(self) -> str:
        """Get the latest diagnostic recording data URL."""
        if self._last_recording_data_url:
            return self._last_recording_data_url
        if self.audio_analyzer and hasattr(self.audio_analyzer, "get_recording_data_url"):
            return self.audio_analyzer.get_recording_data_url()
        return ""


# Global application state instance
app_state = AppState()
