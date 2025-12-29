"""
Configuration panel component for DMX and Audio settings.
"""
from nicegui import ui
from typing import Optional

from config import AudioInputMode, AudioDeviceType
from web.state import app_state
from audio_devices import list_audio_devices, AudioDeviceInfo


# Order for displaying device types in dropdown
DEVICE_TYPE_ORDER = [
    AudioDeviceType.LOOPBACK,
    AudioDeviceType.MONITOR,
    AudioDeviceType.VIRTUAL,
    AudioDeviceType.MICROPHONE,
    AudioDeviceType.LINE_IN,
    AudioDeviceType.UNKNOWN,
]

# Prefixes for device types
DEVICE_TYPE_PREFIX = {
    AudioDeviceType.LOOPBACK: "[Loopback]",
    AudioDeviceType.MONITOR: "[Monitor]",
    AudioDeviceType.VIRTUAL: "[Virtual]",
    AudioDeviceType.MICROPHONE: "[Mic]",
    AudioDeviceType.LINE_IN: "[Line-In]",
    AudioDeviceType.UNKNOWN: "[Other]",
}


class ConfigPanel:
    """Configuration panel for DMX and Audio settings."""
    
    def __init__(self):
        self._devices: list[AudioDeviceInfo] = []
        self._device_select: Optional[ui.select] = None
        self._device_info_label: Optional[ui.label] = None
        # Map display name -> device name (for storage)
        self._display_to_name: dict[str, str] = {}
        self._create_ui()
    
    def _create_ui(self) -> None:
        """Create the configuration panel UI."""
        # DMX Settings
        with ui.expansion('DMX Settings', icon='settings_ethernet', value=True).classes('w-full'):
            with ui.column().classes('w-full gap-2 p-2'):
                # Port input
                port_input = ui.input(
                    'DMX Port',
                    value=app_state.config.dmx.port,
                    placeholder='Auto-detect if empty'
                ).classes('w-full')
                port_input.on('blur', lambda e: self._update_port(e.sender.value or ''))  # type: ignore
                
                # Simulate DMX checkbox
                ui.checkbox(
                    'Simulate DMX (no hardware)',
                    value=app_state.simulate_dmx,
                    on_change=lambda e: setattr(app_state, 'simulate_dmx', e.value)
                )
        
        # Audio Settings
        with ui.expansion('Audio Input', icon='mic', value=True).classes('w-full'):
            with ui.column().classes('w-full gap-2 p-2'):
                # Device selector with refresh button
                with ui.row().classes('w-full items-end gap-2'):
                    self._device_select = ui.select(
                        options=[],
                        value=None,
                        label='Audio Device',
                        on_change=self._on_device_change
                    ).classes('flex-grow')
                    
                    ui.button(icon='refresh', on_click=self._refresh_devices).props('flat dense').tooltip('Refresh device list')
                
                # Device info label
                self._device_info_label = ui.label('').classes('text-xs text-gray-400')
                
                ui.separator().classes('my-2')
                
                # Fallback mode selector
                mode_options = {
                    'Auto-detect': AudioInputMode.AUTO,
                    'Prefer Loopback': AudioInputMode.LOOPBACK,
                    'Prefer Microphone': AudioInputMode.MICROPHONE,
                }
                
                # Find current fallback mode name
                current_mode_name = 'Auto-detect'
                for name, mode in mode_options.items():
                    if mode == app_state.config.audio.fallback_mode:
                        current_mode_name = name
                        break
                
                ui.select(
                    list(mode_options.keys()),
                    value=current_mode_name,
                    label='Fallback Mode (if device not found)',
                    on_change=lambda e: self._update_fallback_mode(mode_options[e.value])
                ).classes('w-full')
                
                ui.label('Used when "Auto" is selected or saved device is unavailable').classes('text-xs text-gray-400')
                
                ui.separator().classes('my-2')
                
                # Simulate audio checkbox
                ui.checkbox(
                    'Simulate Audio (no capture)',
                    value=app_state.simulate_audio,
                    on_change=lambda e: setattr(app_state, 'simulate_audio', e.value)
                )
        
        # File operations
        with ui.expansion('Configuration File', icon='folder').classes('w-full'):
            with ui.column().classes('w-full gap-2 p-2'):
                # Show name
                name_input = ui.input(
                    'Show Name',
                    value=app_state.config.name
                ).classes('w-full')
                name_input.on('blur', lambda e: setattr(app_state.config, 'name', str(e.sender.value or '')))  # type: ignore
                
                with ui.row().classes('gap-2'):
                    ui.button('New', on_click=self._new_config, icon='add').props('flat')
                    ui.button('Load', on_click=self._load_config, icon='folder_open').props('flat')
                    ui.button('Save', on_click=self._save_config, icon='save').props('flat')
        
        # Initial device list refresh
        self._refresh_devices()
    
    def _refresh_devices(self) -> None:
        """Refresh the audio device list."""
        self._devices = list_audio_devices()
        
        # Group devices by type
        grouped: dict[AudioDeviceType, list[AudioDeviceInfo]] = {}
        for device in self._devices:
            if device.device_type not in grouped:
                grouped[device.device_type] = []
            grouped[device.device_type].append(device)
        
        # Build options list with type prefixes, ordered by type
        options: list[str] = ['Auto (use fallback mode)']
        self._display_to_name = {'Auto (use fallback mode)': ''}
        
        for device_type in DEVICE_TYPE_ORDER:
            if device_type not in grouped:
                continue
            
            devices_in_group = grouped[device_type]
            if not devices_in_group:
                continue
            
            prefix = DEVICE_TYPE_PREFIX.get(device_type, "")
            
            for device in devices_in_group:
                # Build display name with type prefix
                suffix = ""
                if device.is_default_loopback:
                    suffix = " *"
                elif device.is_default:
                    suffix = " *"
                
                display = f"{prefix} {device.name}{suffix}"
                options.append(display)
                self._display_to_name[display] = device.name
        
        # Update select options
        if self._device_select:
            self._device_select.options = options
            
            # Set current value
            current_device = app_state.config.audio.device_name
            current_display = 'Auto (use fallback mode)'
            
            if current_device:
                # Find display name for current device
                for display, name in self._display_to_name.items():
                    if name == current_device:
                        current_display = display
                        break
                else:
                    # Device not found - show warning in info label
                    if self._device_info_label:
                        self._device_info_label.text = f"Saved device not found: {current_device}"
            
            self._device_select.value = current_display
            self._device_select.update()
        
        # Update info label
        self._update_device_info(app_state.config.audio.device_name)
    
    def _on_device_change(self, e) -> None:
        """Handle device selection change."""
        selected_display = e.value
        if selected_display is None:
            return
        
        device_name = self._display_to_name.get(selected_display, '')
        app_state.config.audio.device_name = device_name
        self._update_device_info(device_name)
    
    def _update_device_info(self, device_name: str) -> None:
        """Update the device info label."""
        if not self._device_info_label:
            return
            
        if not device_name:
            self._device_info_label.text = 'Will auto-select based on fallback mode'
            return
        
        # Find the device info
        for device in self._devices:
            if device.name == device_name:
                type_name = device.device_type.value.replace('_', ' ').title()
                self._device_info_label.text = (
                    f"{type_name} | {device.channels}ch @ {device.sample_rate}Hz | {device.host_api}"
                )
                return
        
        self._device_info_label.text = f'Device not found: {device_name}'
    
    def _update_fallback_mode(self, mode: AudioInputMode) -> None:
        """Update fallback mode."""
        app_state.config.audio.fallback_mode = mode
    
    def _update_port(self, value: str) -> None:
        """Update DMX port."""
        app_state.config.dmx.port = str(value)
    
    def _new_config(self) -> None:
        """Create new configuration."""
        from config import ShowConfig
        app_state.config = ShowConfig()
        self._refresh_devices()  # Refresh device selection
        ui.notify('New configuration created', type='info')
    
    async def _load_config(self) -> None:
        """Load configuration from file."""
        from config import ShowConfig
        
        # Use browser file picker
        result = await ui.run_javascript('''
            return new Promise((resolve) => {
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = '.json';
                input.onchange = async (e) => {
                    const file = e.target.files[0];
                    if (file) {
                        const text = await file.text();
                        resolve(text);
                    } else {
                        resolve(null);
                    }
                };
                input.click();
            });
        ''')
        
        if result:
            try:
                import json
                data = json.loads(result)
                app_state.config = ShowConfig.model_validate(data)
                self._refresh_devices()  # Refresh device selection with loaded config
                ui.notify(f'Loaded: {app_state.config.name}', type='positive')
            except Exception as e:
                ui.notify(f'Load failed: {e}', type='negative')
    
    async def _save_config(self) -> None:
        """Save configuration to file."""
        import json
        
        data = app_state.config.model_dump_json(indent=2)
        filename = f"{app_state.config.name.replace(' ', '_')}.json"
        
        # Trigger download in browser
        await ui.run_javascript(f'''
            const blob = new Blob([{json.dumps(data)}], {{type: 'application/json'}});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = {json.dumps(filename)};
            a.click();
            URL.revokeObjectURL(url);
        ''')
        
        ui.notify(f'Saved: {filename}', type='positive')
