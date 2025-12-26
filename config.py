"""
Configuration data models for fixtures, effects, and the show.
Supports dynamic channel mapping with QLC+ compatible channel types.
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ChannelType(str, Enum):
    """
    DMX channel types - compatible with QLC+ presets.
    See: https://www.qlcplus.org/docs/html_en_EN/fixturedefinitioneditor.html
    """
    # Intensity
    INTENSITY = "intensity"              # Generic intensity/dimmer
    INTENSITY_MASTER_DIMMER = "intensity_master_dimmer"
    INTENSITY_DIMMER = "intensity_dimmer"
    INTENSITY_RED = "intensity_red"
    INTENSITY_GREEN = "intensity_green"
    INTENSITY_BLUE = "intensity_blue"
    INTENSITY_WHITE = "intensity_white"
    INTENSITY_AMBER = "intensity_amber"
    INTENSITY_UV = "intensity_uv"
    INTENSITY_CYAN = "intensity_cyan"
    INTENSITY_MAGENTA = "intensity_magenta"
    INTENSITY_YELLOW = "intensity_yellow"
    INTENSITY_HUE = "intensity_hue"
    INTENSITY_SATURATION = "intensity_saturation"
    INTENSITY_VALUE = "intensity_value"
    
    # Position
    POSITION_PAN = "position_pan"
    POSITION_PAN_FINE = "position_pan_fine"
    POSITION_TILT = "position_tilt"
    POSITION_TILT_FINE = "position_tilt_fine"
    
    # Speed
    SPEED_PAN_TILT_FAST_SLOW = "speed_pan_tilt_fast_slow"
    SPEED_PAN_TILT_SLOW_FAST = "speed_pan_tilt_slow_fast"
    
    # Color
    COLOR_WHEEL = "color_wheel"
    COLOR_MACRO = "color_macro"
    COLOR_CTO_MIXER = "color_cto_mixer"  # Color temperature orange
    COLOR_CTB_MIXER = "color_ctb_mixer"  # Color temperature blue
    
    # Gobo
    GOBO_WHEEL = "gobo_wheel"
    GOBO_INDEX = "gobo_index"
    
    # Shutter
    SHUTTER_STROBE = "shutter_strobe"
    SHUTTER_STROBE_SLOW_FAST = "shutter_strobe_slow_fast"
    SHUTTER_STROBE_FAST_SLOW = "shutter_strobe_fast_slow"
    SHUTTER_IRIS_MIN_TO_MAX = "shutter_iris_min_to_max"
    SHUTTER_IRIS_MAX_TO_MIN = "shutter_iris_max_to_min"
    
    # Beam
    BEAM_ZOOM_SMALL_BIG = "beam_zoom_small_big"
    BEAM_ZOOM_BIG_SMALL = "beam_zoom_big_small"
    BEAM_FOCUS_NEAR_FAR = "beam_focus_near_far"
    BEAM_FOCUS_FAR_NEAR = "beam_focus_far_near"
    
    # Prism
    PRISM = "prism"
    PRISM_ROTATION = "prism_rotation"
    
    # Effect
    EFFECT = "effect"
    EFFECT_SPEED = "effect_speed"
    
    # Maintenance
    MAINTENANCE = "maintenance"
    NOTHING = "nothing"  # No function / reserved
    
    # Special - for forced/fixed values
    FIXED = "fixed"  # Channel forced to a specific value


class ChannelCapability(BaseModel):
    """A capability range within a channel (like QLC+ Capability)."""
    min_value: int = Field(..., ge=0, le=255)
    max_value: int = Field(..., ge=0, le=255)
    name: str = Field(..., description="Name of this capability")
    description: str = Field(default="")


class ChannelConfig(BaseModel):
    """
    Configuration for a single DMX channel.
    Can be from a profile or custom-defined per fixture.
    """
    offset: int = Field(..., ge=1, description="Channel offset from start (1 = first channel)")
    name: str = Field(default="", description="Channel name (e.g., 'Red Dimmer')")
    channel_type: ChannelType = Field(..., description="What this channel controls")
    default_value: int = Field(default=0, ge=0, le=255, description="Default/home value")
    
    # For FIXED type - the value to always output
    fixed_value: Optional[int] = Field(default=None, ge=0, le=255, description="Fixed DMX value (only for FIXED type)")
    
    # Capabilities (value ranges with meanings)
    capabilities: list[ChannelCapability] = Field(default_factory=list)
    
    # Whether effects engine controls this channel
    enabled: bool = Field(default=True, description="Whether this channel is controlled by effects")
    
    def get_dmx_channel(self, start_channel: int) -> int:
        """Get actual DMX channel number."""
        return start_channel + self.offset - 1


class VisualizationMode(str, Enum):
    """How audio is mapped to fixture output."""
    ENERGY = "energy"
    FREQUENCY_SPLIT = "frequency_split"
    BEAT_PULSE = "beat_pulse"
    COLOR_CYCLE = "color_cycle"
    RAINBOW_WAVE = "rainbow_wave"
    STROBE_BEAT = "strobe_beat"
    RANDOM_FLASH = "random_flash"


class FixtureProfile(BaseModel):
    """
    Profile defining a fixture type's channel layout.
    Based on QLC+ fixture definition format.
    """
    name: str = Field(..., description="Profile name")
    manufacturer: str = Field(default="", description="Manufacturer name")
    model: str = Field(default="", description="Model name")
    fixture_type: str = Field(default="Generic", description="Fixture type (e.g., 'Moving Head', 'PAR')")
    channel_count: int = Field(..., ge=1, le=512)
    channels: list[ChannelConfig] = Field(..., description="Channel definitions")
    
    # Physical properties (from QLC+)
    pan_max: int = Field(default=540, description="Max pan degrees")
    tilt_max: int = Field(default=270, description="Max tilt degrees")
    
    def get_channel_by_offset(self, offset: int) -> Optional[ChannelConfig]:
        """Get channel config by offset."""
        for ch in self.channels:
            if ch.offset == offset:
                return ch
        return None
    
    def get_channel_by_type(self, channel_type: ChannelType) -> Optional[ChannelConfig]:
        """Get first channel with given type."""
        for ch in self.channels:
            if ch.channel_type == channel_type:
                return ch
        return None


class FixtureConfig(BaseModel):
    """Configuration for a single fixture instance."""
    name: str = Field(..., description="Fixture name/identifier")
    profile_name: str = Field(default="", description="Name of the fixture profile (empty for custom)")
    start_channel: int = Field(..., ge=1, le=512, description="Starting DMX channel")
    
    # Position in the show (for effects ordering)
    position: int = Field(default=0, description="Order/position in fixture array")
    
    # Per-fixture settings
    intensity_scale: float = Field(default=1.0, ge=0.0, le=1.0)
    
    # Movement limits
    pan_min: int = Field(default=0, ge=0, le=255)
    pan_max: int = Field(default=255, ge=0, le=255)
    tilt_min: int = Field(default=0, ge=0, le=255)
    tilt_max: int = Field(default=255, ge=0, le=255)
    
    # Channel configurations - copied from profile and can be modified per-fixture
    # If empty, uses profile channels directly
    channels: list[ChannelConfig] = Field(default_factory=list)
    
    def get_channels(self, profile: Optional[FixtureProfile] = None) -> list[ChannelConfig]:
        """Get effective channel list (fixture-specific or from profile)."""
        if self.channels:
            return self.channels
        if profile:
            return profile.channels
        return []
    
    def copy_channels_from_profile(self, profile: FixtureProfile) -> None:
        """Copy channel configs from profile to allow per-fixture customization."""
        self.channels = [
            ChannelConfig(
                offset=ch.offset,
                name=ch.name,
                channel_type=ch.channel_type,
                default_value=ch.default_value,
                fixed_value=ch.fixed_value,
                capabilities=list(ch.capabilities),
                enabled=ch.enabled
            )
            for ch in profile.channels
        ]


class DMXConfig(BaseModel):
    """DMX interface configuration."""
    port: str = Field(default="")
    universe_size: int = Field(default=512, ge=1, le=512)
    fps: int = Field(default=40, ge=1, le=44)


class EffectsConfig(BaseModel):
    """Global effects configuration."""
    mode: VisualizationMode = Field(default=VisualizationMode.ENERGY)
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    color_speed: float = Field(default=1.0, ge=0.1, le=10.0)
    beat_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    smooth_factor: float = Field(default=0.3, ge=0.0, le=1.0)
    strobe_on_drop: bool = Field(default=False)
    movement_enabled: bool = Field(default=True)
    movement_speed: float = Field(default=0.5, ge=0.0, le=1.0)


class ShowConfig(BaseModel):
    """Complete show configuration."""
    name: str = Field(default="My Light Show")
    dmx: DMXConfig = Field(default_factory=DMXConfig)
    effects: EffectsConfig = Field(default_factory=EffectsConfig)
    profiles: list[FixtureProfile] = Field(default_factory=list)
    fixtures: list[FixtureConfig] = Field(default_factory=list)
    
    def get_profile(self, name: str) -> Optional[FixtureProfile]:
        """Get a profile by name."""
        for profile in self.profiles:
            if profile.name == name:
                return profile
        return FIXTURE_PRESETS.get(name)
    
    def save(self, path: str) -> None:
        """Save configuration to JSON file."""
        import json
        with open(path, 'w') as f:
            json.dump(self.model_dump(), f, indent=2)
    
    @classmethod
    def load(cls, path: str) -> "ShowConfig":
        """Load configuration from JSON file."""
        import json
        with open(path, 'r') as f:
            data = json.load(f)
        return cls.model_validate(data)


# =============================================================================
# FIXTURE PRESETS (QLC+ compatible)
# =============================================================================

def _create_muvy_washq_profile() -> FixtureProfile:
    """Create profile for Purelight Muvy WashQ (14 channel mode) - based on QLC+ definition."""
    return FixtureProfile(
        name="Purelight Muvy WashQ 14ch",
        manufacturer="Purelight",
        model="Muvy WashQ",
        fixture_type="Moving Head",
        channel_count=14,
        pan_max=545,
        tilt_max=184,
        channels=[
            ChannelConfig(offset=1, name="Pan", channel_type=ChannelType.POSITION_PAN, default_value=128),
            ChannelConfig(offset=2, name="Pan Fine", channel_type=ChannelType.POSITION_PAN_FINE, default_value=0),
            ChannelConfig(offset=3, name="Tilt", channel_type=ChannelType.POSITION_TILT, default_value=128),
            ChannelConfig(offset=4, name="Tilt Fine", channel_type=ChannelType.POSITION_TILT_FINE, default_value=0),
            ChannelConfig(offset=5, name="XY Speed", channel_type=ChannelType.SPEED_PAN_TILT_FAST_SLOW, default_value=0),
            ChannelConfig(
                offset=6, 
                name="Dimmer/Shutter", 
                channel_type=ChannelType.INTENSITY_MASTER_DIMMER, 
                default_value=255,
                capabilities=[
                    ChannelCapability(min_value=0, max_value=7, name="Off"),
                    ChannelCapability(min_value=8, max_value=134, name="Master Dimmer"),
                    ChannelCapability(min_value=135, max_value=239, name="Strobe (slow to fast)"),
                    ChannelCapability(min_value=240, max_value=255, name="Open"),
                ]
            ),
            ChannelConfig(offset=7, name="Red", channel_type=ChannelType.INTENSITY_RED, default_value=0),
            ChannelConfig(offset=8, name="Green", channel_type=ChannelType.INTENSITY_GREEN, default_value=0),
            ChannelConfig(offset=9, name="Blue", channel_type=ChannelType.INTENSITY_BLUE, default_value=0),
            ChannelConfig(offset=10, name="White", channel_type=ChannelType.INTENSITY_WHITE, default_value=0),
            ChannelConfig(
                offset=11, 
                name="Color Macro", 
                channel_type=ChannelType.COLOR_MACRO, 
                default_value=0,
                capabilities=[
                    ChannelCapability(min_value=0, max_value=7, name="Color selection (manual)"),
                    ChannelCapability(min_value=8, max_value=231, name="Macro color"),
                    ChannelCapability(min_value=232, max_value=255, name="Color change jumpy"),
                ]
            ),
            ChannelConfig(offset=12, name="Color Speed", channel_type=ChannelType.EFFECT_SPEED, default_value=0),
            ChannelConfig(
                offset=13, 
                name="Effect Mode", 
                channel_type=ChannelType.EFFECT, 
                default_value=0,
                capabilities=[
                    ChannelCapability(min_value=0, max_value=7, name="Manual operation"),
                    ChannelCapability(min_value=8, max_value=63, name="Auto fast"),
                    ChannelCapability(min_value=64, max_value=127, name="Auto slow"),
                    ChannelCapability(min_value=128, max_value=191, name="Music control 1"),
                    ChannelCapability(min_value=192, max_value=255, name="Music control 2"),
                ]
            ),
            ChannelConfig(
                offset=14, 
                name="Reset", 
                channel_type=ChannelType.MAINTENANCE, 
                default_value=0,
                capabilities=[
                    ChannelCapability(min_value=0, max_value=149, name="No function"),
                    ChannelCapability(min_value=150, max_value=200, name="Reset"),
                    ChannelCapability(min_value=201, max_value=255, name="No function"),
                ]
            ),
        ],
    )


def _create_generic_rgb_par() -> FixtureProfile:
    """Create a generic RGB PAR profile."""
    return FixtureProfile(
        name="Generic RGB PAR",
        manufacturer="Generic",
        model="RGB PAR",
        fixture_type="PAR",
        channel_count=3,
        channels=[
            ChannelConfig(offset=1, name="Red", channel_type=ChannelType.INTENSITY_RED, default_value=0),
            ChannelConfig(offset=2, name="Green", channel_type=ChannelType.INTENSITY_GREEN, default_value=0),
            ChannelConfig(offset=3, name="Blue", channel_type=ChannelType.INTENSITY_BLUE, default_value=0),
        ],
    )


def _create_generic_rgbw_par() -> FixtureProfile:
    """Create a generic RGBW PAR profile."""
    return FixtureProfile(
        name="Generic RGBW PAR",
        manufacturer="Generic",
        model="RGBW PAR",
        fixture_type="PAR",
        channel_count=4,
        channels=[
            ChannelConfig(offset=1, name="Red", channel_type=ChannelType.INTENSITY_RED, default_value=0),
            ChannelConfig(offset=2, name="Green", channel_type=ChannelType.INTENSITY_GREEN, default_value=0),
            ChannelConfig(offset=3, name="Blue", channel_type=ChannelType.INTENSITY_BLUE, default_value=0),
            ChannelConfig(offset=4, name="White", channel_type=ChannelType.INTENSITY_WHITE, default_value=0),
        ],
    )


def _create_generic_dimmer_rgbw() -> FixtureProfile:
    """Create a generic dimmer + RGBW profile."""
    return FixtureProfile(
        name="Generic Dimmer+RGBW",
        manufacturer="Generic",
        model="Dimmer+RGBW PAR",
        fixture_type="PAR",
        channel_count=5,
        channels=[
            ChannelConfig(offset=1, name="Dimmer", channel_type=ChannelType.INTENSITY_MASTER_DIMMER, default_value=255),
            ChannelConfig(offset=2, name="Red", channel_type=ChannelType.INTENSITY_RED, default_value=0),
            ChannelConfig(offset=3, name="Green", channel_type=ChannelType.INTENSITY_GREEN, default_value=0),
            ChannelConfig(offset=4, name="Blue", channel_type=ChannelType.INTENSITY_BLUE, default_value=0),
            ChannelConfig(offset=5, name="White", channel_type=ChannelType.INTENSITY_WHITE, default_value=0),
        ],
    )


# Built-in fixture presets
FIXTURE_PRESETS: dict[str, FixtureProfile] = {
    "Purelight Muvy WashQ 14ch": _create_muvy_washq_profile(),
    "Generic RGB PAR": _create_generic_rgb_par(),
    "Generic RGBW PAR": _create_generic_rgbw_par(),
    "Generic Dimmer+RGBW": _create_generic_dimmer_rgbw(),
}


def get_available_presets() -> list[str]:
    """Get list of available fixture preset names."""
    return list(FIXTURE_PRESETS.keys())


def get_preset(name: str) -> Optional[FixtureProfile]:
    """Get a fixture preset by name."""
    return FIXTURE_PRESETS.get(name)


def get_channel_type_display_name(ct: ChannelType) -> str:
    """Get human-readable name for channel type."""
    names = {
        ChannelType.INTENSITY: "Intensity",
        ChannelType.INTENSITY_MASTER_DIMMER: "Master Dimmer",
        ChannelType.INTENSITY_DIMMER: "Dimmer",
        ChannelType.INTENSITY_RED: "Red",
        ChannelType.INTENSITY_GREEN: "Green",
        ChannelType.INTENSITY_BLUE: "Blue",
        ChannelType.INTENSITY_WHITE: "White",
        ChannelType.INTENSITY_AMBER: "Amber",
        ChannelType.INTENSITY_UV: "UV",
        ChannelType.INTENSITY_CYAN: "Cyan",
        ChannelType.INTENSITY_MAGENTA: "Magenta",
        ChannelType.INTENSITY_YELLOW: "Yellow",
        ChannelType.INTENSITY_HUE: "Hue",
        ChannelType.INTENSITY_SATURATION: "Saturation",
        ChannelType.INTENSITY_VALUE: "Value",
        ChannelType.POSITION_PAN: "Pan",
        ChannelType.POSITION_PAN_FINE: "Pan Fine",
        ChannelType.POSITION_TILT: "Tilt",
        ChannelType.POSITION_TILT_FINE: "Tilt Fine",
        ChannelType.SPEED_PAN_TILT_FAST_SLOW: "P/T Speed (fast-slow)",
        ChannelType.SPEED_PAN_TILT_SLOW_FAST: "P/T Speed (slow-fast)",
        ChannelType.COLOR_WHEEL: "Color Wheel",
        ChannelType.COLOR_MACRO: "Color Macro",
        ChannelType.COLOR_CTO_MIXER: "CTO",
        ChannelType.COLOR_CTB_MIXER: "CTB",
        ChannelType.GOBO_WHEEL: "Gobo Wheel",
        ChannelType.GOBO_INDEX: "Gobo Index",
        ChannelType.SHUTTER_STROBE: "Strobe",
        ChannelType.SHUTTER_STROBE_SLOW_FAST: "Strobe (slow-fast)",
        ChannelType.SHUTTER_STROBE_FAST_SLOW: "Strobe (fast-slow)",
        ChannelType.SHUTTER_IRIS_MIN_TO_MAX: "Iris",
        ChannelType.SHUTTER_IRIS_MAX_TO_MIN: "Iris (inv)",
        ChannelType.BEAM_ZOOM_SMALL_BIG: "Zoom",
        ChannelType.BEAM_ZOOM_BIG_SMALL: "Zoom (inv)",
        ChannelType.BEAM_FOCUS_NEAR_FAR: "Focus",
        ChannelType.BEAM_FOCUS_FAR_NEAR: "Focus (inv)",
        ChannelType.PRISM: "Prism",
        ChannelType.PRISM_ROTATION: "Prism Rotation",
        ChannelType.EFFECT: "Effect",
        ChannelType.EFFECT_SPEED: "Effect Speed",
        ChannelType.MAINTENANCE: "Maintenance",
        ChannelType.NOTHING: "Nothing",
        ChannelType.FIXED: "Fixed Value",
    }
    return names.get(ct, ct.value)
