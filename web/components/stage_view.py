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
    
    # Beam configuration
    BEAM_LENGTH = 2.8
    BEAM_RADIUS = 0.04  # Visible thin beam
    
    # Effect fixture beam configuration  
    EFFECT_BEAM_COUNT = 4
    EFFECT_BEAM_LENGTH = 2.5
    EFFECT_BEAM_RADIUS = 0.03
    EFFECT_BEAM_SPREAD = 25  # Degrees from vertical
    
    def __init__(self):
        self._scene = None
        self._fixture_objects: dict = {}
        self._create_ui()
    
    def _create_ui(self) -> None:
        """Create the 3D scene UI."""
        with ui.scene(
            width=800,
            height=400,
            background_color='#1a1a1a',
            grid=False
        ).classes('w-full rounded-lg') as self._scene:
            # Stage floor
            self._scene.box(12, 10, 0.1).move(0, 0, -0.05).material('#2a2a2a')
            
            # Floor grid lines
            for i in range(-6, 7):
                op = 0.12 if i % 2 == 0 else 0.06
                self._scene.line([i, -5, 0.01], [i, 5, 0.01]).material('#555555', opacity=op)
            for i in range(-5, 6):
                op = 0.12 if i % 2 == 0 else 0.06
                self._scene.line([-6, i, 0.01], [6, i, 0.01]).material('#555555', opacity=op)
            
            # Truss
            self._scene.box(8, 0.15, 0.15).move(0, 0, 3.5).material('#aaaaaa')
            self._scene.box(0.12, 0.12, 3.5).move(-4, 0, 1.75).material('#999999')
            self._scene.box(0.12, 0.12, 3.5).move(4, 0, 1.75).material('#999999')
        
        self._scene.move_camera(x=0, y=-8, z=4, look_at_x=0, look_at_y=0, look_at_z=1.5, duration=0)
        ui.timer(0.05, self._update_scene)
    
    def _update_scene(self) -> None:
        """Update the scene with current fixture states."""
        if not self._scene:
            return
        
        fixtures = app_state.config.fixtures
        if not fixtures:
            self._clear_fixtures()
            return
        
        num = len(fixtures)
        sorted_fixtures = sorted(fixtures, key=lambda f: f.position)
        spacing = 6.0 / max(1, num - 1) if num > 1 else 0
        start_x = -3.0 if num > 1 else 0
        
        updated = set()
        for i, fixture in enumerate(sorted_fixtures):
            x = start_x + i * spacing if num > 1 else 0
            y, z = 0, 3.2
            
            state = app_state.get_fixture_state(fixture.name)
            ftype = self._get_fixture_type(fixture)
            
            # Recreate if structure is wrong
            if fixture.name in self._fixture_objects:
                obj = self._fixture_objects[fixture.name]
                recreate = obj.get('ftype') != ftype
                if ftype == FixtureType.EFFECT:
                    recreate = recreate or 'beams' not in obj
                else:
                    recreate = recreate or 'beam' not in obj
                if recreate:
                    del self._fixture_objects[fixture.name]
            
            if fixture.name not in self._fixture_objects:
                self._create_fixture(fixture.name, ftype)
            
            self._update_fixture(fixture.name, state, ftype, x, y, z)
            updated.add(fixture.name)
        
        for name in set(self._fixture_objects.keys()) - updated:
            del self._fixture_objects[name]
    
    def _get_fixture_type(self, fixture) -> FixtureType:
        if fixture.profile_name:
            profile = get_preset(fixture.profile_name)
            if profile:
                return profile.fixture_type
        return FixtureType.OTHER
    
    def _create_fixture(self, name: str, ftype: FixtureType) -> None:
        """Create fixture 3D objects."""
        if not self._scene:
            return
        
        with self._scene:
            obj = {'ftype': ftype}
            
            # Mount
            obj['mount'] = self._scene.box(0.06, 0.06, 0.15).material('#555555')
            
            if ftype == FixtureType.EFFECT:
                obj['body'] = self._scene.box(0.24, 0.24, 0.18).material('#2a2a2a')
                obj['lens'] = self._scene.sphere(0.09).material('#111111')
                obj['beams'] = []
                for _ in range(self.EFFECT_BEAM_COUNT):
                    # Use box for beam - more reliable than cylinder
                    beam = self._scene.box(
                        self.EFFECT_BEAM_RADIUS * 2,
                        self.EFFECT_BEAM_RADIUS * 2, 
                        self.EFFECT_BEAM_LENGTH
                    ).material('#333333', opacity=0.0)
                    obj['beams'].append(beam)
            else:
                obj['yoke_l'] = self._scene.box(0.02, 0.08, 0.14).material('#3a3a3a')
                obj['yoke_r'] = self._scene.box(0.02, 0.08, 0.14).material('#3a3a3a')
                obj['head'] = self._scene.box(0.10, 0.14, 0.10).material('#2a2a2a')
                obj['lens'] = self._scene.sphere(0.05).material('#111111')
                # Use box for beam
                obj['beam'] = self._scene.box(
                    self.BEAM_RADIUS * 2,
                    self.BEAM_RADIUS * 2,
                    self.BEAM_LENGTH
                ).material('#333333', opacity=0.0)
            
            self._fixture_objects[name] = obj
    
    def _update_fixture(self, name: str, state: FixtureState, ftype: FixtureType,
                        x: float, y: float, z: float) -> None:
        """Update fixture position and state."""
        obj = self._fixture_objects.get(name)
        if not obj:
            return
        
        # Position body parts
        obj['mount'].move(x, y, z + 0.07)
        
        if ftype == FixtureType.EFFECT:
            obj['body'].move(x, y, z - 0.07)
            lens_z = z - 0.18
            obj['lens'].move(x, y, lens_z)
        else:
            obj['yoke_l'].move(x - 0.06, y, z - 0.05)
            obj['yoke_r'].move(x + 0.06, y, z - 0.05)
            obj['head'].move(x, y, z - 0.14)
            lens_z = z - 0.21
            obj['lens'].move(x, y, lens_z)
        
        # Calculate color and brightness
        r, g, b = state.red, state.green, state.blue
        brightness = (r + g + b) / 765.0
        dimmer = state.dimmer / 255.0
        total = brightness * dimmer
        
        # Color macro for effect fixtures
        if ftype == FixtureType.EFFECT and brightness < 0.1 and state.color_macro > 5:
            r, g, b = self._color_macro_to_rgb(state.color_macro)
            brightness = (r + g + b) / 765.0
            total = brightness * dimmer
        
        # Lens glow
        if total > 0.05:
            gr = min(255, int(r * 1.2 + 50))
            gg = min(255, int(g * 1.2 + 50))
            gb = min(255, int(b * 1.2 + 50))
            obj['lens'].material(f'#{gr:02x}{gg:02x}{gb:02x}')
        else:
            obj['lens'].material('#111111')
        
        # Update beams
        color = f'#{r:02x}{g:02x}{b:02x}'
        
        if ftype == FixtureType.EFFECT:
            self._update_effect_beams(obj, state, x, y, lens_z, color, total)
        else:
            self._update_spot_beam(obj, state, x, y, lens_z, color, total)
    
    def _update_spot_beam(self, obj: dict, state: FixtureState,
                          x: float, y: float, lens_z: float,
                          color: str, total: float) -> None:
        """Update spot fixture beam."""
        beam = obj.get('beam')
        if not beam:
            return
        
        if total < 0.05:
            beam.material('#333333', opacity=0.0)
            return
        
        # Pan/tilt from DMX (center is 128)
        pan = (state.pan - 128) / 128.0 * 0.8  # radians, ~45 deg each way
        tilt = (state.tilt - 128) / 128.0 * 0.5  # radians, ~30 deg each way
        
        # Beam points down (-Z), offset by half length
        # Simple approach: position center, then rotate
        half = self.BEAM_LENGTH / 2
        
        # Center of beam when pointing straight down
        cx, cy, cz = x, y, lens_z - half
        
        # Move and rotate beam
        beam.move(cx, cy, cz)
        beam.rotate(tilt, 0, pan)  # Tilt around X, pan around Z
        
        # Strobe effect
        opacity = min(0.6, total * 0.7)
        if state.strobe > 5:
            freq = state.strobe / 40.0
            if int(time.time() * freq * 10) % 2 == 0:
                opacity = min(0.8, opacity + 0.2)
            else:
                opacity *= 0.3
        
        beam.material(color, opacity=opacity)
    
    def _update_effect_beams(self, obj: dict, state: FixtureState,
                             x: float, y: float, lens_z: float,
                             color: str, total: float) -> None:
        """Update effect fixture beams with motor rotation."""
        beams = obj.get('beams', [])
        if not beams:
            return
        
        if total < 0.05:
            for beam in beams:
                beam.material('#333333', opacity=0.0)
            return
        
        # Motor rotation
        motor = 0.0
        if state.effect >= 128:
            speed = (state.effect - 128) / 127.0 * 2.0
            motor = (time.time() * speed * 2 * math.pi) % (2 * math.pi)
        elif state.effect > 0:
            motor = (state.effect / 127.0) * 2 * math.pi
        
        spread_rad = math.radians(self.EFFECT_BEAM_SPREAD)
        half = self.EFFECT_BEAM_LENGTH / 2
        num = len(beams)
        
        for i, beam in enumerate(beams):
            # Angle around the motor axis
            angle = (i / num) * 2 * math.pi + motor
            
            # Beam tilts outward at spread angle, rotated around vertical
            # Position: offset from lens in the tilted direction
            dx = math.sin(angle) * math.sin(spread_rad) * half
            dy = math.cos(angle) * math.sin(spread_rad) * half
            dz = -math.cos(spread_rad) * half
            
            cx = x + dx
            cy = y + dy
            cz = lens_z + dz
            
            beam.move(cx, cy, cz)
            # Rotate: first tilt by spread, then rotate around Z by angle
            beam.rotate(spread_rad, 0, angle)
            
            # Per-beam strobe phase
            opacity = min(0.5, total * 0.6)
            if state.strobe > 5:
                freq = state.strobe / 50.0
                phase = i * 0.15
                if int((time.time() + phase) * freq * 10) % 2 == 0:
                    opacity = min(0.75, opacity + 0.25)
                else:
                    opacity *= 0.25
            
            beam.material(color, opacity=opacity)
    
    def _clear_fixtures(self) -> None:
        self._fixture_objects.clear()
    
    def _color_macro_to_rgb(self, val: int) -> tuple[int, int, int]:
        """Convert color macro to RGB."""
        if val <= 5: return (0, 0, 0)
        if val <= 20: return (255, 0, 0)
        if val <= 35: return (0, 255, 0)
        if val <= 50: return (0, 0, 255)
        if val <= 65: return (255, 255, 255)
        if val <= 80: return (255, 255, 0)
        if val <= 95: return (255, 0, 255)
        if val <= 110: return (255, 128, 128)
        if val <= 125: return (0, 255, 255)
        if val <= 140: return (128, 255, 128)
        if val <= 155: return (128, 128, 255)
        if val <= 200: return (255, 255, 255)
        if val <= 255:
            c = int(time.time() * 2) % 7
            return [(255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255),(0,255,255),(255,255,255)][c]
        return (128, 128, 128)
