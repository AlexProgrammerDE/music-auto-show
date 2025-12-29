"""
Effects panel component for controlling visualization modes and effects.
"""
from nicegui import ui

from config import VisualizationMode, MovementMode, EffectFixtureMode, RotationMode, StrobeEffectMode
from web.state import app_state


class EffectsPanel:
    """Effects panel for controlling visualization and effects settings."""
    
    def __init__(self):
        self._create_ui()
    
    def _create_ui(self) -> None:
        """Create the effects panel UI."""
        with ui.expansion('Effects', icon='auto_awesome', value=True).classes('w-full'):
            with ui.column().classes('w-full gap-3 p-2'):
                # Visualization mode
                mode_options = [m.value for m in VisualizationMode]
                ui.select(
                    mode_options,
                    value=app_state.config.effects.mode.value,
                    label='Mode',
                    on_change=lambda e: self._set_mode(e.value)
                ).classes('w-full')
                
                # Intensity slider
                ui.label('Intensity').classes('text-sm text-gray-400')
                ui.slider(
                    min=0.0, max=1.0, step=0.01,
                    value=app_state.config.effects.intensity,
                    on_change=lambda e: self._set_intensity(e.value)
                ).classes('w-full')
                
                # Audio gain slider
                ui.label('Audio Gain').classes('text-sm text-gray-400')
                ui.slider(
                    min=0.1, max=5.0, step=0.1,
                    value=app_state.config.effects.audio_gain,
                    on_change=lambda e: app_state.set_audio_gain(e.value)
                ).classes('w-full')
                
                # Color speed slider
                ui.label('Color Speed').classes('text-sm text-gray-400')
                ui.slider(
                    min=0.1, max=10.0, step=0.1,
                    value=app_state.config.effects.color_speed,
                    on_change=lambda e: self._set_config('color_speed', e.value)
                ).classes('w-full')
                
                # Smoothing slider
                ui.label('Smoothing').classes('text-sm text-gray-400')
                ui.slider(
                    min=0.0, max=1.0, step=0.01,
                    value=app_state.config.effects.smooth_factor,
                    on_change=lambda e: self._set_config('smooth_factor', e.value)
                ).classes('w-full')
                
                # Checkboxes
                ui.checkbox(
                    'Strobe on Drop',
                    value=app_state.config.effects.strobe_on_drop,
                    on_change=lambda e: self._set_config('strobe_on_drop', e.value)
                )
        
        # Movement settings
        with ui.expansion('Movement', icon='open_with').classes('w-full'):
            with ui.column().classes('w-full gap-3 p-2'):
                ui.checkbox(
                    'Enable Movement',
                    value=app_state.config.effects.movement_enabled,
                    on_change=lambda e: self._set_config('movement_enabled', e.value)
                )
                
                # Movement mode
                movement_options = [m.value for m in MovementMode]
                ui.select(
                    movement_options,
                    value=app_state.config.effects.movement_mode.value,
                    label='Movement Mode',
                    on_change=lambda e: self._set_movement_mode(e.value)
                ).classes('w-full')
                
                # Movement speed
                ui.label('Movement Speed').classes('text-sm text-gray-400')
                ui.slider(
                    min=0.0, max=1.0, step=0.01,
                    value=app_state.config.effects.movement_speed,
                    on_change=lambda e: self._set_config('movement_speed', e.value)
                ).classes('w-full')
        
        # Effect fixtures (Derby/Moonflower)
        with ui.expansion('Effect Fixtures', icon='blur_on').classes('w-full'):
            with ui.column().classes('w-full gap-3 p-2'):
                ui.label('For Derby/Moonflower fixtures').classes('text-xs text-gray-400')
                
                # Effect fixture mode
                effect_mode_options = [m.value for m in EffectFixtureMode]
                ui.select(
                    effect_mode_options,
                    value=app_state.config.effects.effect_fixture_mode.value,
                    label='Show Mode',
                    on_change=lambda e: self._set_effect_fixture_mode(e.value)
                ).classes('w-full')
                
                # Rotation mode
                rotation_options = [m.value for m in RotationMode]
                ui.select(
                    rotation_options,
                    value=app_state.config.effects.rotation_mode.value,
                    label='Rotation Mode',
                    on_change=lambda e: self._set_rotation_mode(e.value)
                ).classes('w-full')
                
                # Strobe effect settings
                ui.checkbox(
                    'Enable Strobe Effects',
                    value=app_state.config.effects.strobe_effect_enabled,
                    on_change=lambda e: self._set_config('strobe_effect_enabled', e.value)
                )
                
                strobe_options = [m.value for m in StrobeEffectMode]
                ui.select(
                    strobe_options,
                    value=app_state.config.effects.strobe_effect_mode.value,
                    label='Strobe Pattern',
                    on_change=lambda e: self._set_strobe_effect_mode(e.value)
                ).classes('w-full')
                
                ui.label('Effect Speed').classes('text-sm text-gray-400')
                ui.slider(
                    min=0.0, max=1.0, step=0.01,
                    value=app_state.config.effects.strobe_effect_speed,
                    on_change=lambda e: self._set_config('strobe_effect_speed', e.value)
                ).classes('w-full')
    
    def _set_mode(self, value: str) -> None:
        """Set visualization mode."""
        app_state.config.effects.mode = VisualizationMode(value)
        app_state.update_effects_config()
    
    def _set_intensity(self, value: float) -> None:
        """Set intensity."""
        app_state.config.effects.intensity = value
        app_state.update_effects_config()
    
    def _set_config(self, attr: str, value) -> None:
        """Set a config attribute."""
        setattr(app_state.config.effects, attr, value)
        app_state.update_effects_config()
    
    def _set_movement_mode(self, value: str) -> None:
        """Set movement mode."""
        app_state.config.effects.movement_mode = MovementMode(value)
        app_state.update_effects_config()
    
    def _set_effect_fixture_mode(self, value: str) -> None:
        """Set effect fixture mode."""
        app_state.config.effects.effect_fixture_mode = EffectFixtureMode(value)
        app_state.update_effects_config()
    
    def _set_rotation_mode(self, value: str) -> None:
        """Set rotation mode."""
        app_state.config.effects.rotation_mode = RotationMode(value)
        app_state.update_effects_config()
    
    def _set_strobe_effect_mode(self, value: str) -> None:
        """Set strobe effect mode."""
        app_state.config.effects.strobe_effect_mode = StrobeEffectMode(value)
        app_state.update_effects_config()
