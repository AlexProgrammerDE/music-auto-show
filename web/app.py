"""
Main NiceGUI application for Music Auto Show.
Defines the layout and integrates all components.
"""
import logging
from pathlib import Path
from typing import Optional

from nicegui import ui, app

from config import ShowConfig
from web.state import app_state

logger = logging.getLogger(__name__)


def create_app(
    config_path: Optional[str] = None,
    simulate: bool = False,
    auto_start: bool = False
) -> None:
    """
    Initialize the NiceGUI application.
    
    Args:
        config_path: Path to configuration file to load
        simulate: Enable simulation mode (no hardware)
        auto_start: Automatically start the show on launch
    """
    # Load config if provided
    if config_path and Path(config_path).exists():
        try:
            app_state.config = ShowConfig.load(config_path)
            logger.info(f"Loaded config from {config_path}")
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
    
    # Set simulation mode
    if simulate:
        app_state.simulate_dmx = True
        app_state.simulate_audio = True
    
    # Store settings for later
    app.storage.general['auto_start'] = auto_start
    
    # Create the main page
    @ui.page('/')
    def main_page():
        _create_main_layout()
        
        # Auto-start if requested
        if app.storage.general.get('auto_start') and not app_state.running:
            app_state.start_show()


def _create_main_layout() -> None:
    """Create the main application layout."""
    # Import components here to avoid circular imports
    from web.components.config_panel import ConfigPanel
    from web.components.effects_panel import EffectsPanel
    from web.components.fixture_list import FixtureList
    from web.components.stage_view import StageView
    from web.components.audio_meters import AudioMeters
    from web.components.audio_visualizer import AudioVisualizer
    from web.components.dmx_universe import DMXUniverse
    
    # Dark theme
    ui.dark_mode().enable()
    
    # Header with controls - use dark color to match theme
    with ui.header().classes('items-center justify-between px-6 py-3').props('color=dark'):
        ui.label('Music Auto Show').classes('text-xl font-bold')
        
        with ui.row().classes('items-center gap-2'):
            # Status indicator dot and text
            status_dot = ui.element('div').classes('w-2 h-2 rounded-full')
            status_label = ui.label().classes('text-sm text-gray-300 mr-4')
            
            def update_status():
                status_label.text = app_state.status_message
                if app_state.status_message == "Running":
                    status_dot.classes(remove='bg-orange-400 bg-red-400', add='bg-green-400')
                elif app_state.status_message == "BLACKOUT":
                    status_dot.classes(remove='bg-orange-400 bg-green-400', add='bg-red-400')
                else:
                    status_dot.classes(remove='bg-green-400 bg-red-400', add='bg-orange-400')
            
            update_status()
            
            # Control buttons - white/light colors for dark header
            ui.button('Start', icon='play_arrow', on_click=lambda: _start_show(update_status)).props('flat dense').classes('text-white')
            ui.button('Stop', icon='stop', on_click=lambda: _stop_show(update_status)).props('flat dense').classes('text-white')
            ui.button('Blackout', icon='highlight_off', on_click=lambda: _toggle_blackout(update_status)).props('flat dense').classes('text-white')
            
            # Timer to update status
            ui.timer(0.5, update_status)
    
    # Main content - use splitter for resizable panels
    with ui.splitter(value=25).classes('w-full h-screen') as splitter:
        # Left panel - Configuration
        with splitter.before:
            with ui.scroll_area().classes('h-full'):
                with ui.column().classes('w-full gap-2 p-2'):
                    ConfigPanel()
                    FixtureList()
                    EffectsPanel()
        
        # Right panel - Visualization
        with splitter.after:
            with ui.scroll_area().classes('h-full'):
                with ui.column().classes('w-full gap-2 p-2'):
                    # Now playing info
                    _create_now_playing()
                    
                    # Stage view (3D)
                    with ui.card().classes('w-full').props('flat bordered'):
                        ui.label('Stage View').classes('text-lg font-semibold mb-2')
                        StageView()
                    
                    # Audio visualizer (spectrum, beats, etc)
                    with ui.card().classes('w-full').props('flat bordered'):
                        ui.label('Audio Visualization').classes('text-lg font-semibold mb-2')
                        AudioVisualizer()
                    
                    # Audio meters
                    with ui.card().classes('w-full').props('flat bordered'):
                        ui.label('Audio Analysis').classes('text-lg font-semibold mb-2')
                        AudioMeters()
                    
                    # DMX Universe
                    with ui.expansion('DMX Universe', icon='settings_input_hdmi').classes('w-full'):
                        DMXUniverse()


def _create_now_playing() -> None:
    """Create the now playing display."""
    with ui.card().classes('w-full').props('flat bordered'):
        with ui.row().classes('items-center gap-4 w-full'):
            # Track info
            with ui.column().classes('flex-grow'):
                ui.label('Now Playing').classes('text-sm text-gray-500')
                track_label = ui.label('No track').classes('text-lg font-semibold')
                tempo_label = ui.label('120 BPM').classes('text-sm text-primary')
                
                def update_track():
                    state = app_state.audio_state
                    if state.artist_name:
                        track_label.text = f"{state.artist_name} - {state.track_name}"
                    else:
                        track_label.text = state.track_name or "No track"
                    tempo_label.text = f"{state.tempo:.0f} BPM"
                
                ui.timer(0.25, update_track)
            
            # Album colors
            with ui.column().classes('items-end'):
                ui.label('Album Colors').classes('text-sm text-gray-500')
                with ui.row().classes('gap-1'):
                    color_boxes = []
                    # Default colors
                    default_colors = [
                        (99, 102, 241),   # Indigo
                        (168, 85, 247),   # Purple  
                        (236, 72, 153),   # Pink
                        (34, 211, 238),   # Cyan
                        (74, 222, 128),   # Green
                    ]
                    for i in range(5):
                        r, g, b = default_colors[i]
                        box = ui.element('div').classes('w-8 h-8 rounded')
                        box.style(f'background-color: rgb({r},{g},{b})')
                        color_boxes.append(box)
                    
                    def update_colors():
                        colors = app_state.audio_state.album_colors
                        for i, box in enumerate(color_boxes):
                            if colors and i < len(colors):
                                r, g, b = colors[i]
                            else:
                                r, g, b = default_colors[i]
                            box.style(f'background-color: rgb({r},{g},{b})')
                    
                    ui.timer(0.5, update_colors)


def _start_show(update_callback) -> None:
    """Start the show."""
    if app_state.start_show():
        ui.notify('Show started!', type='positive')
    else:
        ui.notify(f'Failed to start: {app_state.status_message}', type='negative')
    update_callback()


def _stop_show(update_callback) -> None:
    """Stop the show."""
    app_state.stop_show()
    ui.notify('Show stopped', type='info')
    update_callback()


def _toggle_blackout(update_callback) -> None:
    """Toggle blackout mode."""
    is_blackout = app_state.toggle_blackout()
    if is_blackout:
        ui.notify('BLACKOUT', type='warning')
    else:
        ui.notify('Blackout off', type='info')
    update_callback()
