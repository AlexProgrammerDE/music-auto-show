"""
Audio meters component for displaying real-time audio analysis.
"""
from nicegui import ui

from web.state import app_state


class AudioMeters:
    """Audio analysis meters with real-time updates."""
    
    def __init__(self):
        self._meters = {}
        self._create_ui()
    
    def _create_ui(self) -> None:
        """Create the audio meters UI."""
        # Two-column layout for meters (stacks on mobile)
        with ui.row().classes('w-full gap-6 flex-col sm:flex-row'):
            # Left column - frequency bands
            with ui.column().classes('w-full sm:flex-1 gap-3'):
                self._create_meter('energy', 'Energy', 'blue')
                self._create_meter('bass', 'Bass', 'red')
                self._create_meter('mid', 'Mid', 'green')
                self._create_meter('high', 'High', 'purple')
            
            # Right column - rhythm info
            with ui.column().classes('w-full sm:flex-1 gap-3'):
                self._create_meter('tempo', 'Tempo', 'orange')
                self._create_meter('beat', 'Beat Position', 'pink')
                self._create_meter('danceability', 'Danceability', 'cyan')
                self._create_meter('valence', 'Valence', 'yellow')
        
        # Background task status
        with ui.expansion('Background Tasks', icon='schedule').classes('w-full mt-2'):
            with ui.column().classes('w-full gap-1 p-2'):
                self._madmom_status = ui.label('Madmom: Idle').classes('text-sm')
                self._madmom_progress = ui.linear_progress(value=0).classes('w-full').style('--q-linear-progress-speed: 10ms')
                self._buffer_label = ui.label('Buffer: 0.0s').classes('text-xs text-gray-400')
                self._next_run_label = ui.label('Next run: --').classes('text-xs text-gray-400')
                self._fps_label = ui.label('Effects FPS: 0').classes('text-xs text-gray-400')
        
        # Update timer
        ui.timer(0.05, self._update_meters)  # 20 FPS for UI
    
    def _create_meter(self, key: str, label: str, color: str) -> None:
        """Create a single meter."""
        with ui.column().classes('w-full gap-1'):
            with ui.row().classes('justify-between items-center w-full'):
                ui.label(label).classes('text-sm font-medium text-gray-500')
                value_label = ui.label('0').classes('text-sm font-mono font-semibold text-primary')
                self._meters[f'{key}_label'] = value_label
            
            progress = ui.linear_progress(value=0, show_value=False).classes('w-full').style('--q-linear-progress-speed: 10ms')
            self._meters[key] = progress
    
    def _update_meters(self) -> None:
        """Update all meters with current values."""
        state = app_state.audio_state
        task = app_state.task_status
        
        # Energy
        self._update_meter('energy', state.energy, f'{state.energy:.0%}')
        
        # Bass
        self._update_meter('bass', state.bass, f'{state.bass:.0%}')
        
        # Mid
        self._update_meter('mid', state.mid, f'{state.mid:.0%}')
        
        # High
        self._update_meter('high', state.high, f'{state.high:.0%}')
        
        # Tempo (normalized to 60-180 BPM range)
        tempo_norm = (state.tempo - 60) / 120  # 60-180 BPM range
        tempo_norm = max(0, min(1, tempo_norm))
        self._update_meter('tempo', tempo_norm, f'{state.tempo:.0f} BPM')
        
        # Beat position
        self._update_meter('beat', state.beat_position, f'{state.beat_position:.0%}')
        
        # Danceability
        self._update_meter('danceability', state.danceability, f'{state.danceability:.0%}')
        
        # Valence
        self._update_meter('valence', state.valence, f'{state.valence:.0%}')
        
        # Task status
        status_color = 'text-green-400' if task.madmom_available else 'text-gray-400'
        if task.madmom_processing:
            status_color = 'text-yellow-400'
        
        self._madmom_status.text = f'Madmom: {task.madmom_status}'
        self._madmom_status.classes(remove='text-green-400 text-yellow-400 text-gray-400', add=status_color)
        
        self._madmom_progress.value = task.progress
        self._buffer_label.text = f'Buffer: {task.buffer_duration:.1f}s'
        
        if task.madmom_processing:
            self._next_run_label.text = 'Next run: Processing...'
        elif task.time_until_next > 0:
            self._next_run_label.text = f'Next run: {task.time_until_next:.1f}s'
        else:
            self._next_run_label.text = 'Next run: Ready'
        
        self._fps_label.text = f'Effects FPS: {task.effects_fps:.0f}'
    
    def _update_meter(self, key: str, value: float, text: str) -> None:
        """Update a single meter."""
        if key in self._meters:
            self._meters[key].value = max(0, min(1, value))
        
        label_key = f'{key}_label'
        if label_key in self._meters:
            self._meters[label_key].text = text
