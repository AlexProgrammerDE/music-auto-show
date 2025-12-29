"""
Configuration panel component for DMX and Audio settings.
"""
from nicegui import ui

from config import AudioInputMode
from web.state import app_state


class ConfigPanel:
    """Configuration panel for DMX and Audio settings."""
    
    def __init__(self):
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
                port_input.on('blur', lambda e: self._update_port(str(e.sender.value or '')))
                
                # Simulate DMX checkbox
                ui.checkbox(
                    'Simulate DMX (no hardware)',
                    value=app_state.simulate_dmx,
                    on_change=lambda e: setattr(app_state, 'simulate_dmx', e.value)
                )
        
        # Audio Settings
        with ui.expansion('Audio Input', icon='mic').classes('w-full'):
            with ui.column().classes('w-full gap-2 p-2'):
                # Audio input mode
                mode_options = {
                    'System Audio (Loopback)': AudioInputMode.LOOPBACK,
                    'Microphone': AudioInputMode.MICROPHONE,
                    'Auto-detect': AudioInputMode.AUTO,
                }
                
                # Find current mode name
                current_mode_name = 'Auto-detect'
                for name, mode in mode_options.items():
                    if mode == app_state.audio_input_mode:
                        current_mode_name = name
                        break
                
                ui.select(
                    list(mode_options.keys()),
                    value=current_mode_name,
                    label='Input Source',
                    on_change=lambda e: setattr(app_state, 'audio_input_mode', mode_options[e.value])
                ).classes('w-full')
                
                ui.label('Loopback captures system audio, Microphone captures live input').classes('text-xs text-gray-400')
                
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
                name_input.on('blur', lambda e: setattr(app_state.config, 'name', str(e.sender.value or '')))
                
                with ui.row().classes('gap-2'):
                    ui.button('New', on_click=self._new_config, icon='add').props('flat')
                    ui.button('Load', on_click=self._load_config, icon='folder_open').props('flat')
                    ui.button('Save', on_click=self._save_config, icon='save').props('flat')
    
    def _update_port(self, value: str) -> None:
        """Update DMX port."""
        app_state.config.dmx.port = value
    
    def _new_config(self) -> None:
        """Create new configuration."""
        from config import ShowConfig
        app_state.config = ShowConfig()
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
