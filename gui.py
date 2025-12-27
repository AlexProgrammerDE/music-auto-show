"""
GUI for Music Auto Show using Dear PyGui.
Provides fixture configuration, live visualization, and effect controls.
"""
import json
import logging
import threading
import time
from typing import Optional
from pathlib import Path

try:
    import dearpygui.dearpygui as dpg
    DEARPYGUI_AVAILABLE = True
except ImportError:
    DEARPYGUI_AVAILABLE = False

from config import (
    ShowConfig, FixtureConfig, FixtureProfile, ChannelConfig,
    VisualizationMode, MovementMode, StrobeEffectMode, RotationMode,
    DMXConfig, EffectsConfig, ChannelType,
    AudioInputMode, get_available_presets, get_preset, FIXTURE_PRESETS, 
    get_channel_type_display_name
)
from dmx_controller import DMXController, create_dmx_controller
from simulators import SimulatedDMXInterface
from audio_analyzer import AnalysisData, AudioAnalyzer, create_audio_analyzer
from effects_engine import EffectsEngine, FixtureState
from gui_dialogs import FixtureDialogs
from gui_visualizer import StageVisualizer

logger = logging.getLogger(__name__)


class MusicAutoShowGUI:
    """Main GUI application for Music Auto Show."""
    
    def __init__(self):
        self.config = ShowConfig()
        self.dmx_controller: Optional[DMXController] = None
        self.dmx_interface = None
        self.audio_analyzer = None
        self.effects_engine: Optional[EffectsEngine] = None
        
        self._running = False
        self._effects_thread: Optional[threading.Thread] = None  # Dedicated effects processing thread
        self._fixture_states: dict[str, FixtureState] = {}
        self._current_analysis: Optional[AnalysisData] = None
        self._state_lock = threading.Lock()  # Protects shared state between threads
        
        self._fixture_list_id = None
        self._visualizer_id = None
        self._status_text_id = None
        self._track_info_id = None
        
        # Initialize helper classes
        self._fixture_dialogs = FixtureDialogs(
            self.config,
            on_fixture_changed=self._refresh_fixture_list,
            on_config_updated=self._update_effects_config
        )
        self._stage_visualizer = StageVisualizer(
            visualizer_id="visualizer",
            width=940,
            height=400
        )
    
    def _update_effects_config(self) -> None:
        """Update effects engine with current config."""
        if self.effects_engine:
            self.effects_engine.update_config(self.config)
    
    def _update_gui_from_state(self) -> None:
        """
        Update GUI elements from shared state.
        Called from main thread during render loop.
        """
        # Get current state (thread-safe read)
        with self._state_lock:
            data = self._current_analysis
            fixture_states = self._fixture_states.copy() if self._fixture_states else {}
        
        if data is not None:
            try:
                self._update_gui(data)
            except Exception:
                pass
    
    def _get_last_used_channel(self) -> int:
        """Calculate the last DMX channel used by any fixture."""
        if not self.config.fixtures:
            return 0
        
        last_channel = 0
        for fixture in self.config.fixtures:
            profile = self.config.get_profile(fixture.profile_name) if fixture.profile_name else None
            channels = fixture.get_channels(profile)
            
            if channels:
                max_offset = max(ch.offset for ch in channels)
                fixture_end = fixture.start_channel + max_offset - 1
            else:
                fixture_end = fixture.start_channel
            
            last_channel = max(last_channel, fixture_end)
        
        return last_channel
    
    def _refresh_dmx_universe_display(self) -> None:
        """Refresh the DMX universe channel display based on configured fixtures."""
        if not dpg.does_item_exist("dmx_universe_container"):
            return
        
        dpg.delete_item("dmx_universe_container", children_only=True)
        
        last_channel = self._get_last_used_channel()
        
        if last_channel == 0:
            dpg.add_text("Add fixtures to see DMX channels", 
                        parent="dmx_universe_container", color=(100, 100, 120))
            return
        
        # Build a map of which fixture uses which channel for coloring
        channel_info: dict[int, tuple[str, str]] = {}  # channel -> (fixture_name, channel_name)
        for fixture in self.config.fixtures:
            profile = self.config.get_profile(fixture.profile_name) if fixture.profile_name else None
            channels = fixture.get_channels(profile)
            for ch in channels:
                dmx_ch = fixture.start_channel + ch.offset - 1
                channel_info[dmx_ch] = (fixture.name, ch.name)
        
        # Create channel display
        with dpg.group(horizontal=True, parent="dmx_universe_container", tag="dmx_channels"):
            for i in range(1, last_channel + 1):
                with dpg.group():
                    # Color the channel number based on whether it's used
                    if i in channel_info:
                        color = (100, 200, 100)  # Green for used channels
                    else:
                        color = (150, 150, 150)  # Gray for unused
                    dpg.add_text(f"{i}", color=color)
                    dpg.add_progress_bar(tag=f"dmx_ch_{i}", default_value=0.0, width=20)
        
        # Add legend
        dpg.add_spacer(height=5, parent="dmx_universe_container")
        dpg.add_text(f"Channels 1-{last_channel} ({last_channel} total)", 
                    parent="dmx_universe_container", color=(120, 120, 150))
    
    def run(self) -> None:
        if not DEARPYGUI_AVAILABLE:
            logger.error("Dear PyGui not available. Install with: pip install dearpygui")
            return
        
        dpg.create_context()
        dpg.create_viewport(title="Music Auto Show", width=1400, height=900)
        
        self._setup_theme()
        self._create_main_window()
        
        dpg.setup_dearpygui()
        dpg.show_viewport()
        
        self._running = True
        
        # Use manual render loop to update GUI from main thread
        while dpg.is_dearpygui_running():
            # Update GUI with latest state (main thread only)
            self._update_gui_from_state()
            dpg.render_dearpygui_frame()
        
        self._running = False
        self._stop_show()
        dpg.destroy_context()
    
    def _setup_theme(self) -> None:
        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
                dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 5)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 8, 4)
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (30, 30, 40))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (45, 45, 60))
                dpg.add_theme_color(dpg.mvThemeCol_Button, (70, 70, 100))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (90, 90, 130))
        dpg.bind_theme(global_theme)
    
    def _create_main_window(self) -> None:
        with dpg.window(label="Music Auto Show", tag="main_window", no_title_bar=True):
            dpg.set_primary_window("main_window", True)
            
            with dpg.menu_bar():
                with dpg.menu(label="File"):
                    dpg.add_menu_item(label="New Config", callback=self._new_config)
                    dpg.add_menu_item(label="Load Config", callback=self._load_config_dialog)
                    dpg.add_menu_item(label="Save Config", callback=self._save_config_dialog)
                    dpg.add_separator()
                    dpg.add_menu_item(label="Exit", callback=lambda: dpg.stop_dearpygui())
            
            with dpg.group(horizontal=True):
                with dpg.child_window(width=400, height=-1, border=True):
                    self._create_config_panel()
                
                with dpg.child_window(width=-1, height=-1, border=True):
                    self._create_visualization_panel()
    
    def _create_config_panel(self) -> None:
        dpg.add_text("Configuration", color=(200, 200, 255))
        dpg.add_separator()
        
        with dpg.group(horizontal=True):
            dpg.add_text("Show Name:")
            dpg.add_input_text(default_value=self.config.name, width=200,
                              callback=lambda s, a: setattr(self.config, 'name', a),
                              tag="show_name_input")
        
        dpg.add_spacer(height=10)
        
        with dpg.collapsing_header(label="DMX Settings", default_open=True):
            with dpg.group(horizontal=True):
                dpg.add_text("Port:")
                dpg.add_input_text(default_value=self.config.dmx.port, width=200,
                                  hint="Auto-detect if empty",
                                  callback=lambda s, a: setattr(self.config.dmx, 'port', a))
            
            dpg.add_checkbox(label="Simulate DMX (no hardware)", tag="simulate_dmx")
        
        dpg.add_spacer(height=10)
        
        with dpg.collapsing_header(label="Audio Input", default_open=True):
            # Audio input mode selector
            audio_modes = [
                ("System Audio (Loopback)", AudioInputMode.LOOPBACK),
                ("Microphone", AudioInputMode.MICROPHONE),
                ("Auto-detect", AudioInputMode.AUTO),
            ]
            audio_mode_names = [m[0] for m in audio_modes]
            dpg.add_combo(label="Input Source", items=audio_mode_names, 
                         default_value=audio_mode_names[2],  # Auto-detect
                         tag="audio_input_mode", width=200)
            dpg.add_text("Loopback captures what you hear, Microphone captures live audio",
                        color=(150, 150, 150))
            dpg.add_checkbox(label="Simulate Audio (no capture)", tag="simulate_audio")
        
        dpg.add_spacer(height=10)
        
        with dpg.collapsing_header(label="Fixtures", default_open=True):
            with dpg.group(horizontal=True):
                dpg.add_button(label="Add Fixture", callback=self._add_fixture_dialog)
                dpg.add_button(label="Remove Selected", callback=self._remove_fixture)
            
            dpg.add_separator()
            
            with dpg.child_window(height=200, border=True, tag="fixture_list_container"):
                self._fixture_list_id = dpg.add_group(tag="fixture_list")
                self._refresh_fixture_list()
        
        dpg.add_spacer(height=10)
        
        with dpg.collapsing_header(label="Effects", default_open=True):
            modes = [m.value for m in VisualizationMode]
            dpg.add_combo(label="Mode", items=modes, default_value=self.config.effects.mode.value,
                         callback=self._on_mode_changed, tag="effect_mode")
            
            dpg.add_slider_float(label="Intensity", default_value=self.config.effects.intensity,
                                min_value=0.0, max_value=1.0, width=200,
                                callback=lambda s, a: setattr(self.config.effects, 'intensity', a))
            
            dpg.add_slider_float(label="Color Speed", default_value=self.config.effects.color_speed,
                                min_value=0.1, max_value=10.0, width=200,
                                callback=lambda s, a: setattr(self.config.effects, 'color_speed', a))
            
            dpg.add_slider_float(label="Smoothing", default_value=self.config.effects.smooth_factor,
                                min_value=0.0, max_value=1.0, width=200,
                                callback=lambda s, a: setattr(self.config.effects, 'smooth_factor', a))
            
            dpg.add_checkbox(label="Strobe on Drop", default_value=self.config.effects.strobe_on_drop,
                            callback=lambda s, a: setattr(self.config.effects, 'strobe_on_drop', a))
            
            dpg.add_checkbox(label="Enable Movement", default_value=self.config.effects.movement_enabled,
                            callback=lambda s, a: setattr(self.config.effects, 'movement_enabled', a))
            
            # Movement mode selector
            movement_modes = [m.value for m in MovementMode]
            dpg.add_combo(label="Movement Mode", items=movement_modes, 
                         default_value=self.config.effects.movement_mode.value,
                         callback=self._on_movement_mode_changed, tag="movement_mode", width=200)
            dpg.add_text("", tag="movement_mode_hint", color=(130, 130, 160))
            self._update_movement_mode_hint(self.config.effects.movement_mode.value)
            
            dpg.add_slider_float(label="Movement Speed", default_value=self.config.effects.movement_speed,
                                min_value=0.0, max_value=1.0, width=200,
                                callback=lambda s, a: setattr(self.config.effects, 'movement_speed', a))
            
            dpg.add_separator()
            dpg.add_text("Effect Fixtures (Derby/Moonflower)", color=(180, 180, 200))
            
            # Rotation mode selector (Channel 3)
            rotation_modes = [m.value for m in RotationMode]
            dpg.add_combo(label="Rotation Mode", items=rotation_modes, 
                         default_value=self.config.effects.rotation_mode.value,
                         callback=self._on_rotation_mode_changed, tag="rotation_mode", width=200)
            dpg.add_text("", tag="rotation_mode_hint", color=(130, 130, 160))
            self._update_rotation_mode_hint(self.config.effects.rotation_mode.value)
            
            dpg.add_checkbox(label="Enable Strobe Effects", default_value=self.config.effects.strobe_effect_enabled,
                            callback=lambda s, a: setattr(self.config.effects, 'strobe_effect_enabled', a))
            
            # Strobe effect mode selector (Channel 4)
            strobe_effect_modes = [m.value for m in StrobeEffectMode]
            dpg.add_combo(label="Strobe Effect Pattern", items=strobe_effect_modes, 
                         default_value=self.config.effects.strobe_effect_mode.value,
                         callback=self._on_strobe_effect_mode_changed, tag="strobe_effect_mode", width=200)
            dpg.add_text("", tag="strobe_effect_mode_hint", color=(130, 130, 160))
            self._update_strobe_effect_mode_hint(self.config.effects.strobe_effect_mode.value)
            
            dpg.add_slider_float(label="Effect Speed", default_value=self.config.effects.strobe_effect_speed,
                                min_value=0.0, max_value=1.0, width=200,
                                callback=lambda s, a: setattr(self.config.effects, 'strobe_effect_speed', a))
    
    def _create_visualization_panel(self) -> None:
        with dpg.group(horizontal=True):
            dpg.add_button(label="Start Show", callback=self._start_show, width=120, height=40)
            dpg.add_button(label="Stop Show", callback=self._stop_show, width=120, height=40)
            dpg.add_button(label="Blackout", callback=self._blackout, width=100, height=40)
            dpg.add_spacer(width=20)
            self._status_text_id = dpg.add_text("Status: Stopped", color=(255, 200, 100))
        
        dpg.add_spacer(height=10)
        dpg.add_separator()
        
        dpg.add_text("Now Playing:", color=(200, 200, 255))
        self._track_info_id = dpg.add_text("No track playing", tag="track_info")
        
        # Album color palette and audio visualization display
        with dpg.group(horizontal=True):
            dpg.add_text("Album Colors:", color=(150, 150, 180))
            dpg.add_spacer(width=10)
            # Create 5 color swatches using drawlist
            with dpg.drawlist(width=200, height=20, tag="color_palette"):
                # Will be drawn in _update_gui
                pass
            
            dpg.add_spacer(width=20)
            # Audio analysis visualization (spectrum, beats, onset detection)
            with dpg.drawlist(width=500, height=80, tag="audio_viz_display"):
                # Will be drawn in _update_gui - shows how BPM/energy/etc are calculated
                pass
        
        dpg.add_spacer(height=10)
        
        with dpg.collapsing_header(label="Audio Analysis", default_open=True):
            with dpg.group(horizontal=True):
                with dpg.child_window(width=300, height=180, border=True):
                    dpg.add_text("Energy:")
                    dpg.add_progress_bar(tag="energy_bar", default_value=0.0, width=-1)
                    dpg.add_text("Bass:")
                    dpg.add_progress_bar(tag="bass_bar", default_value=0.0, width=-1)
                    dpg.add_text("Mid:")
                    dpg.add_progress_bar(tag="mid_bar", default_value=0.0, width=-1)
                    dpg.add_text("High:")
                    dpg.add_progress_bar(tag="high_bar", default_value=0.0, width=-1)
                
                with dpg.child_window(width=300, height=180, border=True):
                    dpg.add_text("Tempo:", tag="tempo_text")
                    dpg.add_progress_bar(tag="tempo_bar", default_value=0.5, width=-1)
                    dpg.add_text("Beat Position:")
                    dpg.add_progress_bar(tag="beat_bar", default_value=0.0, width=-1)
                    dpg.add_text("Danceability:")
                    dpg.add_progress_bar(tag="dance_bar", default_value=0.5, width=-1)
                    dpg.add_text("Valence:")
                    dpg.add_progress_bar(tag="valence_bar", default_value=0.5, width=-1)
        
        dpg.add_spacer(height=10)
        
        with dpg.collapsing_header(label="Stage View", default_open=True):
            with dpg.drawlist(width=940, height=400, tag="visualizer"):
                self._visualizer_id = "visualizer"
                dpg.draw_rectangle((0, 0), (940, 400), fill=(10, 10, 15))
                dpg.draw_text((420, 190), "Start show to see visualization", size=18, color=(60, 60, 80))
        
        dpg.add_spacer(height=10)
        
        with dpg.collapsing_header(label="DMX Universe", default_open=False):
            with dpg.child_window(height=120, border=True, horizontal_scrollbar=True, tag="dmx_universe_container"):
                dpg.add_text("Add fixtures to see DMX channels", tag="dmx_no_fixtures_text", color=(100, 100, 120))
    
    def _refresh_fixture_list(self) -> None:
        if self._fixture_list_id:
            dpg.delete_item(self._fixture_list_id, children_only=True)
            
            for i, fixture in enumerate(self.config.fixtures):
                profile_text = fixture.profile_name if fixture.profile_name else "Custom"
                with dpg.group(horizontal=True, parent=self._fixture_list_id):
                    dpg.add_selectable(
                        label=f"{fixture.name} [{profile_text}] (Ch {fixture.start_channel})",
                        width=350, tag=f"fixture_sel_{i}",
                        callback=self._on_fixture_selected,
                        user_data=fixture
                    )
        
        # Also refresh DMX universe display when fixtures change
        self._refresh_dmx_universe_display()
    
    def _add_fixture_dialog(self) -> None:
        """Show the add fixture dialog."""
        self._fixture_dialogs.show_add_fixture_dialog()
    
    def _on_fixture_selected(self, sender, app_data, user_data) -> None:
        """Handle fixture selection - open edit dialog."""
        if user_data is not None:
            self._fixture_dialogs.show_edit_fixture_dialog(user_data)
    
    def _remove_fixture(self) -> None:
        for i, fixture in enumerate(self.config.fixtures):
            if dpg.does_item_exist(f"fixture_sel_{i}"):
                if dpg.get_value(f"fixture_sel_{i}"):
                    self.config.fixtures.pop(i)
                    self._refresh_fixture_list()
                    break
    
    def _on_mode_changed(self, sender, app_data) -> None:
        self.config.effects.mode = VisualizationMode(app_data)
        if self.effects_engine:
            self.effects_engine.update_config(self.config)
    
    def _on_movement_mode_changed(self, sender, app_data) -> None:
        self.config.effects.movement_mode = MovementMode(app_data)
        self._update_movement_mode_hint(app_data)
        if self.effects_engine:
            self.effects_engine.update_config(self.config)
    
    def _update_movement_mode_hint(self, mode_value: str) -> None:
        """Update the movement mode hint text."""
        hints = {
            "subtle": "Small adjustments, stays mostly centered",
            "standard": "Moderate movement on beats/bars",
            "dramatic": "Full range, aggressive movement",
            "wall_wash": "Targets walls and corners",
            "sweep": "Slow continuous sweeping",
            "random": "Unpredictable positions",
        }
        hint = hints.get(mode_value, "")
        if dpg.does_item_exist("movement_mode_hint"):
            dpg.set_value("movement_mode_hint", hint)
    
    def _on_rotation_mode_changed(self, sender, app_data) -> None:
        """Handle rotation mode change."""
        self.config.effects.rotation_mode = RotationMode(app_data)
        self._update_rotation_mode_hint(app_data)
        if self.effects_engine:
            self.effects_engine.update_config(self.config)
    
    def _update_rotation_mode_hint(self, mode_value: str) -> None:
        """Update the rotation mode hint text."""
        hints = {
            "off": "No rotation",
            "manual_slow": "Slow smooth sweep through positions",
            "manual_beat": "Jump to new position on beats",
            "auto_slow": "Constant slow auto-rotation",
            "auto_medium": "Constant medium auto-rotation",
            "auto_fast": "Constant fast auto-rotation",
            "auto_music": "Auto-rotation speed follows energy",
        }
        hint = hints.get(mode_value, "")
        if dpg.does_item_exist("rotation_mode_hint"):
            dpg.set_value("rotation_mode_hint", hint)
    
    def _on_strobe_effect_mode_changed(self, sender, app_data) -> None:
        """Handle strobe effect mode change."""
        self.config.effects.strobe_effect_mode = StrobeEffectMode(app_data)
        self._update_strobe_effect_mode_hint(app_data)
        if self.effects_engine:
            self.effects_engine.update_config(self.config)
    
    def _update_strobe_effect_mode_hint(self, mode_value: str) -> None:
        """Update the strobe effect mode hint text."""
        hints = {
            "off": "No strobe effect pattern",
            "auto": "Automatically cycle effects based on music",
            "effect_1": "Pattern 1 - light movement",
            "effect_2": "Pattern 2 - light movement",
            "effect_3": "Pattern 3 - light movement",
            "effect_4": "Pattern 4 - light movement",
            "effect_5": "Pattern 5 - light movement",
            "effect_6": "Pattern 6 - light movement",
            "effect_7": "Pattern 7 - light movement",
            "effect_8": "Pattern 8 - light movement",
            "effect_9": "Pattern 9 - light movement",
            "effect_10": "Pattern 10 - light movement",
            "effect_11": "Pattern 11 - light movement",
            "effect_12": "Pattern 12 - light movement",
            "effect_13": "Pattern 13 - light movement",
            "effect_14": "Pattern 14 - light movement",
            "effect_15": "Pattern 15 - light movement",
            "effect_16": "Pattern 16 - light movement",
            "effect_17": "Pattern 17 - light movement",
            "effect_18_strobe": "All lights on (strobe mode)",
        }
        hint = hints.get(mode_value, "")
        if dpg.does_item_exist("strobe_effect_mode_hint"):
            dpg.set_value("strobe_effect_mode_hint", hint)
    
    def _start_show(self) -> None:
        logger.info("=" * 50)
        logger.info("STARTING SHOW")
        logger.info("=" * 50)
        
        simulate_dmx = dpg.get_value("simulate_dmx") if dpg.does_item_exist("simulate_dmx") else True
        simulate_audio = dpg.get_value("simulate_audio") if dpg.does_item_exist("simulate_audio") else False
        
        # Get audio input mode from combo
        audio_mode_map = {
            "System Audio (Loopback)": AudioInputMode.LOOPBACK,
            "Microphone": AudioInputMode.MICROPHONE,
            "Auto-detect": AudioInputMode.AUTO,
        }
        audio_mode_str = dpg.get_value("audio_input_mode") if dpg.does_item_exist("audio_input_mode") else "Auto-detect"
        audio_input_mode = audio_mode_map.get(audio_mode_str, AudioInputMode.AUTO)
        
        logger.info(f"Simulate DMX: {simulate_dmx}")
        logger.info(f"Simulate Audio: {simulate_audio}")
        logger.info(f"Audio Input Mode: {audio_input_mode.value}")
        logger.info(f"DMX Port: {self.config.dmx.port or '(auto-detect)'}")
        logger.info(f"Fixtures configured: {len(self.config.fixtures)}")
        
        if not self.config.fixtures:
            logger.warning("=" * 50)
            logger.warning("WARNING: No fixtures configured!")
            logger.warning("Add fixtures using the 'Add Fixture' button before starting.")
            logger.warning("Without fixtures, no DMX output will be generated.")
            logger.warning("=" * 50)
            dpg.set_value(self._status_text_id, "Status: No fixtures configured!")
            return
        
        for f in self.config.fixtures:
            logger.info(f"  - {f.name}: start_channel={f.start_channel}, profile={f.profile_name or 'custom'}")
        
        self.dmx_controller, self.dmx_interface = create_dmx_controller(
            port=self.config.dmx.port, simulate=simulate_dmx, fps=self.config.dmx.fps
        )
        
        logger.info("Opening DMX interface...")
        if not self.dmx_interface.open():
            logger.error("DMX connection failed!")
            dpg.set_value(self._status_text_id, "Status: DMX connection failed!")
            return
        
        logger.info("Starting DMX output...")
        if not self.dmx_controller.start():
            logger.error("DMX start failed!")
            dpg.set_value(self._status_text_id, "Status: DMX start failed!")
            return
        
        logger.info("Creating audio analyzer...")
        self.audio_analyzer = create_audio_analyzer(
            simulate=simulate_audio,
            input_mode=audio_input_mode
        )
        
        logger.info("Starting audio capture...")
        if not self.audio_analyzer.start():
            logger.error("Audio capture failed!")
            dpg.set_value(self._status_text_id, "Status: Audio capture failed!")
            self.dmx_controller.stop()
            self.dmx_interface.close()
            return
        
        logger.info("Creating effects engine...")
        self.effects_engine = EffectsEngine(self.dmx_controller, self.config)
        
        # Start effects processing thread (runs independently of GUI)
        self._effects_thread = threading.Thread(target=self._effects_loop, daemon=True)
        self._effects_thread.start()
        
        logger.info("=" * 50)
        logger.info("SHOW RUNNING")
        logger.info("=" * 50)
        
        dpg.set_value(self._status_text_id, "Status: Running")
    
    def _stop_show(self) -> None:
        # Stop effects thread first
        if self._effects_thread:
            self._effects_thread.join(timeout=1.0)
            self._effects_thread = None
        
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
        
        if self._status_text_id and dpg.does_item_exist(self._status_text_id):
            dpg.set_value(self._status_text_id, "Status: Stopped")
    
    def _blackout(self) -> None:
        if self.effects_engine:
            is_blackout = self.effects_engine.toggle_blackout()
            if self._status_text_id and dpg.does_item_exist(self._status_text_id):
                if is_blackout:
                    dpg.set_value(self._status_text_id, "Status: BLACKOUT")
                else:
                    dpg.set_value(self._status_text_id, "Status: Running")
    
    def _effects_loop(self) -> None:
        """
        Dedicated effects processing loop - runs in its own thread.
        Processes audio data and updates fixture states at ~30 FPS.
        Does NOT touch the GUI directly.
        """
        frame_count = 0
        last_debug_time = time.time()
        
        while self._running:
            if self.effects_engine and self.audio_analyzer:
                # Get audio analysis (thread-safe read)
                data = self.audio_analyzer.get_data()
                
                # Process effects (heavy computation)
                fixture_states = self.effects_engine.process(data)
                
                # Update shared state (thread-safe write)
                with self._state_lock:
                    self._current_analysis = data
                    self._fixture_states = fixture_states
                
                frame_count += 1
                
                # Debug logging every 5 seconds
                now = time.time()
                if now - last_debug_time >= 5.0:
                    # Log audio analysis
                    logger.info(f"Audio: energy={data.features.energy:.2f}, bass={data.features.bass:.2f}, "
                               f"tempo={data.features.tempo:.0f} BPM")
                    
                    # Log track and colors
                    if data.track_name and data.track_name != "System Audio":
                        logger.info(f"  Track: {data.artist_name} - {data.track_name}")
                        if data.album_colors:
                            colors_str = " ".join([f"#{r:02x}{g:02x}{b:02x}" for r, g, b in data.album_colors[:5]])
                            logger.info(f"  Album colors: {colors_str}")
                    
                    # Log fixture states
                    for name, state in fixture_states.items():
                        logger.info(f"  Fixture '{name}': R={state.red} G={state.green} B={state.blue} "
                                   f"Dimmer={state.dimmer} Pan={state.pan} Tilt={state.tilt} PTSpeed={state.pt_speed}")
                    
                    # Log actual DMX channel values
                    if self.dmx_controller:
                        channels = self.dmx_controller.get_all_channels()
                        non_zero = [(i+1, v) for i, v in enumerate(channels[:32]) if v > 0]
                        if non_zero:
                            logger.info(f"  DMX channels (1-32): {non_zero}")
                        else:
                            logger.info(f"  DMX channels 1-32: ALL ZERO")
                    
                    last_debug_time = now
            
            time.sleep(0.033)  # ~30 FPS
    
    def _update_gui(self, data: AnalysisData) -> None:
        if not dpg.is_dearpygui_running():
            return
        
        if data.track_name and data.track_name != "System Audio":
            if data.artist_name:
                track_text = f"{data.artist_name} - {data.track_name} ({data.features.tempo:.0f} BPM)"
            else:
                track_text = f"{data.track_name} ({data.features.tempo:.0f} BPM)"
        else:
            track_text = f"System Audio - {data.features.tempo:.0f} BPM"
        
        if dpg.does_item_exist("track_info"):
            dpg.set_value("track_info", track_text[:80])
        
        if dpg.does_item_exist("energy_bar"):
            dpg.set_value("energy_bar", data.features.energy)
        if dpg.does_item_exist("bass_bar"):
            dpg.set_value("bass_bar", data.features.bass)
        if dpg.does_item_exist("mid_bar"):
            dpg.set_value("mid_bar", data.features.mid)
        if dpg.does_item_exist("high_bar"):
            dpg.set_value("high_bar", data.features.high)
        if dpg.does_item_exist("dance_bar"):
            dpg.set_value("dance_bar", data.features.danceability)
        if dpg.does_item_exist("valence_bar"):
            dpg.set_value("valence_bar", data.features.valence)
        if dpg.does_item_exist("tempo_text"):
            dpg.set_value("tempo_text", f"Tempo: {data.features.tempo:.0f} BPM")
        if dpg.does_item_exist("tempo_bar"):
            dpg.set_value("tempo_bar", data.normalized_tempo)
        if dpg.does_item_exist("beat_bar"):
            dpg.set_value("beat_bar", data.beat_position)
        
        # Draw album color palette
        if dpg.does_item_exist("color_palette"):
            dpg.delete_item("color_palette", children_only=True)
            if data.album_colors:
                swatch_width = 35
                for i, color in enumerate(data.album_colors[:5]):
                    x = i * (swatch_width + 5)
                    dpg.draw_rectangle(
                        (x, 2), (x + swatch_width, 18),
                        fill=(color[0], color[1], color[2], 255),
                        color=(100, 100, 120),
                        thickness=1,
                        rounding=3,
                        parent="color_palette"
                    )
            else:
                dpg.draw_text((0, 3), "(no colors detected)", size=11, 
                             color=(100, 100, 120), parent="color_palette")
        
        # Draw comprehensive audio analysis visualization
        if dpg.does_item_exist("audio_viz_display"):
            dpg.delete_item("audio_viz_display", children_only=True)
            
            viz_width = 500
            viz_height = 80
            parent = "audio_viz_display"
            
            # Background
            dpg.draw_rectangle(
                (0, 0), (viz_width, viz_height),
                fill=(15, 15, 22, 255),
                color=(50, 50, 70),
                thickness=1,
                parent=parent
            )
            
            # Layout: [Spectrum 150px] [Beat/Onset 150px] [Frequency Bands 100px] [Beat Pulse 100px]
            import math
            
            # === Section 1: Frequency Spectrum (shows FFT - how bass/mid/high are calculated) ===
            spectrum_x = 2
            spectrum_width = 145
            spectrum_height = viz_height - 4
            
            # Use spectrum data if available, otherwise generate from bass/mid/high
            spectrum_data = data.spectrum if (hasattr(data, 'spectrum') and data.spectrum and len(data.spectrum) > 0) else None
            
            if spectrum_data:
                num_bands = len(spectrum_data)
                bar_width = max(2, spectrum_width / num_bands)
                
                for i, value in enumerate(spectrum_data):
                    x = spectrum_x + i * bar_width
                    bar_h = value * (spectrum_height - 2)
                    
                    # Color gradient: blue (low) -> cyan -> green -> yellow -> red (high)
                    pos = i / max(1, num_bands - 1)
                    if pos < 0.33:
                        r, g, b = 50, int(100 + pos * 3 * 155), 255
                    elif pos < 0.66:
                        p = (pos - 0.33) * 3
                        r, g, b = int(p * 255), 255, int(255 * (1 - p))
                    else:
                        p = (pos - 0.66) * 3
                        r, g, b = 255, int(255 * (1 - p)), 50
                    
                    if bar_h > 0.5:
                        dpg.draw_rectangle(
                            (x, spectrum_height - bar_h + 2), (x + bar_width - 1, spectrum_height),
                            fill=(r, g, b, 220),
                            parent=parent
                        )
            else:
                # Fallback: generate spectrum-like display from bass/mid/high
                num_bands = 24
                bar_width = spectrum_width / num_bands
                bass, mid, high = data.features.bass, data.features.mid, data.features.high
                
                for i in range(num_bands):
                    pos = i / (num_bands - 1)
                    # Blend values based on position
                    if pos < 0.33:
                        value = bass * (1 - pos * 3) + mid * (pos * 3)
                    elif pos < 0.66:
                        value = mid * (1 - (pos - 0.33) * 3) + high * ((pos - 0.33) * 3)
                    else:
                        value = high * (1 - (pos - 0.66) * 2)
                    
                    # Add some variation
                    value *= 0.7 + 0.3 * math.sin(i * 0.8 + data.beat_position * 6.28)
                    
                    x = spectrum_x + i * bar_width
                    bar_h = max(2, value * (spectrum_height - 4))
                    
                    if pos < 0.33:
                        r, g, b = 50, int(100 + pos * 3 * 155), 255
                    elif pos < 0.66:
                        p = (pos - 0.33) * 3
                        r, g, b = int(p * 255), 255, int(255 * (1 - p))
                    else:
                        p = (pos - 0.66) * 3
                        r, g, b = 255, int(255 * (1 - p * 0.8)), 50
                    
                    dpg.draw_rectangle(
                        (x, spectrum_height - bar_h + 2), (x + bar_width - 1, spectrum_height),
                        fill=(r, g, b, 200),
                        parent=parent
                    )
            
            # Spectrum separator line
            dpg.draw_line((150, 0), (150, viz_height), color=(50, 50, 70), thickness=1, parent=parent)
            
            # === Section 2: Onset Detection History (shows how beats are detected) ===
            onset_x = 152
            onset_width = 145
            onset_height = viz_height - 4
            
            onset_data = data.onset_history if (hasattr(data, 'onset_history') and data.onset_history and len(data.onset_history) > 0) else None
            
            if onset_data:
                num_points = len(onset_data)
                point_width = onset_width / num_points
                
                # Draw onset strength as a line graph
                points = []
                for i, value in enumerate(onset_data):
                    x = onset_x + i * point_width
                    y = onset_height - value * (onset_height - 8) + 2
                    points.append((x, y))
                
                # Draw filled area under the curve
                if len(points) >= 2:
                    for i in range(len(points) - 1):
                        x1, y1 = points[i]
                        x2, y2 = points[i + 1]
                        dpg.draw_line(
                            (x1, y1), (x1, onset_height),
                            color=(100, 180, 255, 60),
                            thickness=max(1, int(point_width) + 1),
                            parent=parent
                        )
                        dpg.draw_line(
                            (x1, y1), (x2, y2),
                            color=(100, 200, 255, 255),
                            thickness=2,
                            parent=parent
                        )
            else:
                # Fallback: show energy-based wave with beat pulses
                num_points = 32
                point_width = onset_width / num_points
                energy = data.features.energy
                beat_pos = data.beat_position
                
                for i in range(num_points):
                    # Simulate onset history - peaks at regular intervals
                    pos = i / num_points
                    # Create wave with beat-synced peaks
                    wave = 0.3 + 0.4 * math.sin(pos * 12.56 + beat_pos * 6.28)
                    wave *= energy
                    # Add decay from recent "beat"
                    if pos > 0.8:
                        wave += (1 - beat_pos) * 0.5 * ((pos - 0.8) / 0.2)
                    
                    x = onset_x + i * point_width
                    bar_h = max(2, wave * (onset_height - 8))
                    
                    dpg.draw_rectangle(
                        (x, onset_height - bar_h), (x + point_width - 1, onset_height),
                        fill=(100, 180, 255, 150),
                        parent=parent
                    )
                
                # Draw threshold line
                threshold_y = onset_height - 0.3 * (onset_height - 8)
                dpg.draw_line(
                    (onset_x, threshold_y), (onset_x + onset_width, threshold_y),
                    color=(255, 100, 100, 150),
                    thickness=1,
                    parent=parent
                )
            
            # Onset separator line
            dpg.draw_line((300, 0), (300, viz_height), color=(50, 50, 70), thickness=1, parent=parent)
            
            # === Section 3: Frequency Bands (bass/mid/high as vertical bars) ===
            bands_x = 305
            bands_width = 90
            band_width = 25
            band_gap = 5
            band_height = viz_height - 8
            
            # Bass bar (red/orange)
            bass_h = data.features.bass * band_height
            dpg.draw_rectangle(
                (bands_x, band_height - bass_h + 4), (bands_x + band_width, band_height + 4),
                fill=(255, 80, 50, 230),
                parent=parent
            )
            
            # Mid bar (green/yellow)
            mid_x = bands_x + band_width + band_gap
            mid_h = data.features.mid * band_height
            dpg.draw_rectangle(
                (mid_x, band_height - mid_h + 4), (mid_x + band_width, band_height + 4),
                fill=(150, 255, 50, 230),
                parent=parent
            )
            
            # High bar (cyan/blue)
            high_x = mid_x + band_width + band_gap
            high_h = data.features.high * band_height
            dpg.draw_rectangle(
                (high_x, band_height - high_h + 4), (high_x + band_width, band_height + 4),
                fill=(50, 200, 255, 230),
                parent=parent
            )
            
            # Bands separator line
            dpg.draw_line((400, 0), (400, viz_height), color=(50, 50, 70), thickness=1, parent=parent)
            
            # === Section 4: Beat Pulse Indicator (shows beat position and tempo) ===
            pulse_x = 405
            pulse_width = 92
            pulse_center_x = pulse_x + pulse_width / 2
            pulse_center_y = viz_height / 2
            
            # Beat position as expanding/contracting circle
            beat_pos = data.beat_position  # 0-1 within beat
            
            # Pulse size: large at beat start, shrinks through beat
            max_radius = min(pulse_width, viz_height) / 2 - 4
            pulse_radius = max_radius * (1.0 - beat_pos * 0.6)
            
            # Color intensity based on beat position
            intensity = int(255 * (1.0 - beat_pos * 0.7))
            
            # Draw outer glow
            if pulse_radius > 5:
                dpg.draw_circle(
                    (pulse_center_x, pulse_center_y), pulse_radius + 3,
                    fill=(intensity // 3, intensity // 2, intensity, 100),
                    parent=parent
                )
            
            # Draw main pulse circle
            dpg.draw_circle(
                (pulse_center_x, pulse_center_y), pulse_radius,
                fill=(intensity // 2, intensity, intensity // 2, 200),
                color=(200, 255, 200, intensity),
                thickness=2,
                parent=parent
            )
            
            # Draw beat position arc (shows progress through beat)
            arc_radius = max_radius + 4
            arc_angle = beat_pos * 360
            if arc_angle > 5:
                # Draw arc as small line segments, starting from top (12 o'clock)
                start_angle = -90  # Start from top
                prev_rad = math.radians(start_angle)
                prev_x = pulse_center_x + arc_radius * math.cos(prev_rad)
                prev_y = pulse_center_y + arc_radius * math.sin(prev_rad)
                
                # Draw arc segments
                step = 10  # Degrees per segment
                for angle in range(step, int(arc_angle) + step, step):
                    actual_angle = min(angle, arc_angle)
                    rad = math.radians(start_angle + actual_angle)
                    x = pulse_center_x + arc_radius * math.cos(rad)
                    y = pulse_center_y + arc_radius * math.sin(rad)
                    dpg.draw_line(
                        (prev_x, prev_y), (x, y),
                        color=(255, 200, 100, 220),
                        thickness=3,
                        parent=parent
                    )
                    prev_x, prev_y = x, y
        
        if self.dmx_controller:
            channels = self.dmx_controller.get_all_channels()
            last_channel = self._get_last_used_channel()
            for i in range(min(last_channel, len(channels))):
                if dpg.does_item_exist(f"dmx_ch_{i+1}"):
                    dpg.set_value(f"dmx_ch_{i+1}", channels[i] / 255.0)
        
        # Draw the stage visualizer
        self._stage_visualizer.draw(
            self.config.fixtures,
            self._fixture_states,
            self._current_analysis
        )
    
    def _new_config(self) -> None:
        self.config = ShowConfig()
        self._fixture_dialogs.config = self.config
        self._refresh_fixture_list()
        if dpg.does_item_exist("show_name_input"):
            dpg.set_value("show_name_input", self.config.name)
    
    def _load_config_dialog(self) -> None:
        with dpg.file_dialog(directory_selector=False, show=True, callback=self._load_config_callback, width=600, height=400):
            dpg.add_file_extension(".json", color=(0, 255, 0))
    
    def _load_config_callback(self, sender, app_data) -> None:
        if app_data and 'file_path_name' in app_data:
            try:
                self.config = ShowConfig.load(app_data['file_path_name'])
                self._fixture_dialogs.config = self.config
                self._refresh_fixture_list()
                if dpg.does_item_exist("show_name_input"):
                    dpg.set_value("show_name_input", self.config.name)
            except Exception as e:
                logger.error(f"Failed to load config: {e}")
    
    def _save_config_dialog(self) -> None:
        with dpg.file_dialog(directory_selector=False, show=True, callback=self._save_config_callback,
                            default_filename="show_config.json", width=600, height=400):
            dpg.add_file_extension(".json", color=(0, 255, 0))
    
    def _save_config_callback(self, sender, app_data) -> None:
        if app_data and 'file_path_name' in app_data:
            try:
                self.config.save(app_data['file_path_name'])
            except Exception as e:
                logger.error(f"Failed to save config: {e}")


def run_gui():
    app = MusicAutoShowGUI()
    app.run()


if __name__ == "__main__":
    run_gui()
