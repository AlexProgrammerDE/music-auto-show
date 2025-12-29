"""
Fixture dialog components for adding and editing fixtures.
"""
from typing import Optional, Callable, Any

from nicegui import ui

from config import (
    FixtureConfig, ChannelConfig, ChannelType,
    get_available_presets, get_preset, get_channel_type_display_name
)
from web.state import app_state


class FixtureDialogs:
    """Dialogs for adding and editing fixtures."""
    
    def __init__(self, on_change: Optional[Callable[[], None]] = None):
        self._on_change = on_change
        self._editing_fixture: Optional[FixtureConfig] = None
    
    def show_add_dialog(self) -> None:
        """Show the add fixture dialog."""
        # Calculate defaults
        next_channel = self._get_next_start_channel()
        next_position = len(app_state.config.fixtures)
        
        with ui.dialog() as dialog, ui.card().classes('w-full max-w-2xl'):
            ui.label('Add Fixture').classes('text-xl font-bold mb-4')
            
            # Basic settings
            name_input = ui.input('Name', value='New Fixture').classes('w-full')
            
            with ui.row().classes('gap-4 w-full'):
                start_input = ui.number('Start Channel', value=next_channel, min=1, max=512).classes('w-32')
                position_input = ui.number('Position', value=next_position).classes('w-32')
            
            # Profile selection
            preset_names = ['(Custom)'] + get_available_presets()
            default_profile = preset_names[1] if len(preset_names) > 1 else '(Custom)'
            
            profile_select = ui.select(
                preset_names,
                value=default_profile,
                label='Profile'
            ).classes('w-full')
            
            # Channel preview
            preview_container = ui.column().classes('w-full max-h-64 overflow-auto')
            
            def update_preview():
                preview_container.clear()
                profile_name = '' if profile_select.value == '(Custom)' else str(profile_select.value or '')
                
                with preview_container:
                    if not profile_name:
                        ui.label('No profile selected. Channels can be configured after adding.').classes('text-gray-400 italic')
                        return
                    
                    profile = get_preset(profile_name)
                    if not profile:
                        ui.label(f'Profile not found: {profile_name}').classes('text-red-400')
                        return
                    
                    ui.label('Channel Preview').classes('font-semibold mb-2')
                    
                    # Use NiceGUI table
                    columns = [
                        {'name': 'dmx', 'label': 'DMX', 'field': 'dmx', 'align': 'left'},
                        {'name': 'name', 'label': 'Name', 'field': 'name', 'align': 'left'},
                        {'name': 'type', 'label': 'Type', 'field': 'type', 'align': 'left'},
                    ]
                    
                    start = int(start_input.value or 1)
                    rows = []
                    for ch in profile.channels:
                        dmx_ch = start + ch.offset - 1
                        type_name = get_channel_type_display_name(ch.channel_type)
                        rows.append({'dmx': dmx_ch, 'name': ch.name, 'type': type_name})
                    
                    ui.table(columns=columns, rows=rows, row_key='dmx').classes('w-full').props('dense')
            
            profile_select.on('update:model-value', lambda: update_preview())
            start_input.on('update:model-value', lambda: update_preview())
            update_preview()
            
            # Movement limits (collapsed by default)
            with ui.expansion('Movement Limits', icon='open_with').classes('w-full'):
                with ui.row().classes('gap-4'):
                    pan_min = ui.number('Pan Min', value=0, min=0, max=255).classes('w-24')
                    pan_max = ui.number('Pan Max', value=255, min=0, max=255).classes('w-24')
                with ui.row().classes('gap-4'):
                    tilt_min = ui.number('Tilt Min', value=0, min=0, max=255).classes('w-24')
                    tilt_max = ui.number('Tilt Max', value=255, min=0, max=255).classes('w-24')
            
            # Buttons
            with ui.row().classes('gap-2 mt-4 justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                
                def add_fixture():
                    profile_name_val = '' if profile_select.value == '(Custom)' else str(profile_select.value or '')
                    
                    fixture = FixtureConfig(
                        name=str(name_input.value or 'New Fixture'),
                        profile_name=profile_name_val,
                        start_channel=int(start_input.value or 1),
                        position=int(position_input.value or 0),
                        pan_min=int(pan_min.value or 0),
                        pan_max=int(pan_max.value or 255),
                        tilt_min=int(tilt_min.value or 0),
                        tilt_max=int(tilt_max.value or 255),
                    )
                    
                    # Copy channels from profile
                    if profile_name_val:
                        profile = get_preset(profile_name_val)
                        if profile:
                            fixture.copy_channels_from_profile(profile)
                    
                    app_state.config.fixtures.append(fixture)
                    app_state.update_effects_config()
                    
                    if self._on_change:
                        self._on_change()
                    
                    ui.notify(f'Added: {fixture.name}', type='positive')
                    dialog.close()
                
                ui.button('Add Fixture', on_click=add_fixture).props('color=primary')
        
        dialog.open()
    
    def show_edit_dialog(self, fixture: FixtureConfig) -> None:
        """Show the edit fixture dialog."""
        self._editing_fixture = fixture
        
        with ui.dialog() as dialog, ui.card().classes('w-full max-w-3xl max-h-screen'):
            ui.label(f'Edit Fixture: {fixture.name}').classes('text-xl font-bold mb-4')
            
            with ui.scroll_area().classes('w-full max-h-96'):
                # Basic settings
                name_input = ui.input('Name', value=fixture.name).classes('w-full')
                
                with ui.row().classes('gap-4 w-full'):
                    start_input = ui.number('Start Channel', value=fixture.start_channel, min=1, max=512).classes('w-32')
                    position_input = ui.number('Position', value=fixture.position).classes('w-32')
                    intensity_input = ui.slider(min=0.0, max=1.0, step=0.01, value=fixture.intensity_scale).classes('w-48')
                    ui.label('Intensity Scale').classes('text-sm text-gray-400')
                
                # Profile info
                ui.separator().classes('my-2')
                profile_text = fixture.profile_name if fixture.profile_name else '(Custom)'
                ui.label(f'Profile: {profile_text}').classes('text-gray-400')
                
                # Change profile
                preset_names = ['(Custom)'] + get_available_presets()
                profile_select = ui.select(
                    preset_names,
                    value=profile_text,
                    label='Change Profile'
                ).classes('w-full')
                ui.label('Changing profile will reset channel settings!').classes('text-xs text-orange-400')
                
                # Movement limits
                ui.separator().classes('my-2')
                ui.label('Movement Limits').classes('font-semibold')
                
                with ui.row().classes('gap-4'):
                    pan_min = ui.number('Pan Min', value=fixture.pan_min, min=0, max=255).classes('w-24')
                    pan_max = ui.number('Pan Max', value=fixture.pan_max, min=0, max=255).classes('w-24')
                    tilt_min = ui.number('Tilt Min', value=fixture.tilt_min, min=0, max=255).classes('w-24')
                    tilt_max = ui.number('Tilt Max', value=fixture.tilt_max, min=0, max=255).classes('w-24')
                
                # Channel configuration
                ui.separator().classes('my-2')
                ui.label('Channels').classes('font-semibold')
                ui.label('Configure each channel. Check "Fixed" to force a specific DMX value.').classes('text-xs text-gray-400')
                
                channel_container = ui.column().classes('w-full')
                
                def refresh_channels():
                    channel_container.clear()
                    
                    profile = app_state.config.get_profile(fixture.profile_name) if fixture.profile_name else None
                    channels = fixture.get_channels(profile)
                    
                    with channel_container:
                        if not channels:
                            ui.label('No channels defined').classes('text-gray-400 italic')
                            return
                        
                        # Use NiceGUI table for channel display
                        columns = [
                            {'name': 'dmx', 'label': 'DMX', 'field': 'dmx', 'align': 'left'},
                            {'name': 'name', 'label': 'Name', 'field': 'name', 'align': 'left'},
                            {'name': 'type', 'label': 'Type', 'field': 'type', 'align': 'left'},
                            {'name': 'fixed', 'label': 'Fixed', 'field': 'fixed', 'align': 'center'},
                            {'name': 'value', 'label': 'Value', 'field': 'value', 'align': 'left'},
                            {'name': 'enabled', 'label': 'On', 'field': 'enabled', 'align': 'center'},
                        ]
                        
                        rows = []
                        for ch in channels:
                            dmx_ch = ch.get_dmx_channel(fixture.start_channel)
                            type_name = get_channel_type_display_name(ch.channel_type)
                            rows.append({
                                'dmx': dmx_ch,
                                'name': ch.name,
                                'type': type_name,
                                'fixed': 'Yes' if ch.fixed_value is not None else 'No',
                                'value': ch.fixed_value if ch.fixed_value is not None else ch.default_value,
                                'enabled': 'Yes' if ch.enabled else 'No',
                            })
                        
                        ui.table(columns=columns, rows=rows, row_key='dmx').classes('w-full').props('dense')
                
                refresh_channels()
                
                # Handle profile change
                def on_profile_change():
                    new_profile_name = '' if profile_select.value == '(Custom)' else str(profile_select.value or '')
                    
                    if new_profile_name:
                        profile = get_preset(new_profile_name)
                        if profile:
                            fixture.profile_name = new_profile_name
                            fixture.copy_channels_from_profile(profile)
                    else:
                        fixture.profile_name = ''
                        fixture.channels = []
                    
                    refresh_channels()
                
                profile_select.on('update:model-value', on_profile_change)
            
            # Buttons
            with ui.row().classes('gap-2 mt-4 justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                
                def save_fixture():
                    fixture.name = str(name_input.value or fixture.name)
                    fixture.start_channel = int(start_input.value or 1)
                    fixture.position = int(position_input.value or 0)
                    fixture.intensity_scale = float(intensity_input.value or 1.0)
                    fixture.pan_min = int(pan_min.value or 0)
                    fixture.pan_max = int(pan_max.value or 255)
                    fixture.tilt_min = int(tilt_min.value or 0)
                    fixture.tilt_max = int(tilt_max.value or 255)
                    
                    new_profile = '' if profile_select.value == '(Custom)' else str(profile_select.value or '')
                    if new_profile != fixture.profile_name:
                        fixture.profile_name = new_profile
                    
                    app_state.update_effects_config()
                    
                    if self._on_change:
                        self._on_change()
                    
                    ui.notify(f'Saved: {fixture.name}', type='positive')
                    dialog.close()
                
                ui.button('Save', on_click=save_fixture).props('color=primary')
        
        dialog.open()
    
    def _get_next_start_channel(self) -> int:
        """Calculate the next available start channel."""
        if not app_state.config.fixtures:
            return 1
        
        highest = 0
        for fixture in app_state.config.fixtures:
            profile = app_state.config.get_profile(fixture.profile_name) if fixture.profile_name else None
            channels = fixture.get_channels(profile)
            
            if channels:
                max_offset = max(ch.offset for ch in channels)
                fixture_end = fixture.start_channel + max_offset - 1
            else:
                fixture_end = fixture.start_channel
            
            highest = max(highest, fixture_end)
        
        return highest + 1
    
    def _update_channel_name(self, idx: int, name: str) -> None:
        """Update channel name."""
        if self._editing_fixture and idx < len(self._editing_fixture.channels):
            self._editing_fixture.channels[idx].name = name
    
    def _toggle_fixed(self, idx: int, is_fixed: bool) -> None:
        """Toggle fixed value for a channel."""
        if not self._editing_fixture or idx >= len(self._editing_fixture.channels):
            return
        
        ch = self._editing_fixture.channels[idx]
        if is_fixed:
            ch.fixed_value = ch.default_value
        else:
            ch.fixed_value = None
    
    def _update_fixed_value(self, idx: int, value: int) -> None:
        """Update fixed value for a channel."""
        if not self._editing_fixture or idx >= len(self._editing_fixture.channels):
            return
        
        ch = self._editing_fixture.channels[idx]
        if ch.fixed_value is not None:
            ch.fixed_value = value
    
    def _update_channel_enabled(self, idx: int, enabled: bool) -> None:
        """Update channel enabled state."""
        if self._editing_fixture and idx < len(self._editing_fixture.channels):
            self._editing_fixture.channels[idx].enabled = enabled
