"""
GUI Dialogs for Music Auto Show.
Contains fixture add/edit dialogs and channel configuration.
"""
from typing import Optional, Callable, TYPE_CHECKING

try:
    import dearpygui.dearpygui as dpg
    DEARPYGUI_AVAILABLE = True
except ImportError:
    DEARPYGUI_AVAILABLE = False

from config import (
    FixtureConfig, ChannelConfig, ChannelType,
    get_available_presets, get_preset, get_channel_type_display_name
)

if TYPE_CHECKING:
    from config import ShowConfig, FixtureProfile


class FixtureDialogs:
    """Handles fixture add/edit dialogs and channel configuration."""
    
    def __init__(self, 
                 config: 'ShowConfig',
                 on_fixture_changed: Optional[Callable[[], None]] = None,
                 on_config_updated: Optional[Callable[[], None]] = None):
        """
        Initialize fixture dialogs.
        
        Args:
            config: The ShowConfig to modify
            on_fixture_changed: Callback when fixture list changes (for refreshing list)
            on_config_updated: Callback when config is updated (for effects engine)
        """
        self.config = config
        self._on_fixture_changed = on_fixture_changed
        self._on_config_updated = on_config_updated
        self._editing_fixture: Optional[FixtureConfig] = None
        self._adding_fixture: Optional[FixtureConfig] = None
    
    def _get_next_start_channel(self) -> int:
        """Calculate the next available start channel based on existing fixtures."""
        if not self.config.fixtures:
            return 1
        
        highest_channel = 0
        for fixture in self.config.fixtures:
            # Get the profile to determine channel count
            profile = self.config.get_profile(fixture.profile_name) if fixture.profile_name else None
            channels = fixture.get_channels(profile)
            
            if channels:
                # Find the highest channel offset
                max_offset = max(ch.offset for ch in channels)
                fixture_end = fixture.start_channel + max_offset - 1
            else:
                # No channels defined, assume at least 1 channel
                fixture_end = fixture.start_channel
            
            highest_channel = max(highest_channel, fixture_end)
        
        return highest_channel + 1
    
    def _get_next_position(self) -> int:
        """Calculate the next position based on existing fixtures."""
        if not self.config.fixtures:
            return 0
        return max(f.position for f in self.config.fixtures) + 1
    
    def show_add_fixture_dialog(self) -> None:
        """Show the add fixture dialog."""
        if not DEARPYGUI_AVAILABLE:
            return
            
        if dpg.does_item_exist("add_fixture_window"):
            dpg.delete_item("add_fixture_window")
        
        # Reset the temporary fixture for the add dialog
        self._adding_fixture = None
        
        # Calculate smart defaults
        next_start_channel = self._get_next_start_channel()
        next_position = self._get_next_position()
        
        with dpg.window(label="Add Fixture", modal=True, tag="add_fixture_window",
                       width=750, height=650, pos=(250, 30)):
            
            # Basic settings
            with dpg.collapsing_header(label="Basic Settings", default_open=True):
                dpg.add_input_text(label="Name", tag="new_fixture_name", default_value="New Fixture", width=250)
                dpg.add_input_int(label="Start Channel", tag="new_fixture_start", default_value=next_start_channel,
                                 min_value=1, max_value=512, width=100)
                dpg.add_input_int(label="Position", tag="new_fixture_position", 
                                 default_value=next_position, width=100)
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
        """Confirm adding a new fixture."""
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
        
        if self._on_fixture_changed:
            self._on_fixture_changed()
        
        if self._on_config_updated:
            self._on_config_updated()
        
        self._adding_fixture = None
        dpg.delete_item("add_fixture_window")
    
    def show_edit_fixture_dialog(self, fixture: FixtureConfig) -> None:
        """Show the edit fixture dialog."""
        if not DEARPYGUI_AVAILABLE or fixture is None:
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
                                      callback=lambda s, a, u: self._update_channel_name(u, a),
                                      user_data=i)
                    
                    # Channel type dropdown
                    type_names = [get_channel_type_display_name(ct) for ct in ChannelType]
                    current_type_name = get_channel_type_display_name(ch.channel_type)
                    dpg.add_combo(items=type_names, default_value=current_type_name, width=-1,
                                 tag=f"ch_type_{i}",
                                 callback=lambda s, a, u: self._update_channel_type(u, a),
                                 user_data=i)
                    
                    # Fixed value checkbox
                    is_fixed = ch.fixed_value is not None
                    dpg.add_checkbox(default_value=is_fixed, tag=f"ch_fixed_{i}",
                                    callback=lambda s, a, u: self._toggle_fixed_value(u, a),
                                    user_data=i)
                    
                    # Fixed value input
                    fixed_val = ch.fixed_value if ch.fixed_value is not None else ch.default_value
                    dpg.add_input_int(default_value=fixed_val, width=-1, min_value=0, max_value=255,
                                     tag=f"ch_fixed_val_{i}",
                                     callback=lambda s, a, u: self._update_fixed_value(u, a),
                                     user_data=i)
                    
                    # Enabled checkbox
                    dpg.add_checkbox(default_value=ch.enabled, tag=f"ch_enabled_{i}",
                                    callback=lambda s, a, u: self._update_channel_enabled(u, a),
                                    user_data=i)
        
        dpg.add_spacer(height=5, parent="channel_list_container")
        dpg.add_button(label="Add Channel", callback=self._add_channel_dialog, parent="channel_list_container")
    
    def _update_channel_name(self, idx: int, name: str) -> None:
        """Update channel name."""
        if self._editing_fixture and idx < len(self._editing_fixture.channels):
            self._editing_fixture.channels[idx].name = name
    
    def _update_channel_type(self, idx: int, type_display_name: str) -> None:
        """Update channel type."""
        if not self._editing_fixture or idx >= len(self._editing_fixture.channels):
            return
        
        # Find channel type by display name
        for ct in ChannelType:
            if get_channel_type_display_name(ct) == type_display_name:
                self._editing_fixture.channels[idx].channel_type = ct
                break
    
    def _toggle_fixed_value(self, idx: int, is_fixed: bool) -> None:
        """Toggle fixed value for a channel."""
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
        """Update fixed value for a channel."""
        if not self._editing_fixture or idx >= len(self._editing_fixture.channels):
            return
        
        ch = self._editing_fixture.channels[idx]
        fixed_tag = f"ch_fixed_{idx}"
        if dpg.does_item_exist(fixed_tag) and dpg.get_value(fixed_tag):
            ch.fixed_value = value
    
    def _update_channel_enabled(self, idx: int, enabled: bool) -> None:
        """Update channel enabled state."""
        if self._editing_fixture and idx < len(self._editing_fixture.channels):
            self._editing_fixture.channels[idx].enabled = enabled
    
    def _add_channel_dialog(self) -> None:
        """Show add channel dialog."""
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
        """Confirm adding a new channel."""
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
        """Save fixture edits."""
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
        
        if self._on_fixture_changed:
            self._on_fixture_changed()
        
        if self._on_config_updated:
            self._on_config_updated()
        
        dpg.delete_item("edit_fixture_window")
        self._editing_fixture = None
