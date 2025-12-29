"""
3D Stage visualization component using NiceGUI's scene.
Displays fixtures on a virtual stage with beams and effects.
"""
import math
import time
from nicegui import ui

from config import FixtureType, get_preset
from effects_engine import FixtureState
from web.state import app_state


class StageView:
    """3D Stage visualization using NiceGUI scene."""
    
    def __init__(self):
        self._scene = None
        self._fixture_objects: dict[str, dict] = {}
        self._floor = None
        self._truss = None
        self._create_ui()
    
    def _create_ui(self) -> None:
        """Create the 3D scene UI."""
        with ui.scene(
            width=800,
            height=400,
            background_color='#0a0e14',
            grid=False
        ).classes('w-full rounded-lg') as self._scene:
            # Stage floor - dark with subtle gradient effect
            self._scene.box(12, 10, 0.1).move(0, 0, -0.05).material('#0d1117')
            
            # Floor grid lines - cyan accent for modern look
            for x in range(-6, 7):
                opacity = 0.15 if x % 2 == 0 else 0.08
                self._scene.line([x, -5, 0.02], [x, 5, 0.02]).material('#39d0d8', opacity=opacity)
            for y in range(-5, 6):
                opacity = 0.15 if y % 2 == 0 else 0.08
                self._scene.line([-6, y, 0.02], [6, y, 0.02]).material('#39d0d8', opacity=opacity)
            
            # Stage edge glow lines
            self._scene.line([-6, -5, 0.03], [6, -5, 0.03]).material('#39d0d8', opacity=0.4)
            self._scene.line([-6, 5, 0.03], [6, 5, 0.03]).material('#39d0d8', opacity=0.2)
            
            # Truss structure - modern metallic look
            # Main horizontal truss bar
            self._scene.box(8, 0.15, 0.15).move(0, 0, 3.5).material('#c0c8d0')
            
            # Secondary truss bars for depth
            self._scene.box(8, 0.08, 0.08).move(0, 0.15, 3.35).material('#8090a0')
            self._scene.box(8, 0.08, 0.08).move(0, -0.15, 3.35).material('#8090a0')
            
            # Truss vertical supports
            self._scene.box(0.12, 0.12, 3.5).move(-4, 0, 1.75).material('#9aa5b5')
            self._scene.box(0.12, 0.12, 3.5).move(4, 0, 1.75).material('#9aa5b5')
            
            # Cross braces for realism
            for z_pos in [0.8, 1.8, 2.8]:
                self._scene.box(0.04, 0.04, 0.4).move(-4, 0, z_pos).material('#606878')
                self._scene.box(0.04, 0.04, 0.4).move(4, 0, z_pos).material('#606878')
        
        # Set camera position - front-of-house elevated view, slightly angled
        self._scene.move_camera(x=0, y=-10, z=5, look_at_x=0, look_at_y=0, look_at_z=1.5, duration=0)
        
        # Update timer for fixture updates
        ui.timer(0.05, self._update_scene)  # 20 FPS
    
    def _update_scene(self) -> None:
        """Update the scene with current fixture states."""
        if not self._scene:
            return
        
        fixtures = app_state.config.fixtures
        
        if not fixtures:
            self._clear_fixtures()
            return
        
        # Calculate fixture positions
        num_fixtures = len(fixtures)
        sorted_fixtures = sorted(fixtures, key=lambda f: f.position)
        
        spacing = 6.0 / max(1, num_fixtures - 1) if num_fixtures > 1 else 0
        start_x = -3.0 if num_fixtures > 1 else 0
        
        # Track which fixtures we've updated
        updated = set()
        
        for i, fixture in enumerate(sorted_fixtures):
            x = start_x + i * spacing if num_fixtures > 1 else 0
            y = 0
            z = 3.2  # Just below truss
            
            state = app_state.get_fixture_state(fixture.name)
            fixture_type = self._get_fixture_type(fixture)
            
            # Create or update fixture
            if fixture.name not in self._fixture_objects:
                self._create_fixture_object(fixture.name, x, y, z, fixture_type)
            
            self._update_fixture_object(fixture.name, state, fixture_type, x, y, z)
            updated.add(fixture.name)
        
        # Remove fixtures no longer in config
        to_remove = set(self._fixture_objects.keys()) - updated
        for name in to_remove:
            self._remove_fixture_object(name)
    
    def _get_fixture_type(self, fixture) -> FixtureType:
        """Get fixture type from profile."""
        if fixture.profile_name:
            profile = get_preset(fixture.profile_name)
            if profile:
                return profile.fixture_type
        return FixtureType.OTHER
    
    def _create_fixture_object(self, name: str, x: float, y: float, z: float, 
                                fixture_type: FixtureType) -> None:
        """Create a fixture object in the scene."""
        if not self._scene:
            return
        
        with self._scene:
            objects = {}
            
            # Mount bracket - visible metallic clamp
            objects['mount'] = self._scene.box(0.08, 0.08, 0.25).move(x, y, z + 0.12).material('#8899bb')
            
            if fixture_type == FixtureType.EFFECT:
                # Effect light (derby/moonflower) - cube body with lens
                objects['body'] = self._scene.box(0.28, 0.28, 0.22).move(x, y, z - 0.08).material('#1e2432')
                # Lens ring
                objects['ring'] = self._scene.cylinder(0.14, 0.04).move(x, y, z - 0.22).material('#303848')
                objects['lens'] = self._scene.sphere(0.11).move(x, y, z - 0.24).material('#181c24')
                
                # Multiple beam indicators for effect lights - use boxes for beams pointing down
                objects['beams'] = []
                for angle in range(0, 360, 72):  # 5 beams
                    rad = math.radians(angle)
                    bx = x + 0.06 * math.cos(rad)
                    by = y + 0.06 * math.sin(rad)
                    # Use thin box for beam (extends downward in -z direction)
                    beam = self._scene.box(0.04, 0.04, 2.8).move(bx, by, z - 1.7).material('#333333', opacity=0.0)
                    objects['beams'].append(beam)
            else:
                # Standard fixture (moving head/par) - yoke and head design
                # Yoke arms
                objects['yoke_l'] = self._scene.box(0.03, 0.12, 0.18).move(x - 0.08, y, z - 0.06).material('#2a3040')
                objects['yoke_r'] = self._scene.box(0.03, 0.12, 0.18).move(x + 0.08, y, z - 0.06).material('#2a3040')
                # Head body
                objects['body'] = self._scene.box(0.14, 0.18, 0.14).move(x, y, z - 0.18).material('#1e2432')
                # Lens
                objects['head'] = self._scene.sphere(0.07).move(x, y, z - 0.28).material('#181c24')
                
                # Single beam - use box extending downward
                objects['beam'] = self._scene.box(0.12, 0.12, 3.0).move(x, y, z - 1.8).material('#333333', opacity=0.0)
                
                # Floor spot - flat cylinder on floor
                objects['spot'] = self._scene.cylinder(0.4, 0.02).move(x, y, 0.02).material('#333333', opacity=0.0)
            
            self._fixture_objects[name] = objects
    
    def _update_fixture_object(self, name: str, state: FixtureState, 
                                fixture_type: FixtureType, x: float, y: float, z: float) -> None:
        """Update a fixture object with current state."""
        if name not in self._fixture_objects:
            return
        
        objects = self._fixture_objects[name]
        
        # Calculate brightness
        brightness = (state.red + state.green + state.blue) / 3.0 / 255.0
        dimmer = state.dimmer / 255.0
        total_brightness = brightness * dimmer
        
        # Get color
        r, g, b = state.red, state.green, state.blue
        
        if fixture_type == FixtureType.EFFECT:
            # Effect light - use color_macro if RGB is zero
            if brightness < 0.1 and state.color_macro > 5:
                r, g, b = self._color_macro_to_rgb(state.color_macro)
                brightness = (r + g + b) / 3.0 / 255.0
                total_brightness = brightness * dimmer
            
            # Update lens color with glow effect
            if 'lens' in objects:
                if total_brightness > 0.05:
                    # Brighten the lens color
                    lr = min(255, int(r * 1.2 + 40))
                    lg = min(255, int(g * 1.2 + 40))
                    lb = min(255, int(b * 1.2 + 40))
                    color = f'#{lr:02x}{lg:02x}{lb:02x}'
                    objects['lens'].material(color)
                else:
                    objects['lens'].material('#1a1e26')
            
            # Update ring glow
            if 'ring' in objects:
                if total_brightness > 0.1:
                    objects['ring'].material(f'#{r:02x}{g:02x}{b:02x}', opacity=0.5)
                else:
                    objects['ring'].material('#252a36')
            
            # Update beams
            if 'beams' in objects:
                # Rotation based on effect value or time
                rotation_offset = 0.0
                if state.effect >= 128:
                    rotation_speed = (state.effect - 128) / 127.0
                    rotation_offset = (time.time() * rotation_speed * 2) % (2 * math.pi)
                elif state.effect > 0:
                    rotation_offset = (state.effect / 127.0) * 2 * math.pi
                
                for i, beam in enumerate(objects['beams']):
                    if total_brightness > 0.05:
                        angle = (i / 5) * 2 * math.pi + rotation_offset
                        bx = x + 0.25 * math.sin(angle)
                        by = y + 0.25 * math.cos(angle)
                        
                        beam.move(bx, by, z - 1.7)
                        
                        # Strobe effect
                        opacity = min(0.7, total_brightness * 0.9)
                        if state.strobe > 5:
                            strobe_freq = state.strobe / 50.0
                            if int(time.time() * strobe_freq * 10) % 2 == 0:
                                opacity = min(0.9, opacity + 0.3)
                            else:
                                opacity *= 0.2
                        
                        color = f'#{r:02x}{g:02x}{b:02x}'
                        beam.material(color, opacity=opacity)
                    else:
                        beam.material('#1a1e26', opacity=0.0)
        else:
            # Standard fixture
            # Update head/lens color with glow
            if 'head' in objects:
                if total_brightness > 0.05:
                    hr = min(255, int(r * 1.2 + 50))
                    hg = min(255, int(g * 1.2 + 50))
                    hb = min(255, int(b * 1.2 + 50))
                    color = f'#{hr:02x}{hg:02x}{hb:02x}'
                    objects['head'].material(color)
                else:
                    objects['head'].material('#1a1e26')
            
            # Update beam - straight down with pan/tilt offset
            if 'beam' in objects:
                if total_brightness > 0.05:
                    # Calculate beam offset from pan/tilt
                    pan_offset = ((state.pan - 128) / 128.0) * 1.5
                    tilt_offset = ((state.tilt - 128) / 128.0) * 0.8
                    
                    # Beam position (center of beam box)
                    bx = x + pan_offset * 0.5
                    by = y + tilt_offset * 0.3
                    bz = z - 1.8
                    
                    objects['beam'].move(bx, by, bz)
                    
                    opacity = min(0.6, total_brightness * 0.75)
                    color = f'#{r:02x}{g:02x}{b:02x}'
                    objects['beam'].material(color, opacity=opacity)
                else:
                    objects['beam'].material('#1a1e26', opacity=0.0)
            
            # Update floor spot
            if 'spot' in objects:
                if total_brightness > 0.1:
                    pan_offset = ((state.pan - 128) / 128.0) * 2.5
                    tilt_offset = ((state.tilt - 128) / 128.0) * 1.5
                    spot_x = x + pan_offset
                    spot_y = y + tilt_offset
                    
                    objects['spot'].move(spot_x, spot_y, 0.02)
                    
                    # Spot size varies with intensity
                    opacity = min(0.5, total_brightness * 0.6)
                    color = f'#{r:02x}{g:02x}{b:02x}'
                    objects['spot'].material(color, opacity=opacity)
                else:
                    objects['spot'].material('#1a1e26', opacity=0.0)
    
    def _remove_fixture_object(self, name: str) -> None:
        """Remove a fixture object from the scene."""
        if name in self._fixture_objects:
            # Objects will be garbage collected
            del self._fixture_objects[name]
    
    def _clear_fixtures(self) -> None:
        """Clear all fixture objects."""
        self._fixture_objects.clear()
    
    def _color_macro_to_rgb(self, color_macro: int) -> tuple[int, int, int]:
        """Convert color macro value to RGB."""
        if color_macro <= 5:
            return (0, 0, 0)
        elif color_macro <= 20:
            return (255, 0, 0)
        elif color_macro <= 35:
            return (0, 255, 0)
        elif color_macro <= 50:
            return (0, 0, 255)
        elif color_macro <= 65:
            return (255, 255, 255)
        elif color_macro <= 80:
            return (255, 255, 0)
        elif color_macro <= 95:
            return (255, 0, 255)
        elif color_macro <= 110:
            return (255, 200, 200)
        elif color_macro <= 125:
            return (0, 255, 255)
        elif color_macro <= 140:
            return (200, 255, 200)
        elif color_macro <= 155:
            return (200, 200, 255)
        elif color_macro <= 170:
            return (255, 255, 255)
        elif color_macro <= 185:
            return (255, 255, 200)
        elif color_macro <= 200:
            return (200, 255, 255)
        elif color_macro <= 215:
            return (255, 255, 255)
        elif color_macro <= 255:
            # Color change modes
            cycle = int(time.time() * 2) % 7
            colors = [
                (255, 0, 0), (0, 255, 0), (0, 0, 255),
                (255, 255, 0), (255, 0, 255), (0, 255, 255), (255, 255, 255)
            ]
            return colors[cycle]
        
        return (128, 128, 128)
