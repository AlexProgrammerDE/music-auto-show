"""
DMX Universe display component showing channel values.
"""
from nicegui import ui

from web.state import app_state


class DMXUniverse:
    """DMX channel display component."""
    
    def __init__(self):
        self._channel_elements = []
        self._value_labels = []
        self._create_ui()
    
    def _create_ui(self) -> None:
        """Create the DMX universe display UI."""
        self._container = ui.column().classes('w-full gap-2 p-2')
        self._refresh_display()
        
        # Update timer
        ui.timer(0.1, self._update_values)  # 10 FPS for DMX display
    
    def _get_last_used_channel(self) -> int:
        """Calculate the last DMX channel used by any fixture."""
        if not app_state.config.fixtures:
            return 0
        
        last_channel = 0
        for fixture in app_state.config.fixtures:
            profile = app_state.config.get_profile(fixture.profile_name) if fixture.profile_name else None
            channels = fixture.get_channels(profile)
            
            if channels:
                max_offset = max(ch.offset for ch in channels)
                fixture_end = fixture.start_channel + max_offset - 1
            else:
                fixture_end = fixture.start_channel
            
            last_channel = max(last_channel, fixture_end)
        
        return last_channel
    
    def _get_channel_info(self) -> dict[int, tuple[str, str]]:
        """Build a map of which fixture uses which channel."""
        channel_info = {}
        for fixture in app_state.config.fixtures:
            profile = app_state.config.get_profile(fixture.profile_name) if fixture.profile_name else None
            channels = fixture.get_channels(profile)
            for ch in channels:
                dmx_ch = fixture.start_channel + ch.offset - 1
                channel_info[dmx_ch] = (fixture.name, ch.name)
        return channel_info
    
    def _refresh_display(self) -> None:
        """Refresh the channel display layout."""
        self._container.clear()
        self._channel_elements = []
        self._value_labels = []
        
        last_channel = self._get_last_used_channel()
        
        with self._container:
            if last_channel == 0:
                ui.label('Add fixtures to see DMX channels').classes('text-gray-400 italic')
                return
            
            channel_info = self._get_channel_info()
            
            # Create channel display in rows of 16
            channels_per_row = 16
            
            for row_start in range(1, last_channel + 1, channels_per_row):
                row_end = min(row_start + channels_per_row - 1, last_channel)
                
                with ui.row().classes('gap-1 flex-wrap'):
                    for ch_num in range(row_start, row_end + 1):
                        is_used = ch_num in channel_info
                        
                        with ui.column().classes('items-center gap-0'):
                            # Channel number
                            color_class = 'text-green-500' if is_used else 'text-gray-500'
                            ui.label(str(ch_num)).classes(f'text-xs {color_class}')
                            
                            # Progress bar using NiceGUI element
                            with ui.element('div').classes('relative w-5 h-10 bg-gray-700 rounded overflow-hidden'):
                                bar_color = 'bg-green-500' if is_used else 'bg-gray-500'
                                bar = ui.element('div').classes(f'absolute bottom-0 w-full {bar_color}')
                                bar.style('height: 0%; transition: height 0.1s')
                                bar._props['id'] = f'dmx-bar-{ch_num}'
                            self._channel_elements.append((ch_num, bar))
                            
                            # Value label
                            val_label = ui.label('0').classes('text-xs text-gray-500 font-mono')
                            self._value_labels.append((ch_num, val_label))
            
            # Legend
            ui.separator().classes('my-2')
            with ui.row().classes('gap-4 text-xs items-center'):
                ui.element('span').classes('w-2 h-2 rounded-full bg-green-500')
                ui.label('Used').classes('text-gray-500')
                ui.element('span').classes('w-2 h-2 rounded-full bg-gray-500')
                ui.label('Unused').classes('text-gray-500')
            ui.label(f'Channels 1-{last_channel} ({last_channel} total)').classes('text-xs text-gray-500')
    
    def _update_values(self) -> None:
        """Update channel values."""
        channels = app_state.dmx_channels
        
        # Check if we need to refresh layout (fixture count changed)
        current_last = self._get_last_used_channel()
        if not self._channel_elements and current_last > 0:
            self._refresh_display()
            return
        
        # Update bars via JavaScript for performance
        updates = []
        for ch_num, bar in self._channel_elements:
            if ch_num <= len(channels):
                value = channels[ch_num - 1]
                percent = (value / 255) * 100
                updates.append(f'''
                    var el = document.getElementById('dmx-bar-{ch_num}');
                    if (el) el.style.height = '{percent:.0f}%';
                ''')
        
        # Update value labels
        for ch_num, label in self._value_labels:
            if ch_num <= len(channels):
                value = channels[ch_num - 1]
                label.text = str(value)
        
        # Batch JavaScript updates
        if updates:
            ui.run_javascript(''.join(updates))
