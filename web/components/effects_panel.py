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
    
    def _create_slider_row(self, label: str, min_val: float, max_val: float, 
                           step: float, value: float, on_change, 
                           format_pct: bool = False, suffix: str = '') -> None:
        """Create a slider row with label and value display."""
        from typing import Any
        
        with ui.column().classes('w-full gap-1'):
            with ui.row().classes('justify-between items-center w-full'):
                ui.label(label).classes('text-sm font-medium').style('color: #8b949e;')
                if format_pct:
                    val_label = ui.label(f'{value:.0%}').classes('text-sm font-mono').style('color: #39d0d8;')
                else:
                    val_label = ui.label(f'{value:.1f}{suffix}').classes('text-sm font-mono').style('color: #39d0d8;')
            
            def update_label(e: Any, lbl=val_label, pct=format_pct, suf=suffix):
                if pct:
                    lbl.text = f'{e.value:.0%}'
                else:
                    lbl.text = f'{e.value:.1f}{suf}'
                on_change(e)
            
            ui.slider(
                min=min_val, max=max_val, step=step,
                value=value,
                on_change=update_label
            ).classes('w-full')
    
    def _create_ui(self) -> None:
        """Create the effects panel UI."""
        with ui.expansion('Effects', icon='auto_awesome', value=True).classes('w-full'):
            with ui.column().classes('w-full gap-4 p-3'):
                # Visualization mode
                mode_options = [m.value for m in VisualizationMode]
                ui.select(
                    mode_options,
                    value=app_state.config.effects.mode.value,
                    label='Mode',
                    on_change=lambda e: self._set_mode(e.value)
                ).classes('w-full')
                
                # Intensity slider with value display
                self._create_slider_row(
                    'Intensity', 0.0, 1.0, 0.01,
                    app_state.config.effects.intensity,
                    lambda e: self._set_intensity(e.value),
                    format_pct=True
                )
                
                # Audio gain slider
                self._create_slider_row(
                    'Audio Gain', 0.1, 5.0, 0.1,
                    app_state.config.effects.audio_gain,
                    lambda e: app_state.set_audio_gain(e.value),
                    suffix='x'
                )
                
                # Color speed slider
                self._create_slider_row(
                    'Color Speed', 0.1, 10.0, 0.1,
                    app_state.config.effects.color_speed,
                    lambda e: self._set_config('color_speed', e.value),
                    suffix='x'
                )
                
                # Smoothing slider
                self._create_slider_row(
                    'Smoothing', 0.0, 1.0, 0.01,
                    app_state.config.effects.smooth_factor,
                    lambda e: self._set_config('smooth_factor', e.value),
                    format_pct=True
                )
                
                # Checkboxes
                ui.checkbox(
                    'Strobe on Drop',
                    value=app_state.config.effects.strobe_on_drop,
                    on_change=lambda e: self._set_config('strobe_on_drop', e.value)
                ).classes('mt-2')
        
        # Movement settings
        with ui.expansion('Movement', icon='open_with').classes('w-full'):
            with ui.column().classes('w-full gap-4 p-3'):
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
                self._create_slider_row(
                    'Movement Speed', 0.0, 1.0, 0.01,
                    app_state.config.effects.movement_speed,
                    lambda e: self._set_config('movement_speed', e.value),
                    format_pct=True
                )
        
        # Effect fixtures (Derby/Moonflower)
        with ui.expansion('Effect Fixtures', icon='blur_on').classes('w-full'):
            with ui.column().classes('w-full gap-4 p-3'):
                ui.label('For Derby/Moonflower fixtures').classes('text-xs').style('color: #8b949e;')
                
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
                
                self._create_slider_row(
                    'Effect Speed', 0.0, 1.0, 0.01,
                    app_state.config.effects.strobe_effect_speed,
                    lambda e: self._set_config('strobe_effect_speed', e.value),
                    format_pct=True
                )
    
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
