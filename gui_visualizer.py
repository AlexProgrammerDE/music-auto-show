"""
GUI Stage Visualizer for Music Auto Show.
Contains the stage view drawing code for visualizing fixtures and beams.
"""
from typing import Optional, TYPE_CHECKING

try:
    import dearpygui.dearpygui as dpg
    DEARPYGUI_AVAILABLE = True
except ImportError:
    DEARPYGUI_AVAILABLE = False

if TYPE_CHECKING:
    from config import ShowConfig, FixtureConfig
    from effects_engine import FixtureState
    from audio_analyzer import AnalysisData


class StageVisualizer:
    """Handles the stage view drawing for visualizing fixtures and beams."""
    
    def __init__(self, 
                 visualizer_id: str = "visualizer",
                 width: int = 940, 
                 height: int = 400):
        """
        Initialize the stage visualizer.
        
        Args:
            visualizer_id: DearPyGui tag for the drawlist
            width: Width of the visualization area
            height: Height of the visualization area
        """
        self.visualizer_id = visualizer_id
        self.width = width
        self.height = height
    
    def draw(self, 
             fixtures: list['FixtureConfig'],
             fixture_states: dict[str, 'FixtureState'],
             current_analysis: Optional['AnalysisData'] = None) -> None:
        """
        Draw a realistic stage view with fixture beams and effects.
        
        Args:
            fixtures: List of fixture configurations
            fixture_states: Dictionary of fixture states keyed by fixture name
            current_analysis: Current audio analysis data (optional)
        """
        if not DEARPYGUI_AVAILABLE:
            return
            
        if not dpg.does_item_exist(self.visualizer_id):
            return
        
        dpg.delete_item(self.visualizer_id, children_only=True)
        
        # Stage background - dark with subtle gradient effect
        dpg.draw_rectangle((0, 0), (self.width, self.height), fill=(8, 8, 12), parent=self.visualizer_id)
        
        # Draw stage floor with perspective
        floor_top = 320
        floor_color = (25, 25, 35)
        dpg.draw_quad(
            (0, floor_top), (self.width, floor_top), (self.width, self.height), (0, self.height),
            fill=floor_color, parent=self.visualizer_id
        )
        
        # Draw floor grid lines for perspective
        for i in range(0, self.width + 1, 80):
            alpha = 30
            dpg.draw_line((i, floor_top), (i, self.height), color=(50, 50, 60, alpha), thickness=1, parent=self.visualizer_id)
        for i in range(floor_top, self.height + 1, 20):
            alpha = 20
            dpg.draw_line((0, i), (self.width, i), color=(50, 50, 60, alpha), thickness=1, parent=self.visualizer_id)
        
        # Draw truss at top
        truss_y = 25
        truss_height = 12
        dpg.draw_rectangle((20, truss_y), (self.width - 20, truss_y + truss_height), 
                          fill=(50, 50, 55), color=(70, 70, 80), thickness=2, parent=self.visualizer_id)
        # Truss detail lines
        for i in range(40, self.width - 20, 30):
            dpg.draw_line((i, truss_y), (i + 15, truss_y + truss_height), color=(40, 40, 45), thickness=1, parent=self.visualizer_id)
            dpg.draw_line((i + 15, truss_y), (i, truss_y + truss_height), color=(40, 40, 45), thickness=1, parent=self.visualizer_id)
        
        if not fixtures:
            dpg.draw_text((self.width // 2 - 100, self.height // 2 - 10), "No fixtures configured", 
                         size=18, color=(80, 80, 100), parent=self.visualizer_id)
            return
        
        num_fixtures = len(fixtures)
        sorted_fixtures = sorted(fixtures, key=lambda f: f.position)
        
        # Calculate fixture positions along the truss
        margin = 80
        available_width = self.width - 2 * margin
        if num_fixtures > 1:
            spacing = available_width / (num_fixtures - 1)
        else:
            spacing = 0
        
        # Import FixtureState locally to avoid circular import
        from effects_engine import FixtureState
        
        # Draw beams first (so they're behind fixtures)
        for i, fixture in enumerate(sorted_fixtures):
            state = fixture_states.get(fixture.name, FixtureState())
            
            if num_fixtures > 1:
                fixture_x = margin + i * spacing
            else:
                fixture_x = self.width / 2
            fixture_y = truss_y + truss_height + 5
            
            self._draw_beam(fixture_x, fixture_y, state)
        
        # Draw fixtures on top of beams
        for i, fixture in enumerate(sorted_fixtures):
            state = fixture_states.get(fixture.name, FixtureState())
            
            if num_fixtures > 1:
                fixture_x = margin + i * spacing
            else:
                fixture_x = self.width / 2
            fixture_y = truss_y + truss_height + 5
            
            self._draw_fixture(fixture_x, fixture_y, fixture, state, truss_y, truss_height)
        
        # Draw audio visualization bar at bottom
        if current_analysis:
            self._draw_audio_bar(current_analysis)
    
    def _draw_beam(self, fixture_x: float, fixture_y: float, state: 'FixtureState') -> None:
        """Draw a light beam from a fixture."""
        # Calculate beam direction from pan/tilt
        # Pan: 0-255, 128 = center, affects X direction
        # Tilt: 0-255, 0 = up, 255 = down
        pan_normalized = (state.pan - 128) / 128  # -1 to 1
        tilt_normalized = state.tilt / 255  # 0 to 1 (0=up, 1=down)
        
        # Beam end position
        beam_length = 250 + tilt_normalized * 80  # Longer beam when pointing down
        beam_spread = 60 + tilt_normalized * 40  # Wider spread when pointing down
        
        beam_end_x = fixture_x + pan_normalized * 200
        beam_end_y = fixture_y + 40 + beam_length * tilt_normalized
        
        # Only draw beam if there's some brightness
        brightness = (state.red + state.green + state.blue) / 3
        if brightness > 5:
            # Beam color with intensity-based alpha
            alpha = min(180, int(brightness * 0.7))
            
            # Draw multiple beam layers for glow effect
            for layer in range(3):
                layer_alpha = alpha // (layer + 1)
                layer_spread = beam_spread + layer * 15
                layer_color = (state.red, state.green, state.blue, layer_alpha)
                
                # Draw beam as a quad (trapezoid shape)
                dpg.draw_quad(
                    (fixture_x - 8 - layer * 3, fixture_y + 45),  # Top left
                    (fixture_x + 8 + layer * 3, fixture_y + 45),  # Top right
                    (beam_end_x + layer_spread / 2, beam_end_y),  # Bottom right
                    (beam_end_x - layer_spread / 2, beam_end_y),  # Bottom left
                    fill=layer_color, parent=self.visualizer_id
                )
            
            # Draw floor spot (where beam hits)
            if tilt_normalized > 0.3:
                spot_size = 30 + tilt_normalized * 40
                spot_alpha = min(100, int(brightness * 0.4))
                spot_color = (state.red, state.green, state.blue, spot_alpha)
                spot_y = min(beam_end_y, self.height - 20)
                dpg.draw_ellipse(
                    (beam_end_x - spot_size, spot_y - spot_size / 3),
                    (beam_end_x + spot_size, spot_y + spot_size / 3),
                    fill=spot_color, parent=self.visualizer_id
                )
    
    def _draw_fixture(self, fixture_x: float, fixture_y: float, 
                      fixture: 'FixtureConfig', state: 'FixtureState',
                      truss_y: int, truss_height: int) -> None:
        """Draw a fixture body on the truss."""
        # Draw fixture mount (connection to truss)
        dpg.draw_rectangle(
            (fixture_x - 4, truss_y + truss_height - 2), 
            (fixture_x + 4, fixture_y + 10),
            fill=(60, 60, 65), parent=self.visualizer_id
        )
        
        # Draw fixture body (yoke)
        yoke_color = (70, 70, 80)
        dpg.draw_rectangle(
            (fixture_x - 12, fixture_y + 8),
            (fixture_x + 12, fixture_y + 45),
            fill=yoke_color, color=(90, 90, 100), thickness=1, rounding=3, parent=self.visualizer_id
        )
        
        # Draw fixture head (rotated based on tilt)
        head_color = (50, 50, 60)
        tilt_offset = (state.tilt - 128) / 255 * 15  # Visual tilt indication
        dpg.draw_ellipse(
            (fixture_x - 10, fixture_y + 25 + tilt_offset - 8),
            (fixture_x + 10, fixture_y + 25 + tilt_offset + 8),
            fill=head_color, color=(80, 80, 90), thickness=1, parent=self.visualizer_id
        )
        
        # Draw lens (LED) - this shows the color
        brightness = (state.red + state.green + state.blue) / 3
        if brightness > 10:
            # Glowing lens
            glow_size = 8 + (brightness / 255) * 4
            glow_color = (
                min(255, state.red + 50),
                min(255, state.green + 50),
                min(255, state.blue + 50),
                200
            )
            dpg.draw_circle(
                (fixture_x, fixture_y + 25 + tilt_offset), glow_size,
                fill=glow_color, parent=self.visualizer_id
            )
        
        # Lens center
        lens_color = (state.red, state.green, state.blue, 255) if brightness > 0 else (30, 30, 35, 255)
        dpg.draw_circle(
            (fixture_x, fixture_y + 25 + tilt_offset), 6,
            fill=lens_color, color=(100, 100, 110), thickness=1, parent=self.visualizer_id
        )
        
        # Draw fixture label
        label_y = fixture_y + 52
        name_short = fixture.name[:10] if len(fixture.name) > 10 else fixture.name
        # Center the text approximately
        text_offset = len(name_short) * 3
        dpg.draw_text(
            (fixture_x - text_offset, label_y), name_short,
            size=11, color=(160, 160, 180), parent=self.visualizer_id
        )
        
        # Draw channel info below
        dpg.draw_text(
            (fixture_x - 15, label_y + 14), f"Ch {fixture.start_channel}",
            size=10, color=(100, 100, 120), parent=self.visualizer_id
        )
    
    def _draw_audio_bar(self, data: 'AnalysisData') -> None:
        """Draw the audio visualization bar at the bottom of the stage."""
        bar_y = self.height - 25
        bar_height = 15
        bar_width = self.width - 40
        
        # Background bar
        dpg.draw_rectangle(
            (20, bar_y), (20 + bar_width, bar_y + bar_height),
            fill=(30, 30, 40), color=(50, 50, 60), thickness=1, parent=self.visualizer_id
        )
        
        # Energy bar
        energy_width = int(data.features.energy * bar_width)
        if energy_width > 0:
            # Color based on frequency content
            r = int(150 + data.features.bass * 105)
            g = int(100 + data.features.mid * 100)
            b = int(100 + data.features.high * 155)
            dpg.draw_rectangle(
                (20, bar_y), (20 + energy_width, bar_y + bar_height),
                fill=(r, g, b, 180), parent=self.visualizer_id
            )
        
        # Beat indicator
        beat_x = 20 + int(data.beat_position * bar_width)
        dpg.draw_line(
            (beat_x, bar_y - 3), (beat_x, bar_y + bar_height + 3),
            color=(255, 255, 255, 150), thickness=2, parent=self.visualizer_id
        )
        
        # BPM text
        dpg.draw_text(
            (self.width - 80, bar_y + 1), f"{data.features.tempo:.0f} BPM",
            size=12, color=(180, 180, 200), parent=self.visualizer_id
        )
