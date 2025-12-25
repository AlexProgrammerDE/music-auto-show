"""
GUI for Music Auto Show using Dear PyGui.
Provides fixture configuration, live visualization, and effect controls.
"""
import json
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
    ShowConfig, FixtureConfig, FixtureProfile, ChannelOverride, ChannelMapping,
    VisualizationMode, DMXConfig, EffectsConfig, ChannelFunction,
    get_available_presets, get_preset, FIXTURE_PRESETS
)
from dmx_controller import DMXController, create_dmx_controller, SimulatedDMXInterface
from audio_analyzer import AnalysisData, AudioAnalyzer, create_audio_analyzer
from effects_engine import EffectsEngine, FixtureState


class MusicAutoShowGUI:
    """
    Main GUI application for Music Auto Show.
    """
    
    def __init__(self):
        self.config = ShowConfig()
        self.dmx_controller: Optional[DMXController] = None
        self.dmx_interface = None
        self.audio_analyzer = None
        self.effects_engine: Optional[EffectsEngine] = None
        
        self._running = False
        self._update_thread: Optional[threading.Thread] = None
        self._fixture_states: dict[str, FixtureState] = {}
        self._current_analysis: Optional[AnalysisData] = None
        
        # GUI element IDs
        self._fixture_list_id = None
        self._visualizer_id = None
        self._status_text_id = None
        self._track_info_id = None
    
    def run(self) -> None:
        """Run the GUI application."""
        if not DEARPYGUI_AVAILABLE:
            print("Dear PyGui not available. Install with: pip install dearpygui")
            return
        
        dpg.create_context()
        dpg.create_viewport(title="Music Auto Show", width=1400, height=900)
        
        self._setup_theme()
        self._create_main_window()
        
        dpg.setup_dearpygui()
        dpg.show_viewport()
        
        # Start update loop
        self._running = True
        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()
        
        dpg.start_dearpygui()
        
        # Cleanup
        self._running = False
        self._stop_show()
        dpg.destroy_context()
    
    def _setup_theme(self) -> None:
        """Setup GUI theme."""
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
        """Create main application window."""
        with dpg.window(label="Music Auto Show", tag="main_window", no_title_bar=True):
            dpg.set_primary_window("main_window", True)
            
            # Menu bar
            with dpg.menu_bar():
                with dpg.menu(label="File"):
                    dpg.add_menu_item(label="New Config", callback=self._new_config)
                    dpg.add_menu_item(label="Load Config", callback=self._load_config_dialog)
                    dpg.add_menu_item(label="Save Config", callback=self._save_config_dialog)
                    dpg.add_separator()
                    dpg.add_menu_item(label="Export JSON", callback=self._export_json)
                    dpg.add_separator()
                    dpg.add_menu_item(label="Exit", callback=lambda: dpg.stop_dearpygui())
                
                with dpg.menu(label="View"):
                    dpg.add_menu_item(label="Reset Layout", callback=self._reset_layout)
            
            # Main layout with columns
            with dpg.group(horizontal=True):
                # Left panel - Configuration
                with dpg.child_window(width=400, height=-1, border=True):
                    self._create_config_panel()
                
                # Right panel - Visualization and controls
                with dpg.child_window(width=-1, height=-1, border=True):
                    self._create_visualization_panel()
    
    def _create_config_panel(self) -> None:
        """Create configuration panel."""
        dpg.add_text("Configuration", color=(200, 200, 255))
        dpg.add_separator()
        
        # Show name
        with dpg.group(horizontal=True):
            dpg.add_text("Show Name:")
            dpg.add_input_text(default_value=self.config.name, width=200,
                              callback=lambda s, a: setattr(self.config, 'name', a),
                              tag="show_name_input")
        
        dpg.add_spacer(height=10)
        
        # DMX Configuration
        with dpg.collapsing_header(label="DMX Settings", default_open=True):
            with dpg.group(horizontal=True):
                dpg.add_text("Port:")
                dpg.add_input_text(default_value=self.config.dmx.port, width=200,
                                  hint="Auto-detect if empty",
                                  callback=lambda s, a: setattr(self.config.dmx, 'port', a),
                                  tag="dmx_port_input")
            
            with dpg.group(horizontal=True):
                dpg.add_text("FPS:")
                dpg.add_slider_int(default_value=self.config.dmx.fps, min_value=1, max_value=44,
                                  width=150, callback=lambda s, a: setattr(self.config.dmx, 'fps', a))
            
            dpg.add_checkbox(label="Simulate DMX (no hardware)", tag="simulate_dmx",
                            callback=self._on_simulate_changed)
        
        dpg.add_spacer(height=10)
        
        # Audio Input Configuration
        with dpg.collapsing_header(label="Audio Input", default_open=True):
            dpg.add_text("Captures system audio (WASAPI loopback)")
            dpg.add_checkbox(label="Simulate Audio (no capture)", tag="simulate_audio",
                            callback=self._on_simulate_changed)
            dpg.add_button(label="List Audio Devices", callback=self._list_audio_devices)
        
        dpg.add_spacer(height=10)
        
        # Fixtures
        with dpg.collapsing_header(label="Fixtures", default_open=True):
            with dpg.group(horizontal=True):
                dpg.add_button(label="Add Fixture", callback=self._add_fixture_dialog)
                dpg.add_button(label="Remove Selected", callback=self._remove_fixture)
            
            dpg.add_separator()
            
            # Fixture list
            with dpg.child_window(height=200, border=True, tag="fixture_list_container"):
                self._fixture_list_id = dpg.add_group(tag="fixture_list")
                self._refresh_fixture_list()
        
        dpg.add_spacer(height=10)
        
        # Effects Configuration
        with dpg.collapsing_header(label="Effects", default_open=True):
            # Mode selection
            modes = [m.value for m in VisualizationMode]
            dpg.add_combo(label="Mode", items=modes, default_value=self.config.effects.mode.value,
                         callback=self._on_mode_changed, tag="effect_mode")
            
            dpg.add_slider_float(label="Intensity", default_value=self.config.effects.intensity,
                                min_value=0.0, max_value=1.0, width=200,
                                callback=lambda s, a: setattr(self.config.effects, 'intensity', a))
            
            dpg.add_slider_float(label="Color Speed", default_value=self.config.effects.color_speed,
                                min_value=0.1, max_value=10.0, width=200,
                                callback=lambda s, a: setattr(self.config.effects, 'color_speed', a))
            
            dpg.add_slider_float(label="Beat Sensitivity", default_value=self.config.effects.beat_sensitivity,
                                min_value=0.0, max_value=1.0, width=200,
                                callback=lambda s, a: setattr(self.config.effects, 'beat_sensitivity', a))
            
            dpg.add_slider_float(label="Smoothing", default_value=self.config.effects.smooth_factor,
                                min_value=0.0, max_value=1.0, width=200,
                                callback=lambda s, a: setattr(self.config.effects, 'smooth_factor', a))
            
            dpg.add_checkbox(label="Strobe on Drop", default_value=self.config.effects.strobe_on_drop,
                            callback=lambda s, a: setattr(self.config.effects, 'strobe_on_drop', a))
            
            dpg.add_checkbox(label="Enable Movement", default_value=self.config.effects.movement_enabled,
                            callback=lambda s, a: setattr(self.config.effects, 'movement_enabled', a))
            
            dpg.add_slider_float(label="Movement Speed", default_value=self.config.effects.movement_speed,
                                min_value=0.0, max_value=1.0, width=200,
                                callback=lambda s, a: setattr(self.config.effects, 'movement_speed', a))
    
    def _create_visualization_panel(self) -> None:
        """Create visualization panel."""
        # Control buttons
        with dpg.group(horizontal=True):
            dpg.add_button(label="Start Show", callback=self._start_show, tag="start_btn",
                          width=120, height=40)
            dpg.add_button(label="Stop Show", callback=self._stop_show, tag="stop_btn",
                          width=120, height=40)
            dpg.add_button(label="Blackout", callback=self._blackout, width=100, height=40)
            
            dpg.add_spacer(width=20)
            self._status_text_id = dpg.add_text("Status: Stopped", color=(255, 200, 100))
        
        dpg.add_spacer(height=10)
        dpg.add_separator()
        
        # Track info
        dpg.add_text("Now Playing:", color=(200, 200, 255))
        self._track_info_id = dpg.add_text("No track playing", tag="track_info")
        
        dpg.add_spacer(height=10)
        
        # Audio features display
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
        
        # Live visualizer
        with dpg.collapsing_header(label="Fixture Visualizer", default_open=True):
            # Create drawing canvas
            with dpg.drawlist(width=900, height=300, tag="visualizer"):
                self._visualizer_id = "visualizer"
                # Initial placeholder
                dpg.draw_rectangle((0, 0), (900, 300), fill=(30, 30, 40))
                dpg.draw_text((400, 140), "Start show to see visualization", size=16,
                             color=(100, 100, 100))
        
        dpg.add_spacer(height=10)
        
        # DMX Universe view
        with dpg.collapsing_header(label="DMX Universe", default_open=False):
            with dpg.child_window(height=100, border=True, horizontal_scrollbar=True):
                with dpg.group(horizontal=True, tag="dmx_channels"):
                    for i in range(1, 33):  # Show first 32 channels
                        with dpg.group():
                            dpg.add_text(f"{i}", color=(150, 150, 150))
                            dpg.add_progress_bar(tag=f"ch_{i}", default_value=0.0, width=20)
    
    def _refresh_fixture_list(self) -> None:
        """Refresh the fixture list display."""
        if self._fixture_list_id:
            dpg.delete_item(self._fixture_list_id, children_only=True)
            
            for i, fixture in enumerate(self.config.fixtures):
                with dpg.group(horizontal=True, parent=self._fixture_list_id):
                    dpg.add_selectable(label=f"{fixture.name} [{fixture.profile_name}] (Ch {fixture.start_channel})",
                                       width=350, tag=f"fixture_sel_{i}",
                                       callback=self._on_fixture_selected,
                                       user_data=fixture)
    
    def _add_fixture_dialog(self) -> None:
        """Show dialog to add a new fixture."""
        if dpg.does_item_exist("add_fixture_window"):
            dpg.delete_item("add_fixture_window")
        
        with dpg.window(label="Add Fixture", modal=True, tag="add_fixture_window",
                       width=500, height=400, pos=(400, 100)):
            dpg.add_input_text(label="Name", tag="new_fixture_name", default_value="New Fixture")
            
            # Profile selection (presets)
            preset_names = get_available_presets()
            dpg.add_combo(label="Fixture Profile", items=preset_names, 
                         default_value=preset_names[0] if preset_names else "",
                         tag="new_fixture_profile", width=300)
            
            dpg.add_input_int(label="Start Channel", tag="new_fixture_start", default_value=1,
                             min_value=1, max_value=512)
            dpg.add_input_int(label="Position", tag="new_fixture_position", default_value=0)
            
            dpg.add_separator()
            dpg.add_text("Movement Limits (optional):")
            
            with dpg.group(horizontal=True):
                dpg.add_input_int(label="Pan Min", tag="new_pan_min", default_value=0, width=80)
                dpg.add_input_int(label="Pan Max", tag="new_pan_max", default_value=255, width=80)
            
            with dpg.group(horizontal=True):
                dpg.add_input_int(label="Tilt Min", tag="new_tilt_min", default_value=0, width=80)
                dpg.add_input_int(label="Tilt Max", tag="new_tilt_max", default_value=255, width=80)
            
            dpg.add_slider_float(label="Intensity Scale", tag="new_intensity_scale", 
                                default_value=1.0, min_value=0.0, max_value=1.0, width=200)
            
            dpg.add_separator()
            with dpg.group(horizontal=True):
                dpg.add_button(label="Add", callback=self._add_fixture_confirm, width=100)
                dpg.add_button(label="Cancel", callback=lambda: dpg.delete_item("add_fixture_window"),
                              width=100)
    
    def _add_fixture_confirm(self) -> None:
        """Confirm adding a new fixture."""
        name = dpg.get_value("new_fixture_name")
        profile_name = dpg.get_value("new_fixture_profile")
        start_channel = dpg.get_value("new_fixture_start")
        position = dpg.get_value("new_fixture_position")
        
        fixture = FixtureConfig(
            name=name,
            profile_name=profile_name,
            start_channel=start_channel,
            position=position,
            pan_min=dpg.get_value("new_pan_min"),
            pan_max=dpg.get_value("new_pan_max"),
            tilt_min=dpg.get_value("new_tilt_min"),
            tilt_max=dpg.get_value("new_tilt_max"),
            intensity_scale=dpg.get_value("new_intensity_scale")
        )
        
        self.config.fixtures.append(fixture)
        self._refresh_fixture_list()
        
        # Update effects engine if running
        if self.effects_engine:
            self.effects_engine.update_config(self.config)
        
        dpg.delete_item("add_fixture_window")
    
    def _on_fixture_selected(self, sender, app_data, user_data) -> None:
        """Handle fixture selection from list."""
        if user_data is not None:
            self._edit_fixture(user_data)
    
    def _edit_fixture(self, fixture: FixtureConfig) -> None:
        """Edit an existing fixture with full channel control."""
        if fixture is None:
            return
        
        # Store reference to fixture being edited
        self._editing_fixture = fixture
        
        if dpg.does_item_exist("edit_fixture_window"):
            dpg.delete_item("edit_fixture_window")
        
        with dpg.window(label=f"Edit Fixture: {fixture.name}", modal=True, 
                       tag="edit_fixture_window", width=700, height=600, pos=(300, 50)):
            
            # Basic settings
            with dpg.collapsing_header(label="Basic Settings", default_open=True):
                dpg.add_input_text(label="Name", default_value=fixture.name, 
                                  tag="edit_fixture_name", width=200)
                dpg.add_input_int(label="Start Channel", default_value=fixture.start_channel,
                                 tag="edit_fixture_start", min_value=1, max_value=512, width=100)
                dpg.add_input_int(label="Position", default_value=fixture.position,
                                 tag="edit_fixture_position", width=100)
                dpg.add_slider_float(label="Intensity Scale", default_value=fixture.intensity_scale,
                                    tag="edit_fixture_intensity", min_value=0.0, max_value=1.0, width=200)
            
            # Profile selection
            with dpg.collapsing_header(label="Fixture Profile", default_open=True):
                preset_names = ["(Custom)"] + get_available_presets()
                current_profile = fixture.profile_name if fixture.profile_name else "(Custom)"
                dpg.add_combo(label="Profile", items=preset_names, default_value=current_profile,
                             tag="edit_fixture_profile", width=300,
                             callback=self._on_profile_changed)
                
                dpg.add_text("Profile provides default channel mappings.", color=(150, 150, 150))
                dpg.add_text("You can override individual channels below.", color=(150, 150, 150))
            
            # Movement limits
            with dpg.collapsing_header(label="Movement Limits", default_open=False):
                with dpg.group(horizontal=True):
                    dpg.add_input_int(label="Pan Min", default_value=fixture.pan_min,
                                     tag="edit_pan_min", width=80)
                    dpg.add_input_int(label="Pan Max", default_value=fixture.pan_max,
                                     tag="edit_pan_max", width=80)
                with dpg.group(horizontal=True):
                    dpg.add_input_int(label="Tilt Min", default_value=fixture.tilt_min,
                                     tag="edit_tilt_min", width=80)
                    dpg.add_input_int(label="Tilt Max", default_value=fixture.tilt_max,
                                     tag="edit_tilt_max", width=80)
            
            # Channel Overrides
            with dpg.collapsing_header(label="Channel Overrides (Force Values)", default_open=True):
                dpg.add_text("Force specific channels to fixed DMX values.", color=(150, 150, 150))
                dpg.add_text("Leave value empty for dynamic control.", color=(150, 150, 150))
                
                dpg.add_separator()
                
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Add Override", callback=self._add_channel_override)
                
                dpg.add_separator()
                
                # Override list container
                with dpg.child_window(height=150, border=True, tag="override_list_container"):
                    dpg.add_group(tag="override_list")
                    self._refresh_override_list(fixture)
            
            # Custom Channels (for custom fixtures)
            with dpg.collapsing_header(label="Custom Channels", default_open=False):
                dpg.add_text("Define custom channel mappings (for fixtures without a profile).", 
                            color=(150, 150, 150))
                
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Add Channel", callback=self._add_custom_channel)
                
                dpg.add_separator()
                
                with dpg.child_window(height=120, border=True, tag="custom_channel_list_container"):
                    dpg.add_group(tag="custom_channel_list")
                    self._refresh_custom_channel_list(fixture)
            
            dpg.add_separator()
            
            # Buttons
            with dpg.group(horizontal=True):
                dpg.add_button(label="Save", callback=self._save_fixture_edit, width=100)
                dpg.add_button(label="Cancel", 
                              callback=lambda: dpg.delete_item("edit_fixture_window"), width=100)
    
    def _on_profile_changed(self, sender, app_data) -> None:
        """Handle profile selection change in edit dialog."""
        pass  # Profile change is handled on save
    
    def _refresh_override_list(self, fixture: FixtureConfig) -> None:
        """Refresh the channel override list in the edit dialog."""
        if not dpg.does_item_exist("override_list"):
            return
        
        dpg.delete_item("override_list", children_only=True)
        
        for i, override in enumerate(fixture.channel_overrides):
            with dpg.group(horizontal=True, parent="override_list"):
                dpg.add_text(f"Ch {override.offset}:")
                if override.force_value is not None:
                    dpg.add_text(f"= {override.force_value}", color=(255, 200, 100))
                else:
                    func_name = override.function.value if override.function else "default"
                    dpg.add_text(f"-> {func_name}", color=(100, 200, 100))
                dpg.add_button(label="X", callback=lambda s, a, idx=i: self._remove_override(idx),
                              width=30)
    
    def _add_channel_override(self) -> None:
        """Show dialog to add a channel override."""
        if dpg.does_item_exist("add_override_window"):
            dpg.delete_item("add_override_window")
        
        with dpg.window(label="Add Channel Override", modal=True, tag="add_override_window",
                       width=400, height=250, pos=(450, 200)):
            dpg.add_input_int(label="Channel Offset", tag="override_offset", default_value=1,
                             min_value=1, max_value=512, width=100)
            
            dpg.add_separator()
            dpg.add_text("Choose ONE option:")
            
            dpg.add_checkbox(label="Force to fixed value", tag="override_force_check",
                            callback=self._toggle_override_mode)
            dpg.add_input_int(label="DMX Value (0-255)", tag="override_force_value", 
                             default_value=0, min_value=0, max_value=255, width=100)
            
            dpg.add_separator()
            
            dpg.add_checkbox(label="Change function", tag="override_func_check",
                            callback=self._toggle_override_mode)
            functions = [f.value for f in ChannelFunction]
            dpg.add_combo(label="Function", items=functions, default_value=functions[0],
                         tag="override_function", width=200)
            
            dpg.add_separator()
            
            with dpg.group(horizontal=True):
                dpg.add_button(label="Add", callback=self._confirm_add_override, width=80)
                dpg.add_button(label="Cancel", 
                              callback=lambda: dpg.delete_item("add_override_window"), width=80)
    
    def _toggle_override_mode(self, sender, app_data) -> None:
        """Toggle between force value and function override modes."""
        if sender == "override_force_check" and app_data:
            dpg.set_value("override_func_check", False)
        elif sender == "override_func_check" and app_data:
            dpg.set_value("override_force_check", False)
    
    def _confirm_add_override(self) -> None:
        """Confirm adding a channel override."""
        if not hasattr(self, '_editing_fixture') or self._editing_fixture is None:
            return
        
        offset = dpg.get_value("override_offset")
        force_check = dpg.get_value("override_force_check")
        func_check = dpg.get_value("override_func_check")
        
        override = ChannelOverride(offset=offset)
        
        if force_check:
            override.force_value = dpg.get_value("override_force_value")
        elif func_check:
            override.function = ChannelFunction(dpg.get_value("override_function"))
        
        # Remove existing override for same offset
        self._editing_fixture.channel_overrides = [
            o for o in self._editing_fixture.channel_overrides if o.offset != offset
        ]
        self._editing_fixture.channel_overrides.append(override)
        
        self._refresh_override_list(self._editing_fixture)
        dpg.delete_item("add_override_window")
    
    def _remove_override(self, index: int) -> None:
        """Remove a channel override."""
        if hasattr(self, '_editing_fixture') and self._editing_fixture:
            if 0 <= index < len(self._editing_fixture.channel_overrides):
                self._editing_fixture.channel_overrides.pop(index)
                self._refresh_override_list(self._editing_fixture)
    
    def _refresh_custom_channel_list(self, fixture: FixtureConfig) -> None:
        """Refresh the custom channel list."""
        if not dpg.does_item_exist("custom_channel_list"):
            return
        
        dpg.delete_item("custom_channel_list", children_only=True)
        
        for i, ch in enumerate(fixture.custom_channels):
            with dpg.group(horizontal=True, parent="custom_channel_list"):
                dpg.add_text(f"Ch {ch.offset}: {ch.function.value}")
                dpg.add_button(label="X", callback=lambda s, a, idx=i: self._remove_custom_channel(idx),
                              width=30)
    
    def _add_custom_channel(self) -> None:
        """Show dialog to add a custom channel."""
        if dpg.does_item_exist("add_channel_window"):
            dpg.delete_item("add_channel_window")
        
        with dpg.window(label="Add Custom Channel", modal=True, tag="add_channel_window",
                       width=350, height=200, pos=(450, 200)):
            dpg.add_input_int(label="Channel Offset", tag="channel_offset", default_value=1,
                             min_value=1, max_value=512, width=100)
            
            functions = [f.value for f in ChannelFunction]
            dpg.add_combo(label="Function", items=functions, default_value=functions[0],
                         tag="channel_function", width=200)
            
            dpg.add_input_int(label="Default Value", tag="channel_default", default_value=0,
                             min_value=0, max_value=255, width=100)
            
            dpg.add_separator()
            
            with dpg.group(horizontal=True):
                dpg.add_button(label="Add", callback=self._confirm_add_channel, width=80)
                dpg.add_button(label="Cancel", 
                              callback=lambda: dpg.delete_item("add_channel_window"), width=80)
    
    def _confirm_add_channel(self) -> None:
        """Confirm adding a custom channel."""
        if not hasattr(self, '_editing_fixture') or self._editing_fixture is None:
            return
        
        offset = dpg.get_value("channel_offset")
        function = ChannelFunction(dpg.get_value("channel_function"))
        default_value = dpg.get_value("channel_default")
        
        # Remove existing channel with same offset
        self._editing_fixture.custom_channels = [
            c for c in self._editing_fixture.custom_channels if c.offset != offset
        ]
        
        self._editing_fixture.custom_channels.append(
            ChannelMapping(offset=offset, function=function, default_value=default_value)
        )
        
        self._refresh_custom_channel_list(self._editing_fixture)
        dpg.delete_item("add_channel_window")
    
    def _remove_custom_channel(self, index: int) -> None:
        """Remove a custom channel."""
        if hasattr(self, '_editing_fixture') and self._editing_fixture:
            if 0 <= index < len(self._editing_fixture.custom_channels):
                self._editing_fixture.custom_channels.pop(index)
                self._refresh_custom_channel_list(self._editing_fixture)
    
    def _save_fixture_edit(self) -> None:
        """Save fixture edits."""
        if not hasattr(self, '_editing_fixture') or self._editing_fixture is None:
            return
        
        fixture = self._editing_fixture
        
        # Update basic settings
        fixture.name = dpg.get_value("edit_fixture_name")
        fixture.start_channel = dpg.get_value("edit_fixture_start")
        fixture.position = dpg.get_value("edit_fixture_position")
        fixture.intensity_scale = dpg.get_value("edit_fixture_intensity")
        
        # Update profile
        profile_value = dpg.get_value("edit_fixture_profile")
        fixture.profile_name = "" if profile_value == "(Custom)" else profile_value
        
        # Update movement limits
        fixture.pan_min = dpg.get_value("edit_pan_min")
        fixture.pan_max = dpg.get_value("edit_pan_max")
        fixture.tilt_min = dpg.get_value("edit_tilt_min")
        fixture.tilt_max = dpg.get_value("edit_tilt_max")
        
        # Overrides and custom channels are already updated in-place
        
        # Refresh UI
        self._refresh_fixture_list()
        
        # Update effects engine if running
        if self.effects_engine:
            self.effects_engine.update_config(self.config)
        
        dpg.delete_item("edit_fixture_window")
        self._editing_fixture = None
    
    def _remove_fixture(self) -> None:
        """Remove selected fixture."""
        for i, fixture in enumerate(self.config.fixtures):
            if dpg.does_item_exist(f"fixture_sel_{i}"):
                if dpg.get_value(f"fixture_sel_{i}"):
                    self.config.fixtures.pop(i)
                    self._refresh_fixture_list()
                    break
    
    def _on_mode_changed(self, sender, app_data) -> None:
        """Handle visualization mode change."""
        self.config.effects.mode = VisualizationMode(app_data)
        if self.effects_engine:
            self.effects_engine.update_config(self.config)
    
    def _on_simulate_changed(self, sender, app_data) -> None:
        """Handle simulation checkbox change."""
        pass
    
    def _start_show(self) -> None:
        """Start the light show."""
        simulate_dmx = dpg.get_value("simulate_dmx") if dpg.does_item_exist("simulate_dmx") else True
        simulate_audio = dpg.get_value("simulate_audio") if dpg.does_item_exist("simulate_audio") else False
        
        # Create DMX controller
        self.dmx_controller, self.dmx_interface = create_dmx_controller(
            port=self.config.dmx.port,
            simulate=simulate_dmx,
            fps=self.config.dmx.fps
        )
        
        if not self.dmx_interface.open():
            dpg.set_value(self._status_text_id, "Status: DMX connection failed!")
            return
        
        if not self.dmx_controller.start():
            dpg.set_value(self._status_text_id, "Status: DMX start failed!")
            return
        
        # Create audio analyzer
        self.audio_analyzer = create_audio_analyzer(simulate=simulate_audio)
        
        if not self.audio_analyzer.start():
            dpg.set_value(self._status_text_id, "Status: Audio capture failed!")
            self.dmx_controller.stop()
            self.dmx_interface.close()
            return
        
        # Create effects engine
        self.effects_engine = EffectsEngine(
            self.dmx_controller,
            self.config
        )
        
        dpg.set_value(self._status_text_id, "Status: Running")
    
    def _stop_show(self) -> None:
        """Stop the light show."""
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
        """Trigger blackout."""
        if self.effects_engine:
            self.effects_engine.blackout()
    
    def _update_loop(self) -> None:
        """Background update loop."""
        while self._running:
            if self.effects_engine and self.audio_analyzer:
                # Get analysis data
                data = self.audio_analyzer.get_data()
                self._current_analysis = data
                
                # Process through effects engine
                self._fixture_states = self.effects_engine.process(data)
                
                # Update GUI
                try:
                    self._update_gui(data)
                except Exception:
                    pass
            
            time.sleep(0.033)  # ~30 FPS GUI updates
    
    def _update_gui(self, data: AnalysisData) -> None:
        """Update GUI elements with current data."""
        if not dpg.is_dearpygui_running():
            return
        
        # Update track info
        if data.track_name and data.track_name != "System Audio":
            if data.artist_name:
                track_text = f"{data.artist_name} - {data.track_name} ({data.features.tempo:.0f} BPM)"
            else:
                track_text = f"{data.track_name} ({data.features.tempo:.0f} BPM)"
        else:
            track_text = f"System Audio - {data.features.tempo:.0f} BPM"
        
        if dpg.does_item_exist("track_info"):
            dpg.set_value("track_info", track_text[:80])
        
        # Update audio feature bars
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
        
        # Update DMX channel display
        if self.dmx_controller:
            channels = self.dmx_controller.get_all_channels()
            for i in range(min(32, len(channels))):
                if dpg.does_item_exist(f"ch_{i+1}"):
                    dpg.set_value(f"ch_{i+1}", channels[i] / 255.0)
        
        # Update visualizer
        self._draw_visualizer()
    
    def _list_audio_devices(self) -> None:
        """List available audio input devices."""
        analyzer = AudioAnalyzer()
        devices = analyzer.list_devices()
        
        if not devices:
            print("No audio input devices found")
            return
        
        print("\nAvailable audio input devices:")
        for dev in devices:
            print(f"  [{dev['index']}] {dev['name']} ({dev['channels']}ch, {dev['sample_rate']}Hz)")
    
    def _draw_visualizer(self) -> None:
        """Draw the fixture visualizer."""
        if not self._visualizer_id or not dpg.does_item_exist(self._visualizer_id):
            return
        
        # Clear previous drawing
        dpg.delete_item(self._visualizer_id, children_only=True)
        
        # Background
        dpg.draw_rectangle((0, 0), (900, 300), fill=(20, 20, 30), parent=self._visualizer_id)
        
        if not self.config.fixtures:
            dpg.draw_text((350, 140), "No fixtures configured", size=16,
                         color=(100, 100, 100), parent=self._visualizer_id)
            return
        
        # Draw each fixture
        num_fixtures = len(self.config.fixtures)
        fixture_width = min(100, (900 - 50) // max(1, num_fixtures))
        spacing = (900 - num_fixtures * fixture_width) // (num_fixtures + 1)
        
        sorted_fixtures = sorted(self.config.fixtures, key=lambda f: f.position)
        
        for i, fixture in enumerate(sorted_fixtures):
            x = spacing + i * (fixture_width + spacing)
            y = 50
            
            # Get fixture state
            state = self._fixture_states.get(fixture.name, FixtureState())
            
            # Draw fixture body
            dpg.draw_rectangle((x, y), (x + fixture_width, y + 180),
                              fill=(40, 40, 50), rounding=5, parent=self._visualizer_id)
            
            # Draw light beam
            beam_color = (state.red, state.green, state.blue, 180)
            beam_center_x = x + fixture_width // 2
            
            # Calculate beam direction from pan/tilt
            pan_offset = (state.pan - 128) / 128 * 50
            tilt_factor = state.tilt / 255
            
            beam_end_x = beam_center_x + pan_offset
            beam_end_y = y + 180 + 100 * tilt_factor
            
            # Draw beam as triangle
            dpg.draw_triangle(
                (beam_center_x - 10, y + 40),
                (beam_center_x + 10, y + 40),
                (beam_end_x, beam_end_y),
                fill=beam_color,
                parent=self._visualizer_id
            )
            
            # Draw LED indicator
            led_color = (state.red, state.green, state.blue, 255)
            dpg.draw_circle((beam_center_x, y + 30), 15, fill=led_color,
                           parent=self._visualizer_id)
            
            # Draw fixture name
            dpg.draw_text((x + 5, y + 185), fixture.name[:12], size=12,
                         color=(200, 200, 200), parent=self._visualizer_id)
            
            # Draw DMX values
            dpg.draw_text((x + 5, y + 200), f"R:{state.red} G:{state.green} B:{state.blue}",
                         size=10, color=(150, 150, 150), parent=self._visualizer_id)
    
    def _new_config(self) -> None:
        """Create new configuration."""
        self.config = ShowConfig()
        self._refresh_fixture_list()
        if dpg.does_item_exist("show_name_input"):
            dpg.set_value("show_name_input", self.config.name)
    
    def _load_config_dialog(self) -> None:
        """Show load config dialog."""
        with dpg.file_dialog(directory_selector=False, show=True, 
                            callback=self._load_config_callback,
                            width=600, height=400):
            dpg.add_file_extension(".json", color=(0, 255, 0))
    
    def _load_config_callback(self, sender, app_data) -> None:
        """Load config from file."""
        if app_data and 'file_path_name' in app_data:
            try:
                self.config = ShowConfig.load(app_data['file_path_name'])
                self._refresh_fixture_list()
                if dpg.does_item_exist("show_name_input"):
                    dpg.set_value("show_name_input", self.config.name)
            except Exception as e:
                print(f"Failed to load config: {e}")
    
    def _save_config_dialog(self) -> None:
        """Show save config dialog."""
        with dpg.file_dialog(directory_selector=False, show=True,
                            callback=self._save_config_callback,
                            default_filename="show_config.json",
                            width=600, height=400):
            dpg.add_file_extension(".json", color=(0, 255, 0))
    
    def _save_config_callback(self, sender, app_data) -> None:
        """Save config to file."""
        if app_data and 'file_path_name' in app_data:
            try:
                self.config.save(app_data['file_path_name'])
            except Exception as e:
                print(f"Failed to save config: {e}")
    
    def _export_json(self) -> None:
        """Export config as JSON."""
        self._save_config_dialog()
    
    def _reset_layout(self) -> None:
        """Reset window layout."""
        pass


def run_gui():
    """Run the GUI application."""
    app = MusicAutoShowGUI()
    app.run()


if __name__ == "__main__":
    run_gui()
