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
    from web.components.dmx_universe import DMXUniverse
    
    # Dark theme
    ui.dark_mode().enable()
    
    # Custom CSS for styling
    ui.add_head_html('''
    <style>
        .control-card {
            background-color: rgba(22, 27, 34, 0.95) !important;
            border: 1px solid rgba(48, 54, 61, 0.8) !important;
        }
        .status-running {
            color: #4ade80 !important;
        }
        .status-stopped {
            color: #f97316 !important;
        }
        .status-blackout {
            color: #ef4444 !important;
        }
        .meter-bar {
            height: 20px !important;
        }
        /* Improved expansion panel styling */
        .nicegui-expansion {
            background-color: rgba(22, 27, 34, 0.9) !important;
            border-radius: 8px !important;
            margin-bottom: 4px !important;
            border: 1px solid rgba(48, 54, 61, 0.6) !important;
        }
        .nicegui-expansion .q-item {
            padding: 10px 16px !important;
            min-height: 44px !important;
        }
        .nicegui-expansion .q-item__label {
            font-weight: 500 !important;
            color: #e6edf3 !important;
        }
        .nicegui-expansion .q-expansion-item__content {
            background-color: rgba(13, 17, 23, 0.6) !important;
            border-top: 1px solid rgba(48, 54, 61, 0.4) !important;
        }
        .nicegui-expansion .q-item__section--avatar {
            color: #7ee787 !important;
        }
        .main-splitter {
            height: calc(100vh - 56px) !important;
        }
        .left-panel {
            background-color: rgba(13, 17, 23, 0.98);
            border-right: 1px solid rgba(48, 54, 61, 0.6);
        }
        /* Album color boxes */
        .album-color-box {
            width: 28px;
            height: 28px;
            border-radius: 6px;
            border: 2px solid rgba(255, 255, 255, 0.1);
            transition: transform 0.2s ease;
        }
        .album-color-box:hover {
            transform: scale(1.1);
        }
        /* Now playing card styling */
        .now-playing-card {
            background: linear-gradient(135deg, rgba(22, 27, 34, 0.95) 0%, rgba(30, 40, 50, 0.95) 100%) !important;
            border: 1px solid rgba(48, 54, 61, 0.8) !important;
        }
        /* Button improvements */
        .q-btn--flat {
            border: 1px solid rgba(48, 54, 61, 0.5) !important;
        }
        /* Slider styling */
        .q-slider__track-container {
            background: rgba(48, 54, 61, 0.6) !important;
        }
        /* Input styling */
        .q-field--dark .q-field__control {
            background: rgba(13, 17, 23, 0.8) !important;
        }
        /* Stage view card */
        .stage-view-card {
            background: linear-gradient(180deg, rgba(13, 17, 23, 0.98) 0%, rgba(22, 27, 34, 0.95) 100%) !important;
            border: 1px solid rgba(48, 54, 61, 0.8) !important;
        }
    </style>
    ''')
    
    # Header with controls
    with ui.header().classes('items-center justify-between px-4 py-2 bg-gray-900'):
        ui.label('Music Auto Show').classes('text-xl font-bold')
        
        with ui.row().classes('items-center gap-4'):
            # Status display
            status_label = ui.label().classes('text-lg font-semibold')
            
            def update_status():
                status_label.text = f"Status: {app_state.status_message}"
                if app_state.status_message == "Running":
                    status_label.classes(remove='status-stopped status-blackout', add='status-running')
                elif app_state.status_message == "BLACKOUT":
                    status_label.classes(remove='status-stopped status-running', add='status-blackout')
                else:
                    status_label.classes(remove='status-running status-blackout', add='status-stopped')
            
            update_status()
            
            # Control buttons
            start_btn = ui.button('Start', on_click=lambda: _start_show(update_status)).props('color=positive')
            stop_btn = ui.button('Stop', on_click=lambda: _stop_show(update_status)).props('color=negative')
            blackout_btn = ui.button('Blackout', on_click=lambda: _toggle_blackout(update_status)).props('color=warning')
            
            # Timer to update status
            ui.timer(0.5, update_status)
    
    # Main content - use row layout instead of splitter for better control
    with ui.row().classes('w-full main-splitter gap-0'):
        # Left panel - Configuration (fixed width)
        with ui.column().classes('w-80 left-panel overflow-auto p-2').style('min-width: 320px; max-width: 400px'):
            # Config panel
            ConfigPanel()
            
            ui.separator().classes('my-2')
            
            # Fixture list
            FixtureList()
            
            ui.separator().classes('my-2')
            
            # Effects panel
            EffectsPanel()
        
        # Right panel - Visualization (flex grow)
        with ui.column().classes('flex-grow overflow-auto p-2'):
            # Now playing info
            _create_now_playing()
            
            ui.separator().classes('my-2')
            
            # Stage view (3D)
            with ui.card().classes('w-full stage-view-card'):
                ui.label('Stage View').classes('text-lg font-semibold mb-2 text-white')
                StageView()
            
            ui.separator().classes('my-2')
            
            # Audio meters
            with ui.card().classes('w-full control-card'):
                ui.label('Audio Analysis').classes('text-lg font-semibold mb-2')
                AudioMeters()
            
            ui.separator().classes('my-2')
            
            # DMX Universe
            with ui.expansion('DMX Universe', icon='settings_input_hdmi').classes('w-full'):
                DMXUniverse()


def _create_now_playing() -> None:
    """Create the now playing display."""
    with ui.card().classes('w-full now-playing-card'):
        with ui.row().classes('items-center gap-4 w-full'):
            # Track info
            with ui.column().classes('flex-grow'):
                ui.label('Now Playing').classes('text-sm text-gray-400')
                track_label = ui.label('No track').classes('text-lg font-semibold text-white')
                tempo_label = ui.label('120 BPM').classes('text-sm text-cyan-400')
                
                def update_track():
                    state = app_state.audio_state
                    if state.artist_name:
                        track_label.text = f"{state.artist_name} - {state.track_name}"
                    else:
                        track_label.text = state.track_name or "No track"
                    tempo_label.text = f"{state.tempo:.0f} BPM"
                
                ui.timer(0.25, update_track)
            
            # Album colors - with default vibrant colors when no track is playing
            with ui.column().classes('items-end'):
                ui.label('Album Colors').classes('text-sm text-gray-400')
                with ui.row().classes('gap-1') as color_row:
                    color_boxes = []
                    # Default colors - vibrant palette for when no album colors available
                    default_colors = [
                        (99, 102, 241),   # Indigo
                        (168, 85, 247),   # Purple  
                        (236, 72, 153),   # Pink
                        (34, 211, 238),   # Cyan
                        (74, 222, 128),   # Green
                    ]
                    for i in range(5):
                        r, g, b = default_colors[i]
                        box = ui.html(
                            f'<div class="album-color-box" style="background:rgb({r},{g},{b});"></div>',
                            sanitize=False
                        )
                        color_boxes.append(box)
                    
                    def update_colors():
                        colors = app_state.audio_state.album_colors
                        for i, box in enumerate(color_boxes):
                            if colors and i < len(colors):
                                r, g, b = colors[i]
                                box.content = f'<div class="album-color-box" style="background:rgb({r},{g},{b});"></div>'
                            else:
                                # Use default colors when no album colors
                                r, g, b = default_colors[i]
                                box.content = f'<div class="album-color-box" style="background:rgb({r},{g},{b});"></div>'
                    
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
