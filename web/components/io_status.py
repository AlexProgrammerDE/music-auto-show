"""
Live input/output status component.
Shows the resolved audio input and DMX output actually used at runtime.
"""
from nicegui import ui

from web.state import app_state, AudioRuntimeStatus, DMXRuntimeStatus


class IOStatusPanel:
    """Display active audio and DMX devices."""

    def __init__(self):
        self._audio_title = None
        self._audio_detail = None
        self._audio_config = None
        self._dmx_title = None
        self._dmx_detail = None
        self._dmx_config = None
        self._create_ui()

    def _create_ui(self) -> None:
        """Create the status panel UI."""
        with ui.element('div').classes('w-full grid grid-cols-1 lg:grid-cols-2 gap-3'):
            with ui.column().classes('w-full gap-1 p-3 rounded border').style(
                'background:#171717;border-color:#303030;'
            ):
                ui.label('Audio input').classes('text-sm font-medium text-gray-300')
                self._audio_title = ui.label('Not running').classes('text-sm font-mono text-white')
                self._audio_detail = ui.label('No input stream is active').classes('text-xs text-gray-400')
                self._audio_config = ui.label('').classes('text-xs text-gray-500')

            with ui.column().classes('w-full gap-1 p-3 rounded border').style(
                'background:#171717;border-color:#303030;'
            ):
                ui.label('DMX output').classes('text-sm font-medium text-gray-300')
                self._dmx_title = ui.label('Not running').classes('text-sm font-mono text-white')
                self._dmx_detail = ui.label('No DMX interface is active').classes('text-xs text-gray-400')
                self._dmx_config = ui.label('').classes('text-xs text-gray-500')

        ui.timer(0.5, self._update)
        self._update()

    def _update(self) -> None:
        """Update status labels."""
        app_state.refresh_runtime_status()
        audio = app_state.audio_runtime_status
        dmx = app_state.dmx_runtime_status

        self._audio_title.text = self._audio_title_text(audio)
        self._audio_detail.text = self._audio_detail_text(audio)
        self._audio_config.text = self._audio_config_text(audio)

        self._dmx_title.text = self._dmx_title_text(dmx)
        self._dmx_detail.text = self._dmx_detail_text(dmx)
        self._dmx_config.text = self._dmx_config_text(dmx)

    def _audio_title_text(self, status: AudioRuntimeStatus) -> str:
        if status.simulated and status.running:
            return 'Simulated audio generator'
        if not status.running:
            return 'Not running'
        prefix = self._audio_selection_prefix(status.selection_reason)
        name = status.device_name or 'Unknown input'
        return f'{prefix}: {name}'

    def _audio_detail_text(self, status: AudioRuntimeStatus) -> str:
        if status.last_error:
            return status.last_error
        if not status.running:
            if status.configured_device_name:
                return f'Selected device: {status.configured_device_name}'
            return f'Auto mode: {status.configured_mode}'

        parts = []
        if status.device_type:
            parts.append(status.device_type.replace('_', ' '))
        if status.channels:
            parts.append(f'{status.channels}ch')
        if status.sample_rate:
            parts.append(f'{status.sample_rate}Hz')
        if status.host_api:
            parts.append(status.host_api)
        if status.device_index is not None:
            parts.append(f'index {status.device_index}')
        return ' | '.join(parts) if parts else 'Input stream active'

    def _audio_config_text(self, status: AudioRuntimeStatus) -> str:
        configured = status.configured_device_name or 'Auto'
        fallback = status.configured_mode or 'auto'
        if status.missing_device_name:
            return f'Configured: {configured} | missing, fallback: {fallback}'
        return f'Configured: {configured} | fallback: {fallback}'

    def _audio_selection_prefix(self, reason: str) -> str:
        labels = {
            'configured_device': 'Selected',
            'configured_index': 'Selected',
            'auto_loopback': 'Auto selected',
            'auto_microphone_fallback': 'Auto fell back',
            'saved_device_missing_fallback': 'Fallback selected',
            'preferred_loopback': 'Loopback selected',
            'preferred_microphone': 'Microphone selected',
            'simulated': 'Simulated',
        }
        return labels.get(reason, 'Active')

    def _dmx_title_text(self, status: DMXRuntimeStatus) -> str:
        if status.simulated and status.is_open:
            return 'Simulated DMX output'
        if not status.is_open:
            return 'Not running'
        if status.port:
            return f'Active on {status.port}'
        return 'DMX interface active'

    def _dmx_detail_text(self, status: DMXRuntimeStatus) -> str:
        if status.last_error:
            return status.last_error
        if not status.is_open:
            return f'Configured port: {status.configured_port or "Auto"}'

        parts = []
        if status.device_info:
            parts.append(status.device_info)
        if status.break_method:
            parts.append(f'break: {status.break_method}')
        parts.append(f'frames: {status.send_count}')
        if status.error_count:
            parts.append(f'errors: {status.error_count}')
        return ' | '.join(parts)

    def _dmx_config_text(self, status: DMXRuntimeStatus) -> str:
        configured = status.configured_port or 'Auto'
        mode = 'simulated' if status.simulated else (status.interface_type or 'serial')
        running = 'running' if status.running else 'stopped'
        return f'Configured: {configured} | {mode} | {running}'
