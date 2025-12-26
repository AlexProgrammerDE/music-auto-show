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
    VisualizationMode, DMXConfig, EffectsConfig, ChannelType,
    get_available_presets, get_preset, FIXTURE_PRESETS, get_channel_type_display_name
)
from dmx_controller import DMXController, create_dmx_controller, SimulatedDMXInterface
from audio_analyzer import AnalysisData, AudioAnalyzer, create_audio_analyzer
from effects_engine import EffectsEngine, FixtureState

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
        self._update_thread: Optional[threading.Thread] = None
        self._fixture_states: dict[str, FixtureState] = {}
        self._current_analysis: Optional[AnalysisData] = None
        
        self._fixture_list_id = None
        self._visualizer_id = None
        self._status_text_id = None
        self._track_info_id = None
        self._editing_fixture: Optional[FixtureConfig] = None
        self._adding_fixture: Optional[FixtureConfig] = None
    
    def run(self) -> None:
        if not DEARPYGUI_AVAILABLE:
            print("Dear PyGui not available. Install with: pip install dearpygui")
            return
        
        dpg.create_context()
        dpg.create_viewport(title="Music Auto Show", width=1400, height=900)
        
        self._setup_theme()
        self._create_main_window()
        
        dpg.setup_dearpygui()
        dpg.show_viewport()
        
        self._running = True
        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()
        
        dpg.start_dearpygui()
        
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
            dpg.add_text("Captures system audio (WASAPI loopback)")
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
            
            dpg.add_slider_float(label="Movement Speed", default_value=self.config.effects.movement_speed,
                                min_value=0.0, max_value=1.0, width=200,
                                callback=lambda s, a: setattr(self.config.effects, 'movement_speed', a))
    
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
        
        with dpg.collapsing_header(label="Fixture Visualizer", default_open=True):
            with dpg.drawlist(width=900, height=300, tag="visualizer"):
                self._visualizer_id = "visualizer"
                dpg.draw_rectangle((0, 0), (900, 300), fill=(30, 30, 40))
                dpg.draw_text((400, 140), "Start show to see visualization", size=16, color=(100, 100, 100))
        
        dpg.add_spacer(height=10)
        
        with dpg.collapsing_header(label="DMX Universe", default_open=False):
            with dpg.child_window(height=100, border=True, horizontal_scrollbar=True):
                with dpg.group(horizontal=True, tag="dmx_channels"):
                    for i in range(1, 33):
                        with dpg.group():
                            dpg.add_text(f"{i}", color=(150, 150, 150))
                            dpg.add_progress_bar(tag=f"ch_{i}", default_value=0.0, width=20)
    
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
    
    def _add_fixture_dialog(self) -> None:
        if dpg.does_item_exist("add_fixture_window"):
            dpg.delete_item("add_fixture_window")
        
        # Reset the temporary fixture for the add dialog
        self._adding_fixture = None
        
        with dpg.window(label="Add Fixture", modal=True, tag="add_fixture_window",
                       width=750, height=650, pos=(250, 30)):
            
            # Basic settings
            with dpg.collapsing_header(label="Basic Settings", default_open=True):
                dpg.add_input_text(label="Name", tag="new_fixture_name", default_value="New Fixture", width=250)
                dpg.add_input_int(label="Start Channel", tag="new_fixture_start", default_value=1,
                                 min_value=1, max_value=512, width=100)
                dpg.add_input_int(label="Position", tag="new_fixture_position", 
                                 default_value=len(self.config.fixtures), width=100)
                dpg.add_slider_float(label="Intensity Scale", tag="new_fixture_intensity",
                                    default_value=1.0, min_value=0.0, max_value=1.0, width=200)
            
            # Profile selection
            with dpg.collapsing_header(label="Profile", default_open=True):
                preset_names = ["(Custom)"] + get_available_presets()
                dpg.add_combo(label="Select Profile", items=preset_names, 
                             default_value=preset_names[1] if len(preset_names) > 1 else "(Custom)",
                             tag="new_fixture_profile", width=300,
                             callback=self._on_add_profile_changed)
                dpg.add_text("Select a profile to auto-configure channels for your fixture type.", 
                            color=(150, 150, 150))
            
            # Movement limits
            with dpg.collapsing_header(label="Movement Limits", default_open=False):
                with dpg.group(horizontal=True):
                    dpg.add_input_int(label="Pan Min", tag="new_pan_min", default_value=0, width=80)
                    dpg.add_input_int(label="Pan Max", tag="new_pan_max", default_value=255, width=80)
                with dpg.group(horizontal=True):
                    dpg.add_input_int(label="Tilt Min", tag="new_tilt_min", default_value=0, width=80)
                    dpg.add_input_int(label="Tilt Max", tag="new_tilt_max", default_value=255, width=80)
            
            # Channels preview
            with dpg.collapsing_header(label="Channels Preview", default_open=True):
                dpg.add_text("Channels will be configured based on the selected profile.", 
                            color=(150, 150, 150))
                dpg.add_text("You can customize channels after adding the fixture.", 
                            color=(150, 150, 150))
                dpg.add_separator()
                with dpg.child_window(height=200, border=True, tag="new_channel_preview"):
                    self._refresh_add_channel_preview()
            
            dpg.add_separator()
            with dpg.group(horizontal=True):
                dpg.add_button(label="Add Fixture", callback=self._add_fixture_confirm, width=120)
                dpg.add_button(label="Cancel", callback=lambda: dpg.delete_item("add_fixture_window"), width=100)
    
    def _on_add_profile_changed(self, sender, app_data) -> None:
        """Update channel preview when profile is changed in add dialog."""
        self._refresh_add_channel_preview()
    
    def _refresh_add_channel_preview(self) -> None:
        """Refresh the channel preview in the add fixture dialog."""
        if not dpg.does_item_exist("new_channel_preview"):
            return
        
        dpg.delete_item("new_channel_preview", children_only=True)
        
        profile_value = dpg.get_value("new_fixture_profile") if dpg.does_item_exist("new_fixture_profile") else "(Custom)"
        profile_name = "" if profile_value == "(Custom)" else profile_value
        
        if not profile_name:
            dpg.add_text("No profile selected. Add fixture first, then configure channels manually.",
                        parent="new_channel_preview", color=(200, 150, 150))
            return
        
        profile = get_preset(profile_name)
        if not profile:
            dpg.add_text(f"Profile '{profile_name}' not found.",
                        parent="new_channel_preview", color=(200, 150, 150))
            return
        
        start_channel = dpg.get_value("new_fixture_start") if dpg.does_item_exist("new_fixture_start") else 1
        
        # Header
        with dpg.group(horizontal=True, parent="new_channel_preview"):
            dpg.add_text("DMX Ch", color=(150, 150, 150))
            dpg.add_spacer(width=20)
            dpg.add_text("Name", color=(150, 150, 150))
            dpg.add_spacer(width=100)
            dpg.add_text("Type", color=(150, 150, 150))
            dpg.add_spacer(width=80)
            dpg.add_text("Default", color=(150, 150, 150))
        
        dpg.add_separator(parent="new_channel_preview")
        
        # Channel rows
        for ch in profile.channels:
            dmx_ch = start_channel + ch.offset - 1
            type_name = get_channel_type_display_name(ch.channel_type)
            
            with dpg.group(horizontal=True, parent="new_channel_preview"):
                dpg.add_text(f"{dmx_ch:3d}", color=(100, 150, 200))
                dpg.add_spacer(width=30)
                dpg.add_text(f"{ch.name[:15]:<15}", color=(200, 200, 200))
                dpg.add_spacer(width=20)
                dpg.add_text(f"{type_name[:12]:<12}", color=(150, 200, 150))
                dpg.add_spacer(width=20)
                dpg.add_text(f"{ch.default_value:3d}", color=(150, 150, 150))
    
    def _add_fixture_confirm(self) -> None:
        name = dpg.get_value("new_fixture_name")
        profile_value = dpg.get_value("new_fixture_profile")
        profile_name = "" if profile_value == "(Custom)" else profile_value
        
        fixture = FixtureConfig(
            name=name,
            profile_name=profile_name,
            start_channel=dpg.get_value("new_fixture_start"),
            position=dpg.get_value("new_fixture_position"),
            intensity_scale=dpg.get_value("new_fixture_intensity"),
            pan_min=dpg.get_value("new_pan_min"),
            pan_max=dpg.get_value("new_pan_max"),
            tilt_min=dpg.get_value("new_tilt_min"),
            tilt_max=dpg.get_value("new_tilt_max"),
        )
        
        # Copy channels from profile for customization
        if profile_name:
            profile = get_preset(profile_name)
            if profile:
                fixture.copy_channels_from_profile(profile)
        
        self.config.fixtures.append(fixture)
        self._refresh_fixture_list()
        
        if self.effects_engine:
            self.effects_engine.update_config(self.config)
        
        self._adding_fixture = None
        dpg.delete_item("add_fixture_window")
    
    def _on_fixture_selected(self, sender, app_data, user_data) -> None:
        if user_data is not None:
            self._edit_fixture(user_data)
    
    def _edit_fixture(self, fixture: FixtureConfig) -> None:
        if fixture is None:
            return
        
        self._editing_fixture = fixture
        
        if dpg.does_item_exist("edit_fixture_window"):
            dpg.delete_item("edit_fixture_window")
        
        with dpg.window(label=f"Edit Fixture: {fixture.name}", modal=True, 
                       tag="edit_fixture_window", width=750, height=650, pos=(250, 30)):
            
            # Basic settings
            with dpg.collapsing_header(label="Basic Settings", default_open=True):
                dpg.add_input_text(label="Name", default_value=fixture.name, 
                                  tag="edit_fixture_name", width=250)
                dpg.add_input_int(label="Start Channel", default_value=fixture.start_channel,
                                 tag="edit_fixture_start", min_value=1, max_value=512, width=100)
                dpg.add_input_int(label="Position", default_value=fixture.position,
                                 tag="edit_fixture_position", width=100)
                dpg.add_slider_float(label="Intensity Scale", default_value=fixture.intensity_scale,
                                    tag="edit_fixture_intensity", min_value=0.0, max_value=1.0, width=200)
            
            # Profile info
            with dpg.collapsing_header(label="Profile", default_open=True):
                profile_text = fixture.profile_name if fixture.profile_name else "(Custom)"
                dpg.add_text(f"Profile: {profile_text}", color=(150, 200, 255))
                
                preset_names = ["(Custom)"] + get_available_presets()
                dpg.add_combo(label="Change Profile", items=preset_names, default_value=profile_text,
                             tag="edit_fixture_profile", width=300,
                             callback=self._on_edit_profile_changed)
                dpg.add_text("Changing profile will reset channel settings!", color=(255, 200, 100))
            
            # Movement limits
            with dpg.collapsing_header(label="Movement Limits", default_open=False):
                with dpg.group(horizontal=True):
                    dpg.add_input_int(label="Pan Min", default_value=fixture.pan_min, tag="edit_pan_min", width=80)
                    dpg.add_input_int(label="Pan Max", default_value=fixture.pan_max, tag="edit_pan_max", width=80)
                with dpg.group(horizontal=True):
                    dpg.add_input_int(label="Tilt Min", default_value=fixture.tilt_min, tag="edit_tilt_min", width=80)
                    dpg.add_input_int(label="Tilt Max", default_value=fixture.tilt_max, tag="edit_tilt_max", width=80)
            
            # Channels - show all from profile/fixture
            with dpg.collapsing_header(label="Channels", default_open=True):
                dpg.add_text("Configure each channel. Set 'Fixed Value' to force a specific DMX value.", color=(150, 150, 150))
                dpg.add_separator()
                
                with dpg.child_window(height=280, border=True, tag="channel_list_container"):
                    self._refresh_channel_list(fixture)
            
            dpg.add_separator()
            
            with dpg.group(horizontal=True):
                dpg.add_button(label="Save", callback=self._save_fixture_edit, width=100)
                dpg.add_button(label="Cancel", callback=lambda: dpg.delete_item("edit_fixture_window"), width=100)
    
    def _on_edit_profile_changed(self, sender, app_data) -> None:
        """Handle profile change - reload channels from new profile."""
        if not self._editing_fixture:
            return
        
        profile_name = "" if app_data == "(Custom)" else app_data
        
        if profile_name:
            profile = get_preset(profile_name)
            if profile:
                self._editing_fixture.profile_name = profile_name
                self._editing_fixture.copy_channels_from_profile(profile)
                self._refresh_channel_list(self._editing_fixture)
        else:
            self._editing_fixture.profile_name = ""
            self._editing_fixture.channels = []
            self._refresh_channel_list(self._editing_fixture)
    
    def _refresh_channel_list(self, fixture: FixtureConfig) -> None:
        """Refresh the channel list in the edit dialog."""
        if not dpg.does_item_exist("channel_list_container"):
            return
        
        dpg.delete_item("channel_list_container", children_only=True)
        
        # Get channels
        profile = self.config.get_profile(fixture.profile_name) if fixture.profile_name else None
        channels = fixture.get_channels(profile)
        
        if not channels:
            dpg.add_text("No channels defined. Add channels or select a profile.", 
                        parent="channel_list_container", color=(200, 150, 150))
            dpg.add_button(label="Add Channel", callback=self._add_channel_dialog,
                          parent="channel_list_container")
            return
        
        # Use a table for proper column alignment
        with dpg.table(header_row=True, borders_innerH=True, borders_outerH=True,
                       borders_innerV=True, borders_outerV=True, row_background=True,
                       parent="channel_list_container", resizable=True):
            
            dpg.add_table_column(label="DMX", width_fixed=True, init_width_or_weight=40)
            dpg.add_table_column(label="Name", width_fixed=True, init_width_or_weight=100)
            dpg.add_table_column(label="Type", width_fixed=True, init_width_or_weight=140)
            dpg.add_table_column(label="Fixed", width_fixed=True, init_width_or_weight=40)
            dpg.add_table_column(label="Value", width_fixed=True, init_width_or_weight=60)
            dpg.add_table_column(label="On", width_fixed=True, init_width_or_weight=40)
            
            # Channel rows
            for i, ch in enumerate(channels):
                dmx_ch = ch.get_dmx_channel(fixture.start_channel)
                
                with dpg.table_row():
                    # DMX Channel number
                    dpg.add_text(f"{dmx_ch}")
                    
                    # Name input
                    dpg.add_input_text(default_value=ch.name, width=-1, tag=f"ch_name_{i}",
                                      callback=lambda s, a, idx=i: self._update_channel_name(idx, a))
                    
                    # Channel type dropdown
                    type_names = [get_channel_type_display_name(ct) for ct in ChannelType]
                    current_type_name = get_channel_type_display_name(ch.channel_type)
                    dpg.add_combo(items=type_names, default_value=current_type_name, width=-1,
                                 tag=f"ch_type_{i}",
                                 callback=lambda s, a, idx=i: self._update_channel_type(idx, a))
                    
                    # Fixed value checkbox
                    is_fixed = ch.fixed_value is not None
                    dpg.add_checkbox(default_value=is_fixed, tag=f"ch_fixed_{i}",
                                    callback=lambda s, a, idx=i: self._toggle_fixed_value(idx, a))
                    
                    # Fixed value input
                    fixed_val = ch.fixed_value if ch.fixed_value is not None else ch.default_value
                    dpg.add_input_int(default_value=fixed_val, width=-1, min_value=0, max_value=255,
                                     tag=f"ch_fixed_val_{i}",
                                     callback=lambda s, a, idx=i: self._update_fixed_value(idx, a))
                    
                    # Enabled checkbox
                    dpg.add_checkbox(default_value=ch.enabled, tag=f"ch_enabled_{i}",
                                    callback=lambda s, a, idx=i: self._update_channel_enabled(idx, a))
        
        dpg.add_spacer(height=5, parent="channel_list_container")
        dpg.add_button(label="Add Channel", callback=self._add_channel_dialog, parent="channel_list_container")
    
    def _update_channel_name(self, idx: int, name: str) -> None:
        if self._editing_fixture and idx < len(self._editing_fixture.channels):
            self._editing_fixture.channels[idx].name = name
    
    def _update_channel_type(self, idx: int, type_display_name: str) -> None:
        if not self._editing_fixture or idx >= len(self._editing_fixture.channels):
            return
        
        # Find channel type by display name
        for ct in ChannelType:
            if get_channel_type_display_name(ct) == type_display_name:
                self._editing_fixture.channels[idx].channel_type = ct
                break
    
    def _toggle_fixed_value(self, idx: int, is_fixed: bool) -> None:
        if not self._editing_fixture or idx >= len(self._editing_fixture.channels):
            return
        
        ch = self._editing_fixture.channels[idx]
        if is_fixed:
            # Set fixed value to current default or 0
            val_tag = f"ch_fixed_val_{idx}"
            if dpg.does_item_exist(val_tag):
                ch.fixed_value = dpg.get_value(val_tag)
            else:
                ch.fixed_value = ch.default_value
        else:
            ch.fixed_value = None
    
    def _update_fixed_value(self, idx: int, value: int) -> None:
        if not self._editing_fixture or idx >= len(self._editing_fixture.channels):
            return
        
        ch = self._editing_fixture.channels[idx]
        fixed_tag = f"ch_fixed_{idx}"
        if dpg.does_item_exist(fixed_tag) and dpg.get_value(fixed_tag):
            ch.fixed_value = value
    
    def _update_channel_enabled(self, idx: int, enabled: bool) -> None:
        if self._editing_fixture and idx < len(self._editing_fixture.channels):
            self._editing_fixture.channels[idx].enabled = enabled
    
    def _add_channel_dialog(self) -> None:
        if dpg.does_item_exist("add_channel_window"):
            dpg.delete_item("add_channel_window")
        
        # Determine next offset
        next_offset = 1
        if self._editing_fixture and self._editing_fixture.channels:
            next_offset = max(ch.offset for ch in self._editing_fixture.channels) + 1
        
        with dpg.window(label="Add Channel", modal=True, tag="add_channel_window",
                       width=400, height=250, pos=(450, 200)):
            dpg.add_input_int(label="Channel Offset", tag="new_ch_offset", default_value=next_offset,
                             min_value=1, max_value=512, width=100)
            dpg.add_input_text(label="Name", tag="new_ch_name", default_value="New Channel", width=200)
            
            type_names = [get_channel_type_display_name(ct) for ct in ChannelType]
            dpg.add_combo(label="Type", items=type_names, default_value=type_names[0],
                         tag="new_ch_type", width=200)
            
            dpg.add_input_int(label="Default Value", tag="new_ch_default", default_value=0,
                             min_value=0, max_value=255, width=100)
            
            dpg.add_separator()
            with dpg.group(horizontal=True):
                dpg.add_button(label="Add", callback=self._confirm_add_channel, width=80)
                dpg.add_button(label="Cancel", callback=lambda: dpg.delete_item("add_channel_window"), width=80)
    
    def _confirm_add_channel(self) -> None:
        if not self._editing_fixture:
            return
        
        offset = dpg.get_value("new_ch_offset")
        name = dpg.get_value("new_ch_name")
        type_name = dpg.get_value("new_ch_type")
        default_val = dpg.get_value("new_ch_default")
        
        # Find channel type
        channel_type = ChannelType.INTENSITY
        for ct in ChannelType:
            if get_channel_type_display_name(ct) == type_name:
                channel_type = ct
                break
        
        # Remove existing channel with same offset
        self._editing_fixture.channels = [c for c in self._editing_fixture.channels if c.offset != offset]
        
        # Add new channel
        self._editing_fixture.channels.append(ChannelConfig(
            offset=offset,
            name=name,
            channel_type=channel_type,
            default_value=default_val
        ))
        
        # Sort by offset
        self._editing_fixture.channels.sort(key=lambda c: c.offset)
        
        self._refresh_channel_list(self._editing_fixture)
        dpg.delete_item("add_channel_window")
    
    def _save_fixture_edit(self) -> None:
        if not self._editing_fixture:
            return
        
        fixture = self._editing_fixture
        
        fixture.name = dpg.get_value("edit_fixture_name")
        fixture.start_channel = dpg.get_value("edit_fixture_start")
        fixture.position = dpg.get_value("edit_fixture_position")
        fixture.intensity_scale = dpg.get_value("edit_fixture_intensity")
        fixture.pan_min = dpg.get_value("edit_pan_min")
        fixture.pan_max = dpg.get_value("edit_pan_max")
        fixture.tilt_min = dpg.get_value("edit_tilt_min")
        fixture.tilt_max = dpg.get_value("edit_tilt_max")
        
        profile_value = dpg.get_value("edit_fixture_profile")
        fixture.profile_name = "" if profile_value == "(Custom)" else profile_value
        
        # Capture all channel settings from GUI
        for i, ch in enumerate(fixture.channels):
            # Get name
            name_tag = f"ch_name_{i}"
            if dpg.does_item_exist(name_tag):
                ch.name = dpg.get_value(name_tag)
            
            # Get type
            type_tag = f"ch_type_{i}"
            if dpg.does_item_exist(type_tag):
                type_display_name = dpg.get_value(type_tag)
                for ct in ChannelType:
                    if get_channel_type_display_name(ct) == type_display_name:
                        ch.channel_type = ct
                        break
            
            # Get fixed checkbox and value
            fixed_tag = f"ch_fixed_{i}"
            fixed_val_tag = f"ch_fixed_val_{i}"
            if dpg.does_item_exist(fixed_tag):
                is_fixed = dpg.get_value(fixed_tag)
                if is_fixed and dpg.does_item_exist(fixed_val_tag):
                    ch.fixed_value = dpg.get_value(fixed_val_tag)
                else:
                    ch.fixed_value = None
            
            # Get enabled
            enabled_tag = f"ch_enabled_{i}"
            if dpg.does_item_exist(enabled_tag):
                ch.enabled = dpg.get_value(enabled_tag)
        
        self._refresh_fixture_list()
        
        if self.effects_engine:
            self.effects_engine.update_config(self.config)
        
        dpg.delete_item("edit_fixture_window")
        self._editing_fixture = None
    
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
    
    def _start_show(self) -> None:
        logger.info("=" * 50)
        logger.info("STARTING SHOW")
        logger.info("=" * 50)
        
        simulate_dmx = dpg.get_value("simulate_dmx") if dpg.does_item_exist("simulate_dmx") else True
        simulate_audio = dpg.get_value("simulate_audio") if dpg.does_item_exist("simulate_audio") else False
        
        logger.info(f"Simulate DMX: {simulate_dmx}")
        logger.info(f"Simulate Audio: {simulate_audio}")
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
        self.audio_analyzer = create_audio_analyzer(simulate=simulate_audio)
        
        logger.info("Starting audio capture...")
        if not self.audio_analyzer.start():
            logger.error("Audio capture failed!")
            dpg.set_value(self._status_text_id, "Status: Audio capture failed!")
            self.dmx_controller.stop()
            self.dmx_interface.close()
            return
        
        logger.info("Creating effects engine...")
        self.effects_engine = EffectsEngine(self.dmx_controller, self.config)
        
        logger.info("=" * 50)
        logger.info("SHOW RUNNING")
        logger.info("=" * 50)
        
        dpg.set_value(self._status_text_id, "Status: Running")
    
    def _stop_show(self) -> None:
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
            self.effects_engine.blackout()
    
    def _update_loop(self) -> None:
        frame_count = 0
        last_debug_time = time.time()
        
        while self._running:
            if self.effects_engine and self.audio_analyzer:
                data = self.audio_analyzer.get_data()
                self._current_analysis = data
                self._fixture_states = self.effects_engine.process(data)
                frame_count += 1
                
                # Debug logging every 5 seconds
                now = time.time()
                if now - last_debug_time >= 5.0:
                    # Log audio analysis
                    logger.info(f"Audio: energy={data.features.energy:.2f}, bass={data.features.bass:.2f}, "
                               f"tempo={data.features.tempo:.0f} BPM")
                    
                    # Log fixture states
                    for name, state in self._fixture_states.items():
                        logger.info(f"  Fixture '{name}': R={state.red} G={state.green} B={state.blue} "
                                   f"Dimmer={state.dimmer}")
                    
                    # Log actual DMX channel values
                    if self.dmx_controller:
                        channels = self.dmx_controller.get_all_channels()
                        non_zero = [(i+1, v) for i, v in enumerate(channels[:32]) if v > 0]
                        if non_zero:
                            logger.info(f"  DMX channels (1-32): {non_zero}")
                        else:
                            logger.info(f"  DMX channels 1-32: ALL ZERO")
                    
                    last_debug_time = now
                
                try:
                    self._update_gui(data)
                except Exception:
                    pass
            time.sleep(0.033)
    
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
        
        if self.dmx_controller:
            channels = self.dmx_controller.get_all_channels()
            for i in range(min(32, len(channels))):
                if dpg.does_item_exist(f"ch_{i+1}"):
                    dpg.set_value(f"ch_{i+1}", channels[i] / 255.0)
        
        self._draw_visualizer()
    
    def _draw_visualizer(self) -> None:
        if not self._visualizer_id or not dpg.does_item_exist(self._visualizer_id):
            return
        
        dpg.delete_item(self._visualizer_id, children_only=True)
        dpg.draw_rectangle((0, 0), (900, 300), fill=(20, 20, 30), parent=self._visualizer_id)
        
        if not self.config.fixtures:
            dpg.draw_text((350, 140), "No fixtures configured", size=16, color=(100, 100, 100), parent=self._visualizer_id)
            return
        
        num_fixtures = len(self.config.fixtures)
        fixture_width = min(100, (900 - 50) // max(1, num_fixtures))
        spacing = (900 - num_fixtures * fixture_width) // (num_fixtures + 1)
        
        sorted_fixtures = sorted(self.config.fixtures, key=lambda f: f.position)
        
        for i, fixture in enumerate(sorted_fixtures):
            x = spacing + i * (fixture_width + spacing)
            y = 50
            state = self._fixture_states.get(fixture.name, FixtureState())
            
            dpg.draw_rectangle((x, y), (x + fixture_width, y + 180), fill=(40, 40, 50), rounding=5, parent=self._visualizer_id)
            
            beam_color = (state.red, state.green, state.blue, 180)
            beam_center_x = x + fixture_width // 2
            pan_offset = (state.pan - 128) / 128 * 50
            tilt_factor = state.tilt / 255
            beam_end_x = beam_center_x + pan_offset
            beam_end_y = y + 180 + 100 * tilt_factor
            
            dpg.draw_triangle((beam_center_x - 10, y + 40), (beam_center_x + 10, y + 40), (beam_end_x, beam_end_y),
                             fill=beam_color, parent=self._visualizer_id)
            
            led_color = (state.red, state.green, state.blue, 255)
            dpg.draw_circle((beam_center_x, y + 30), 15, fill=led_color, parent=self._visualizer_id)
            
            dpg.draw_text((x + 5, y + 185), fixture.name[:12], size=12, color=(200, 200, 200), parent=self._visualizer_id)
            dpg.draw_text((x + 5, y + 200), f"R:{state.red} G:{state.green} B:{state.blue}",
                         size=10, color=(150, 150, 150), parent=self._visualizer_id)
    
    def _new_config(self) -> None:
        self.config = ShowConfig()
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
                self._refresh_fixture_list()
                if dpg.does_item_exist("show_name_input"):
                    dpg.set_value("show_name_input", self.config.name)
            except Exception as e:
                print(f"Failed to load config: {e}")
    
    def _save_config_dialog(self) -> None:
        with dpg.file_dialog(directory_selector=False, show=True, callback=self._save_config_callback,
                            default_filename="show_config.json", width=600, height=400):
            dpg.add_file_extension(".json", color=(0, 255, 0))
    
    def _save_config_callback(self, sender, app_data) -> None:
        if app_data and 'file_path_name' in app_data:
            try:
                self.config.save(app_data['file_path_name'])
            except Exception as e:
                print(f"Failed to save config: {e}")


def run_gui():
    app = MusicAutoShowGUI()
    app.run()


if __name__ == "__main__":
    run_gui()
