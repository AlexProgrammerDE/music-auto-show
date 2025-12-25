"""
Configuration data models for fixtures, effects, and the show.
Supports dynamic channel mapping with range-based functions and fixture presets.
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ChannelFunction(str, Enum):
    """DMX channel functions - what a channel controls."""
    # Movement
    PAN = "pan"
    PAN_FINE = "pan_fine"
    TILT = "tilt"
    TILT_FINE = "tilt_fine"
    PT_SPEED = "pt_speed"  # Pan/Tilt movement speed
    
    # Color
    RED = "red"
    GREEN = "green"
    BLUE = "blue"
    WHITE = "white"
    AMBER = "amber"
    UV = "uv"
    
    # Intensity
    DIMMER = "dimmer"  # Master dimmer with optional shutter ranges
    
    # Effects
    STROBE = "strobe"
    COLOR_MACRO = "color_macro"  # Color presets/macros
    MACRO_SPEED = "macro_speed"  # Speed for color macros
    EFFECT_MACRO = "effect_macro"  # Combined effect macros (color + movement)
    
    # Control
    CONTROL = "control"  # Reset, lamp on/off, etc.


class ChannelRange(BaseModel):
    """Defines a value range within a channel that triggers a specific behavior."""
    min_value: int = Field(..., ge=0, le=255)
    max_value: int = Field(..., ge=0, le=255)
    name: str = Field(..., description="Name of this range (e.g., 'Dimmer', 'Strobe', 'Open')")
    description: str = Field(default="", description="What this range does")


class ChannelMapping(BaseModel):
    """
    Maps a channel offset to its function.
    Offset is relative to fixture's start_channel (1-based).
    """
    offset: int = Field(..., ge=1, description="Channel offset from start (1 = first channel)")
    function: ChannelFunction = Field(..., description="What this channel controls")
    default_value: int = Field(default=0, ge=0, le=255, description="Default/home value")
    ranges: list[ChannelRange] = Field(default_factory=list, description="Value ranges with special meanings")
    
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
    Can be saved as a preset and reused.
    """
    name: str = Field(..., description="Profile name (e.g., 'Muvy WashQ 14ch')")
    manufacturer: str = Field(default="", description="Manufacturer name")
    model: str = Field(default="", description="Model name")
    channel_count: int = Field(..., ge=1, le=512, description="Total number of channels")
    channels: list[ChannelMapping] = Field(..., description="Channel mappings")
    
    # Movement capabilities
    has_pan: bool = Field(default=False)
    has_tilt: bool = Field(default=False)
    pan_range: int = Field(default=540, description="Pan range in degrees")
    tilt_range: int = Field(default=230, description="Tilt range in degrees")
    
    # Color capabilities
    has_rgb: bool = Field(default=False)
    has_white: bool = Field(default=False)
    has_color_macros: bool = Field(default=False)
    
    def get_channel(self, function: ChannelFunction) -> Optional[ChannelMapping]:
        """Get channel mapping for a specific function."""
        for ch in self.channels:
            if ch.function == function:
                return ch
        return None


class FixtureConfig(BaseModel):
    """Configuration for a single fixture instance."""
    name: str = Field(..., description="Fixture name/identifier")
    profile_name: str = Field(..., description="Name of the fixture profile to use")
    start_channel: int = Field(..., ge=1, le=512, description="Starting DMX channel")
    
    # Position in the show (for effects ordering)
    position: int = Field(default=0, description="Order/position in fixture array (0=leftmost)")
    
    # Per-fixture overrides (optional)
    intensity_scale: float = Field(default=1.0, ge=0.0, le=1.0, description="Scale intensity for this fixture")
    
    # Movement limits (override profile defaults if needed)
    pan_min: int = Field(default=0, ge=0, le=255)
    pan_max: int = Field(default=255, ge=0, le=255)
    tilt_min: int = Field(default=0, ge=0, le=255)
    tilt_max: int = Field(default=255, ge=0, le=255)


