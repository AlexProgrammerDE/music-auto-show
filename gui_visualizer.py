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

from config import FixtureType

if TYPE_CHECKING:
    from config import ShowConfig, FixtureConfig, FixtureProfile
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
        self._profiles_cache: dict[str, 'FixtureProfile'] = {}
    
    def _get_fixture_type(self, fixture: 'FixtureConfig') -> FixtureType:
        """Get the fixture type for a fixture."""
        from config import get_preset, FIXTURE_PRESETS
        
        if fixture.profile_name:
            # Check cache first
            if fixture.profile_name in self._profiles_cache:
                return self._profiles_cache[fixture.profile_name].fixture_type
            
            # Look up profile
            profile = get_preset(fixture.profile_name)
            if profile:
                self._profiles_cache[fixture.profile_name] = profile
                return profile.fixture_type
        
        return FixtureType.OTHER
    
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
            fixture_type = self._get_fixture_type(fixture)
            
            if num_fixtures > 1:
                fixture_x = margin + i * spacing
            else:
                fixture_x = self.width / 2
            fixture_y = truss_y + truss_height + 5
            
            # Draw different effects based on fixture type
            if fixture_type == FixtureType.EFFECT:
                self._draw_effect_light_beams(fixture_x, fixture_y, state)
            else:
                self._draw_beam(fixture_x, fixture_y, state)
        
        # Draw fixtures on top of beams
        for i, fixture in enumerate(sorted_fixtures):
            state = fixture_states.get(fixture.name, FixtureState())
            fixture_type = self._get_fixture_type(fixture)
            
            if num_fixtures > 1:
                fixture_x = margin + i * spacing
            else:
                fixture_x = self.width / 2
            fixture_y = truss_y + truss_height + 5
            
            # Draw different fixture bodies based on type
            if fixture_type == FixtureType.EFFECT:
                self._draw_effect_light_fixture(fixture_x, fixture_y, fixture, state, truss_y, truss_height)
            else:
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
    
    def _draw_effect_light_beams(self, fixture_x: float, fixture_y: float, state: 'FixtureState') -> None:
        """Draw scattered beams from an effect light (Derby, Moonflower, etc.)."""
        import math
        import time
        
        # Effect lights create multiple scattered beams
        # The color_macro and effect values control the pattern
        
        # Determine color from color_macro value (Techno Derby mapping)
        color = self._color_macro_to_rgb(state.color_macro)
        r, g, b = color
        
        # Check if there's any output
        brightness = (r + g + b) / 3
        if brightness < 10:
            return
        
        # Calculate rotation based on effect value (Channel 3: rotation speed)
        # state.effect: 0=off, 1-127=manual, 128-255=auto speed
        rotation_offset = 0.0
        if state.effect >= 128:
            # Auto rotation - use time for animation
            rotation_speed = (state.effect - 128) / 127.0  # 0 to 1
            rotation_offset = (time.time() * rotation_speed * 2) % (2 * math.pi)
        elif state.effect > 0:
            # Manual rotation position
            rotation_offset = (state.effect / 127.0) * 2 * math.pi
        
        # Draw 4-6 scattered beams (Derby style)
        num_beams = 5
        beam_length = 180
        
        for i in range(num_beams):
            # Each beam at a different angle
            base_angle = (i / num_beams) * 2 * math.pi
            angle = base_angle + rotation_offset
            
            # Vary beam properties
            beam_spread = 25 + (i % 3) * 10
            length_var = beam_length + (i % 2) * 30
            
            # Calculate beam end position
            # Beams go downward and outward
            end_x = fixture_x + math.sin(angle) * 120
            end_y = fixture_y + 50 + length_var + abs(math.cos(angle)) * 50
            
            # Alpha based on strobe (if strobe is active, flash the beams)
            alpha = min(150, int(brightness * 0.6))
            if state.strobe > 5:
                # Strobe effect - blink based on time
                strobe_freq = state.strobe / 50.0
                if int(time.time() * strobe_freq * 10) % 2 == 0:
                    alpha = min(200, alpha + 50)
                else:
                    alpha = alpha // 2
            
            beam_color = (r, g, b, alpha)
            
            # Draw beam as narrow cone
            dpg.draw_quad(
                (fixture_x - 5, fixture_y + 50),  # Top left
                (fixture_x + 5, fixture_y + 50),  # Top right  
                (end_x + beam_spread / 2, end_y),  # Bottom right
                (end_x - beam_spread / 2, end_y),  # Bottom left
                fill=beam_color, parent=self.visualizer_id
            )
        
        # Draw floor spots where beams hit
        for i in range(num_beams):
            base_angle = (i / num_beams) * 2 * math.pi
            angle = base_angle + rotation_offset
            
            spot_x = fixture_x + math.sin(angle) * 100
            spot_y = self.height - 60 + abs(math.cos(angle)) * 30
            spot_size = 20 + (i % 3) * 8
            
            spot_alpha = min(80, int(brightness * 0.3))
            spot_color = (r, g, b, spot_alpha)
            
            dpg.draw_ellipse(
                (spot_x - spot_size, spot_y - spot_size / 3),
                (spot_x + spot_size, spot_y + spot_size / 3),
                fill=spot_color, parent=self.visualizer_id
            )
    
    def _draw_effect_light_fixture(self, fixture_x: float, fixture_y: float,
                                    fixture: 'FixtureConfig', state: 'FixtureState',
                                    truss_y: int, truss_height: int) -> None:
        """Draw an effect light fixture body (Derby, Moonflower style)."""
        # Draw fixture mount
        dpg.draw_rectangle(
            (fixture_x - 4, truss_y + truss_height - 2),
            (fixture_x + 4, fixture_y + 10),
            fill=(60, 60, 65), parent=self.visualizer_id
        )
        
        # Effect light body - wider and shorter than moving head
        body_color = (55, 55, 65)
        dpg.draw_rectangle(
            (fixture_x - 18, fixture_y + 8),
            (fixture_x + 18, fixture_y + 48),
            fill=body_color, color=(75, 75, 85), thickness=1, rounding=5, parent=self.visualizer_id
        )
        
        # Dome/lens area - characteristic of derby lights
        color = self._color_macro_to_rgb(state.color_macro)
        r, g, b = color
        brightness = (r + g + b) / 3
        
        # Glowing dome
        if brightness > 10:
            glow_color = (min(255, r + 30), min(255, g + 30), min(255, b + 30), 180)
            dpg.draw_ellipse(
                (fixture_x - 14, fixture_y + 18),
                (fixture_x + 14, fixture_y + 44),
                fill=glow_color, parent=self.visualizer_id
            )
        
        # Inner lens/mirror
        lens_color = (r, g, b, 255) if brightness > 0 else (25, 25, 30, 255)
        dpg.draw_ellipse(
            (fixture_x - 10, fixture_y + 22),
            (fixture_x + 10, fixture_y + 40),
            fill=lens_color, color=(90, 90, 100), thickness=1, parent=self.visualizer_id
        )
        
        # Small indicator dots (like the multiple lenses on a derby)
        dot_positions = [(-6, 26), (6, 26), (-6, 36), (6, 36), (0, 31)]
        for dx, dy in dot_positions:
            dot_brightness = brightness / 255.0
            dot_color = (
                int(r * dot_brightness * 0.8),
                int(g * dot_brightness * 0.8), 
                int(b * dot_brightness * 0.8),
                200
            )
            dpg.draw_circle(
                (fixture_x + dx, fixture_y + dy), 3,
                fill=dot_color, parent=self.visualizer_id
            )
        
        # Label
        label_y = fixture_y + 52
        name_short = fixture.name[:10] if len(fixture.name) > 10 else fixture.name
        text_offset = len(name_short) * 3
        dpg.draw_text(
            (fixture_x - text_offset, label_y), name_short,
            size=11, color=(160, 160, 180), parent=self.visualizer_id
        )
        
        # Type indicator
        dpg.draw_text(
            (fixture_x - 20, label_y + 14), f"Effect Ch{fixture.start_channel}",
            size=9, color=(120, 100, 140), parent=self.visualizer_id
        )
    
    def _color_macro_to_rgb(self, color_macro: int) -> tuple[int, int, int]:
        """Convert Techno Derby color macro value to RGB."""
        # Techno Derby color ranges
        if color_macro <= 5:
            return (0, 0, 0)  # No function
        elif color_macro <= 20:
            return (255, 0, 0)  # Red
        elif color_macro <= 35:
            return (0, 255, 0)  # Green
        elif color_macro <= 50:
            return (0, 0, 255)  # Blue
        elif color_macro <= 65:
            return (255, 255, 255)  # White
        elif color_macro <= 80:
            return (255, 255, 0)  # Red + Green (Yellow)
        elif color_macro <= 95:
            return (255, 0, 255)  # Red + Blue (Magenta)
        elif color_macro <= 110:
            return (255, 200, 200)  # Red + White
        elif color_macro <= 125:
            return (0, 255, 255)  # Green + Blue (Cyan)
        elif color_macro <= 140:
            return (200, 255, 200)  # Green + White
        elif color_macro <= 155:
            return (200, 200, 255)  # Blue + White
        elif color_macro <= 170:
            return (255, 255, 255)  # RGB (White)
        elif color_macro <= 185:
            return (255, 255, 200)  # RGW
        elif color_macro <= 200:
            return (200, 255, 255)  # GBW
        elif color_macro <= 215:
            return (255, 255, 255)  # RGBW
        elif color_macro <= 255:
            # Color change modes - cycle through colors based on time
            import time
            cycle = int(time.time() * 2) % 7
            colors = [
                (255, 0, 0), (0, 255, 0), (0, 0, 255),
                (255, 255, 0), (255, 0, 255), (0, 255, 255), (255, 255, 255)
            ]
            return colors[cycle]
        
        return (128, 128, 128)  # Default gray
    
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
