"""
Manual audio input recording component.
Records the selected input so users can hear what the analyzer captures.
"""
from nicegui import ui

from web.state import app_state


class AudioRecorder:
    """Manual diagnostic audio recorder."""

    def __init__(self):
        self._status_label = None
        self._source_label = None
        self._level_label = None
        self._player = None
        self._create_ui()

    def _create_ui(self) -> None:
        """Create the recorder UI."""
        with ui.column().classes('w-full gap-3'):
            with ui.row().classes('w-full items-center gap-2 flex-wrap'):
                ui.button('Record', icon='fiber_manual_record', on_click=self._record).props('flat dense')
                ui.button('Stop', icon='stop', on_click=self._stop).props('flat dense')
                ui.button('Clear', icon='delete', on_click=self._clear).props('flat dense')

            with ui.column().classes('w-full gap-1'):
                self._status_label = ui.label('Idle').classes('text-sm font-mono text-white')
                self._source_label = ui.label('Source: not running').classes('text-xs text-gray-400')
                self._level_label = ui.label('Peak 0% | RMS 0% | clipped 0').classes('text-xs text-gray-400')

            self._player = ui.html(self._empty_player_html(), sanitize=False).classes('w-full')

        ui.timer(0.25, self._update_status)
        self._update_status()

    def _record(self) -> None:
        ok, message = app_state.start_audio_recording()
        if ok:
            self._player.content = self._empty_player_html()
            ui.notify(message, type='positive')
        else:
            ui.notify(message, type='negative')
        self._update_status()

    def _stop(self) -> None:
        ok, message = app_state.stop_audio_recording()
        if ok:
            self._refresh_player()
            ui.notify(message, type='positive')
        else:
            ui.notify(message, type='warning')
        self._update_status()

    def _clear(self) -> None:
        app_state.clear_audio_recording()
        self._player.content = self._empty_player_html()
        self._update_status()

    def _update_status(self) -> None:
        state = app_state.recording_state

        if state.recording:
            self._status_label.text = f'Recording {state.duration:.1f}s / {state.max_duration:.0f}s'
        elif state.has_recording:
            self._status_label.text = f'Ready {state.duration:.1f}s'
        elif state.error:
            self._status_label.text = state.error
        else:
            self._status_label.text = 'Idle'

        source = state.source or app_state.audio_runtime_status.device_name or 'not running'
        self._source_label.text = f'Source: {source}'
        self._level_label.text = (
            f'Peak {state.peak:.0%} | RMS {state.rms:.0%} | clipped {state.clipped_samples}'
        )

    def _refresh_player(self) -> None:
        data_url = app_state.get_audio_recording_data_url()
        if not data_url:
            self._player.content = self._empty_player_html()
            return

        self._player.content = f'''
            <audio controls preload="metadata" style="width:100%;height:40px">
                <source src="{data_url}" type="audio/wav">
            </audio>
        '''

    def _empty_player_html(self) -> str:
        return '''
            <audio controls preload="metadata" style="width:100%;height:40px" disabled></audio>
        '''