class DMXConfig(BaseModel):
    """DMX interface configuration."""
    port: str = Field(default="", description="Serial port (auto-detect if empty)")
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
    profiles: list[FixtureProfile] = Field(default_factory=list, description="Available fixture profiles")
    fixtures: list[FixtureConfig] = Field(default_factory=list, description="Fixture instances")
    
    def get_profile(self, name: str) -> Optional[FixtureProfile]:
        """Get a profile by name."""
        for profile in self.profiles:
            if profile.name == name:
                return profile
        # Check built-in presets
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
# FIXTURE PRESETS
# =============================================================================

def _create_muvy_washq_profile() -> FixtureProfile:
    """Create profile for Purelight Muvy WashQ (14 channel mode)."""
    return FixtureProfile(
        name="Purelight Muvy WashQ 14ch",
        manufacturer="Purelight",
        model="Muvy WashQ",
        channel_count=14,
        has_pan=True,
        has_tilt=True,
        has_rgb=True,
        has_white=True,
        has_color_macros=True,
        pan_range=540,
        tilt_range=230,
        channels=[
            # Channel 1: Pan (0-255 = 0-540°)
            ChannelMapping(
                offset=1,
                function=ChannelFunction.PAN,
                default_value=128,
            ),
            # Channel 2: Pan Fine
            ChannelMapping(
                offset=2,
                function=ChannelFunction.PAN_FINE,
                default_value=0,
            ),
            # Channel 3: Tilt (0-255 = 0-230°)
            ChannelMapping(
                offset=3,
                function=ChannelFunction.TILT,
                default_value=128,
            ),
            # Channel 4: Tilt Fine
            ChannelMapping(
                offset=4,
                function=ChannelFunction.TILT_FINE,
                default_value=0,
            ),
            # Channel 5: P/T Speed (0=fast, 255=slow)
            ChannelMapping(
                offset=5,
                function=ChannelFunction.PT_SPEED,
                default_value=0,  # Fast by default
            ),
            # Channel 6: Dimmer/Shutter
            ChannelMapping(
                offset=6,
                function=ChannelFunction.DIMMER,
                default_value=255,  # Full open
                ranges=[
                    ChannelRange(min_value=0, max_value=7, name="Closed", description="No output"),
                    ChannelRange(min_value=8, max_value=134, name="Dimmer", description="0-100% brightness"),
                    ChannelRange(min_value=135, max_value=239, name="Strobe", description="Strobe slow to fast"),
                    ChannelRange(min_value=240, max_value=255, name="Open", description="100% without strobe"),
                ],
            ),
            # Channel 7: Red
            ChannelMapping(
                offset=7,
                function=ChannelFunction.RED,
                default_value=0,
            ),
            # Channel 8: Green
            ChannelMapping(
                offset=8,
                function=ChannelFunction.GREEN,
                default_value=0,
            ),
            # Channel 9: Blue
            ChannelMapping(
                offset=9,
                function=ChannelFunction.BLUE,
                default_value=0,
            ),
            # Channel 10: White
            ChannelMapping(
                offset=10,
                function=ChannelFunction.WHITE,
                default_value=0,
            ),
            # Channel 11: Color Macros
            ChannelMapping(
                offset=11,
                function=ChannelFunction.COLOR_MACRO,
                default_value=0,  # No macro
                ranges=[
                    ChannelRange(min_value=0, max_value=8, name="No function", description="Manual RGBW control"),
                    ChannelRange(min_value=9, max_value=20, name="RGB", description="RGB mix"),
                    ChannelRange(min_value=21, max_value=34, name="Red", description="Red"),
                    ChannelRange(min_value=35, max_value=49, name="Green", description="Green"),
                    ChannelRange(min_value=50, max_value=63, name="Blue", description="Blue"),
                    ChannelRange(min_value=64, max_value=77, name="No function", description=""),
                    ChannelRange(min_value=78, max_value=91, name="RGB", description="RGB mix"),
                    ChannelRange(min_value=92, max_value=105, name="RB", description="Red+Blue"),
                    ChannelRange(min_value=106, max_value=119, name="RG", description="Red+Green"),
                    ChannelRange(min_value=120, max_value=133, name="RGB", description="RGB mix"),
                    ChannelRange(min_value=134, max_value=147, name="RG", description="Red+Green"),
                    ChannelRange(min_value=148, max_value=161, name="RGB", description="RGB mix"),
                    ChannelRange(min_value=162, max_value=189, name="RGBW", description="RGBW mix"),
                    ChannelRange(min_value=190, max_value=201, name="RBW", description="Red+Blue+White"),
                    ChannelRange(min_value=202, max_value=217, name="Warm White", description="Warm white"),
                    ChannelRange(min_value=218, max_value=232, name="Cold White", description="Cold white"),
                    ChannelRange(min_value=233, max_value=255, name="Macro Color", description="Color change with ch12 speed"),
                ],
            ),
            # Channel 12: Macro Speed
            ChannelMapping(
                offset=12,
                function=ChannelFunction.MACRO_SPEED,
                default_value=0,
            ),
            # Channel 13: Effect Macro (color + movement)
            ChannelMapping(
                offset=13,
                function=ChannelFunction.EFFECT_MACRO,
                default_value=0,
            ),
            # Channel 14: Control (Reset)
            ChannelMapping(
                offset=14,
                function=ChannelFunction.CONTROL,
                default_value=0,
                ranges=[
                    ChannelRange(min_value=0, max_value=249, name="No function", description=""),
                    ChannelRange(min_value=250, max_value=255, name="Reset", description="Hold 3+ seconds to reset"),
                ],
            ),
        ],
    )


