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
    
    # Custom CSS for styling - Modern dark theme with vibrant accents
    ui.add_head_html('''
    <style>
        :root {
            --bg-primary: #0f1419;
            --bg-secondary: #1a1f2e;
            --bg-tertiary: #252d3d;
            --bg-card: #1e2536;
            --border-color: rgba(99, 179, 237, 0.15);
            --border-hover: rgba(99, 179, 237, 0.3);
            --text-primary: #f0f6fc;
            --text-secondary: #8b949e;
            --accent-blue: #58a6ff;
            --accent-cyan: #39d0d8;
            --accent-purple: #a78bfa;
            --accent-pink: #f472b6;
            --accent-green: #34d399;
            --accent-orange: #fb923c;
            --accent-red: #f87171;
        }
        
        /* Global body styling */
        body {
            background: var(--bg-primary) !important;
        }
        
        /* Control cards */
        .control-card {
            background: var(--bg-card) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 12px !important;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3) !important;
        }
        
        /* Status indicators */
        .status-running {
            color: var(--accent-green) !important;
            text-shadow: 0 0 10px rgba(52, 211, 153, 0.5);
        }
        .status-stopped {
            color: var(--accent-orange) !important;
        }
        .status-blackout {
            color: var(--accent-red) !important;
            text-shadow: 0 0 10px rgba(248, 113, 113, 0.5);
            animation: pulse-red 1s infinite;
        }
        
        @keyframes pulse-red {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
        }
        
        /* Expansion panels - modern styling */
        .nicegui-expansion {
            background: var(--bg-secondary) !important;
            border-radius: 10px !important;
            margin-bottom: 8px !important;
            border: 1px solid var(--border-color) !important;
            overflow: hidden;
            transition: all 0.2s ease;
        }
        .nicegui-expansion:hover {
            border-color: var(--border-hover) !important;
        }
        .nicegui-expansion .q-item {
            padding: 12px 16px !important;
            min-height: 48px !important;
        }
        .nicegui-expansion .q-item__label {
            font-weight: 600 !important;
            color: var(--text-primary) !important;
            font-size: 0.95rem !important;
        }
        .nicegui-expansion .q-expansion-item__content {
            background: var(--bg-tertiary) !important;
            border-top: 1px solid var(--border-color) !important;
            padding: 8px !important;
        }
        .nicegui-expansion .q-item__section--avatar {
            color: var(--accent-cyan) !important;
        }
        
        /* Main layout */
        .main-splitter {
            height: calc(100vh - 60px) !important;
        }
        .left-panel {
            background: linear-gradient(180deg, var(--bg-secondary) 0%, var(--bg-primary) 100%);
            border-right: 1px solid var(--border-color);
        }
        
        /* Album color boxes */
        .album-color-box {
            width: 32px;
            height: 32px;
            border-radius: 8px;
            border: 2px solid rgba(255, 255, 255, 0.15);
            transition: all 0.2s ease;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
        }
        .album-color-box:hover {
            transform: scale(1.15);
            border-color: rgba(255, 255, 255, 0.4);
        }
        
        /* Now playing card */
        .now-playing-card {
            background: linear-gradient(135deg, var(--bg-card) 0%, var(--bg-tertiary) 100%) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 12px !important;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3) !important;
        }
        
        /* Header buttons - vibrant colors */
        .header-btn-start {
            background: linear-gradient(135deg, #059669 0%, #10b981 100%) !important;
            border: none !important;
            font-weight: 600 !important;
            box-shadow: 0 2px 10px rgba(16, 185, 129, 0.3) !important;
        }
        .header-btn-start:hover {
            box-shadow: 0 4px 20px rgba(16, 185, 129, 0.5) !important;
        }
        .header-btn-stop {
            background: linear-gradient(135deg, #dc2626 0%, #ef4444 100%) !important;
            border: none !important;
            font-weight: 600 !important;
            box-shadow: 0 2px 10px rgba(239, 68, 68, 0.3) !important;
        }
        .header-btn-stop:hover {
            box-shadow: 0 4px 20px rgba(239, 68, 68, 0.5) !important;
        }
        .header-btn-blackout {
            background: linear-gradient(135deg, #d97706 0%, #f59e0b 100%) !important;
            border: none !important;
            font-weight: 600 !important;
            box-shadow: 0 2px 10px rgba(245, 158, 11, 0.3) !important;
        }
        .header-btn-blackout:hover {
            box-shadow: 0 4px 20px rgba(245, 158, 11, 0.5) !important;
        }
        
        /* Flat button improvements */
        .q-btn--flat {
            border: 1px solid var(--border-color) !important;
            border-radius: 6px !important;
            transition: all 0.2s ease !important;
        }
        .q-btn--flat:hover {
            border-color: var(--border-hover) !important;
            background: rgba(99, 179, 237, 0.1) !important;
        }
        
        /* Slider styling - modern look */
        .q-slider {
            padding: 8px 0 !important;
        }
        .q-slider__track-container {
            background: var(--bg-tertiary) !important;
            height: 6px !important;
            border-radius: 3px !important;
        }
        .q-slider__track {
            background: linear-gradient(90deg, var(--accent-cyan) 0%, var(--accent-blue) 100%) !important;
            border-radius: 3px !important;
        }
        .q-slider__thumb {
            background: var(--accent-cyan) !important;
            width: 18px !important;
            height: 18px !important;
            box-shadow: 0 2px 8px rgba(57, 208, 216, 0.4) !important;
        }
        .q-slider__thumb:after {
            display: none !important;
        }
        
        /* Input styling */
        .q-field--dark .q-field__control {
            background: var(--bg-tertiary) !important;
            border-radius: 8px !important;
        }
        .q-field--dark .q-field__control:before {
            border-color: var(--border-color) !important;
        }
        .q-field--dark .q-field__control:hover:before {
            border-color: var(--border-hover) !important;
        }
        .q-field--focused .q-field__control:after {
            border-color: var(--accent-cyan) !important;
        }
        
        /* Select dropdown */
        .q-field__native, .q-field__input {
            color: var(--text-primary) !important;
        }
        
        /* Stage view card */
        .stage-view-card {
            background: linear-gradient(180deg, var(--bg-primary) 0%, var(--bg-secondary) 100%) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 12px !important;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4) !important;
            overflow: hidden;
        }
        
        /* Progress bars - vibrant colors */
        .q-linear-progress {
            border-radius: 4px !important;
            overflow: hidden;
            height: 8px !important;
        }
        .q-linear-progress__track {
            background: var(--bg-tertiary) !important;
        }
        .q-linear-progress__model {
            --q-linear-progress-speed: 0.05s;
        }
        
        /* Fixture list items */
        .fixture-item {
            background: var(--bg-tertiary) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 10px !important;
            padding: 12px 16px !important;
            transition: all 0.2s ease !important;
            cursor: pointer;
        }
        .fixture-item:hover {
            border-color: var(--accent-cyan) !important;
            background: rgba(57, 208, 216, 0.08) !important;
            transform: translateX(4px);
        }
        
        /* Checkbox styling */
        .q-checkbox__inner {
            color: var(--accent-cyan) !important;
        }
        .q-checkbox__bg {
            border-color: var(--border-color) !important;
        }
        .q-checkbox__inner--truthy .q-checkbox__bg {
            background: var(--accent-cyan) !important;
            border-color: var(--accent-cyan) !important;
        }
        
        /* Table styling */
        .q-table {
            background: transparent !important;
        }
        .q-table th {
            background: var(--bg-tertiary) !important;
            color: var(--text-secondary) !important;
            font-weight: 600 !important;
            border-bottom: 1px solid var(--border-color) !important;
        }
        .q-table td {
            border-bottom: 1px solid var(--border-color) !important;
            color: var(--text-primary) !important;
        }
        .q-table tbody tr:hover {
            background: rgba(57, 208, 216, 0.05) !important;
        }
        
        /* Dialog styling */
        .q-dialog__inner > .q-card {
            background: var(--bg-secondary) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 16px !important;
            box-shadow: 0 8px 40px rgba(0, 0, 0, 0.5) !important;
        }
        
        /* Scrollbar styling */
        ::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        ::-webkit-scrollbar-track {
            background: var(--bg-primary);
        }
        ::-webkit-scrollbar-thumb {
            background: var(--bg-tertiary);
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: var(--accent-cyan);
        }
        
        /* Separator */
        .q-separator {
            background: var(--border-color) !important;
        }
        
        /* Notification styling */
        .q-notification {
            border-radius: 10px !important;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4) !important;
        }
        
        /* Header app bar */
        .q-header {
            background: linear-gradient(180deg, var(--bg-secondary) 0%, var(--bg-primary) 100%) !important;
            border-bottom: 1px solid var(--border-color) !important;
        }
        
        /* Scene canvas styling */
        canvas {
            border-radius: 8px !important;
        }
    </style>
    ''')
    
    # Header with controls
    with ui.header().classes('items-center justify-between px-6 py-3'):
        ui.label('Music Auto Show').classes('text-xl font-bold').style('color: #f0f6fc; letter-spacing: -0.5px;')
        
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
            
            # Control buttons - styled with custom classes
            start_btn = ui.button('Start', on_click=lambda: _start_show(update_status)).classes('header-btn-start')
            stop_btn = ui.button('Stop', on_click=lambda: _stop_show(update_status)).classes('header-btn-stop')
            blackout_btn = ui.button('Blackout', on_click=lambda: _toggle_blackout(update_status)).classes('header-btn-blackout')
            
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
