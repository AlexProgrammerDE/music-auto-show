"""
UI Components for Music Auto Show web interface.
"""
from .config_panel import ConfigPanel
from .effects_panel import EffectsPanel
from .fixture_list import FixtureList
from .fixture_dialogs import FixtureDialogs
from .stage_view import StageView
from .audio_meters import AudioMeters
from .dmx_universe import DMXUniverse

__all__ = [
    'ConfigPanel',
    'EffectsPanel', 
    'FixtureList',
    'FixtureDialogs',
    'StageView',
    'AudioMeters',
    'DMXUniverse',
]