def _create_generic_rgb_par() -> FixtureProfile:
    """Create a generic RGB PAR profile."""
    return FixtureProfile(
        name="Generic RGB PAR",
        manufacturer="Generic",
        model="RGB PAR",
        channel_count=3,
        has_rgb=True,
        channels=[
            ChannelMapping(offset=1, function=ChannelFunction.RED, default_value=0),
            ChannelMapping(offset=2, function=ChannelFunction.GREEN, default_value=0),
            ChannelMapping(offset=3, function=ChannelFunction.BLUE, default_value=0),
        ],
    )


def _create_generic_rgbw_par() -> FixtureProfile:
    """Create a generic RGBW PAR profile."""
    return FixtureProfile(
        name="Generic RGBW PAR",
        manufacturer="Generic",
        model="RGBW PAR",
        channel_count=4,
        has_rgb=True,
        has_white=True,
        channels=[
            ChannelMapping(offset=1, function=ChannelFunction.RED, default_value=0),
            ChannelMapping(offset=2, function=ChannelFunction.GREEN, default_value=0),
            ChannelMapping(offset=3, function=ChannelFunction.BLUE, default_value=0),
            ChannelMapping(offset=4, function=ChannelFunction.WHITE, default_value=0),
        ],
    )


def _create_generic_dimmer_rgbw() -> FixtureProfile:
    """Create a generic dimmer + RGBW profile."""
    return FixtureProfile(
        name="Generic Dimmer+RGBW",
        manufacturer="Generic",
        model="Dimmer+RGBW",
        channel_count=5,
        has_rgb=True,
        has_white=True,
        channels=[
            ChannelMapping(offset=1, function=ChannelFunction.DIMMER, default_value=255),
            ChannelMapping(offset=2, function=ChannelFunction.RED, default_value=0),
            ChannelMapping(offset=3, function=ChannelFunction.GREEN, default_value=0),
            ChannelMapping(offset=4, function=ChannelFunction.BLUE, default_value=0),
            ChannelMapping(offset=5, function=ChannelFunction.WHITE, default_value=0),
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
