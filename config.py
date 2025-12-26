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


class AudioInputMode(str, Enum):
    """Audio input source mode."""
    LOOPBACK = "loopback"  # System audio loopback (WASAPI on Windows)
    MICROPHONE = "microphone"  # Microphone/line-in input
    AUTO = "auto"  # Auto-detect best available


class VisualizationMode(str, Enum):
    """How audio is mapped to fixture output."""
    ENERGY = "energy"
    FREQUENCY_SPLIT = "frequency_split"
    BEAT_PULSE = "beat_pulse"
    COLOR_CYCLE = "color_cycle"
    RAINBOW_WAVE = "rainbow_wave"
    STROBE_BEAT = "strobe_beat"
    RANDOM_FLASH = "random_flash"


class FixtureType(str, Enum):
    """Classification of fixture types for different control algorithms."""
    MOVING_HEAD = "moving_head"  # Pan/tilt with RGB/RGBW - full color mixing
    PAR = "par"  # Static RGB/RGBW wash light
    EFFECT = "effect"  # Derby, moonflower, etc. - color macros, patterns, strobes
    LASER = "laser"  # Laser effects
    STROBE = "strobe"  # Dedicated strobe light
    DIMMER = "dimmer"  # Single channel dimmer
    OTHER = "other"  # Generic/unknown


class MovementMode(str, Enum):
    """Movement modes for pan/tilt fixtures."""
    SUBTLE = "subtle"  # Minimal movement, small subtle adjustments
    STANDARD = "standard"  # Standard club/dance floor mode
    DRAMATIC = "dramatic"  # Full range movement, uses entire pan/tilt range
    WALL_WASH = "wall_wash"  # Targets walls and corners, sweeping patterns
    SWEEP = "sweep"  # Slow continuous sweeping motion, theatrical
    RANDOM = "random"  # Unpredictable movement for variety
    # Dynamic show modes
    CIRCLE = "circle"  # Circular motion - beams trace circles, phase-offset per fixture
    FIGURE_8 = "figure_8"  # Figure-8/lemniscate pattern - elegant infinity loops
    BALLYHOO = "ballyhoo"  # Fast sweeping wave motion across fixtures - classic show effect
    FAN = "fan"  # Fixtures fan in/out from center point - dramatic reveals
    CHASE = "chase"  # Sequential position chase - beams "chase" across fixtures
    STROBE_POSITION = "strobe_position"  # Fast snappy beat-synced position jumps
    CRAZY = "crazy"  # Wild full-range movement - showcases entire pan/tilt capability


class FixtureProfile(BaseModel):
    """
    Profile defining a fixture type's channel layout.
    Based on QLC+ fixture definition format.
    """
    name: str = Field(..., description="Profile name")
    manufacturer: str = Field(default="", description="Manufacturer name")
    model: str = Field(default="", description="Model name")
    fixture_type: FixtureType = Field(default=FixtureType.OTHER, description="Fixture type classification")
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
    movement_mode: MovementMode = Field(default=MovementMode.STANDARD)


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
        fixture_type=FixtureType.MOVING_HEAD,
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
                default_value=0,  # MUST be 0-8 for manual RGB control
                capabilities=[
                    ChannelCapability(min_value=0, max_value=8, name="No function (manual RGB)"),
                    ChannelCapability(min_value=9, max_value=20, name="RGBW"),
                    ChannelCapability(min_value=21, max_value=34, name="Red"),
                    ChannelCapability(min_value=35, max_value=49, name="Green"),
                    ChannelCapability(min_value=50, max_value=63, name="Blue"),
                    ChannelCapability(min_value=64, max_value=77, name="White"),
                    ChannelCapability(min_value=78, max_value=91, name="RGB"),
                    ChannelCapability(min_value=92, max_value=105, name="RB"),
                    ChannelCapability(min_value=106, max_value=119, name="RG"),
                    ChannelCapability(min_value=120, max_value=133, name="RGBW"),
                    ChannelCapability(min_value=134, max_value=147, name="RG"),
                    ChannelCapability(min_value=148, max_value=161, name="RGB"),
                    ChannelCapability(min_value=162, max_value=189, name="RGBW"),
                    ChannelCapability(min_value=190, max_value=201, name="RBW"),
                    ChannelCapability(min_value=202, max_value=217, name="Warm White (RGBW Mix)"),
                    ChannelCapability(min_value=218, max_value=232, name="Cool White (RGBW Mix)"),
                    ChannelCapability(min_value=233, max_value=255, name="Macro Color (speed via Ch12)"),
                ]
            ),
            ChannelConfig(offset=12, name="Color Speed", channel_type=ChannelType.EFFECT_SPEED, default_value=0),
            ChannelConfig(
                offset=13, 
                name="Macro P/T/M", 
                channel_type=ChannelType.EFFECT, 
                default_value=0,  # 0 = manual control, no auto patterns
                capabilities=[
                    ChannelCapability(min_value=0, max_value=0, name="Manual operation"),
                    ChannelCapability(min_value=1, max_value=255, name="Movement patterns and color change"),
                ]
            ),
            ChannelConfig(
                offset=14, 
                name="Reset", 
                channel_type=ChannelType.MAINTENANCE, 
                default_value=0,  # MUST stay 0 to avoid accidental reset
                capabilities=[
                    ChannelCapability(min_value=0, max_value=249, name="No function"),
                    ChannelCapability(min_value=250, max_value=255, name="Reset (hold 3+ sec)"),
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
        fixture_type=FixtureType.PAR,
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
        fixture_type=FixtureType.PAR,
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
        fixture_type=FixtureType.PAR,
        channel_count=5,
        channels=[
            ChannelConfig(offset=1, name="Dimmer", channel_type=ChannelType.INTENSITY_MASTER_DIMMER, default_value=255),
            ChannelConfig(offset=2, name="Red", channel_type=ChannelType.INTENSITY_RED, default_value=0),
            ChannelConfig(offset=3, name="Green", channel_type=ChannelType.INTENSITY_GREEN, default_value=0),
            ChannelConfig(offset=4, name="Blue", channel_type=ChannelType.INTENSITY_BLUE, default_value=0),
            ChannelConfig(offset=5, name="White", channel_type=ChannelType.INTENSITY_WHITE, default_value=0),
        ],
    )


def _create_showtec_techno_derby() -> FixtureProfile:
    """Create profile for Showtec Techno Derby (4 channel mode) - Article 43156."""
    return FixtureProfile(
        name="Showtec Techno Derby 4ch",
        manufacturer="Showtec",
        model="Techno Derby",
        fixture_type=FixtureType.EFFECT,
        channel_count=4,
        channels=[
            ChannelConfig(
                offset=1,
                name="Color",
                channel_type=ChannelType.COLOR_MACRO,
                default_value=0,
                capabilities=[
                    ChannelCapability(min_value=0, max_value=5, name="No function"),
                    ChannelCapability(min_value=6, max_value=20, name="Red"),
                    ChannelCapability(min_value=21, max_value=35, name="Green"),
                    ChannelCapability(min_value=36, max_value=50, name="Blue"),
                    ChannelCapability(min_value=51, max_value=65, name="White"),
                    ChannelCapability(min_value=66, max_value=80, name="Red + Green"),
                    ChannelCapability(min_value=81, max_value=95, name="Red + Blue"),
                    ChannelCapability(min_value=96, max_value=110, name="Red + White"),
                    ChannelCapability(min_value=111, max_value=125, name="Green + Blue"),
                    ChannelCapability(min_value=126, max_value=140, name="Green + White"),
                    ChannelCapability(min_value=141, max_value=155, name="Blue + White"),
                    ChannelCapability(min_value=156, max_value=170, name="Red + Green + Blue"),
                    ChannelCapability(min_value=171, max_value=185, name="Red + Green + White"),
                    ChannelCapability(min_value=186, max_value=200, name="Green + Blue + White"),
                    ChannelCapability(min_value=201, max_value=215, name="Red + Green + Blue + White"),
                    ChannelCapability(min_value=216, max_value=229, name="Color change slow"),
                    ChannelCapability(min_value=230, max_value=255, name="Color change fast"),
                ]
            ),
            ChannelConfig(
                offset=2,
                name="Strobe",
                channel_type=ChannelType.SHUTTER_STROBE_SLOW_FAST,
                default_value=0,
                capabilities=[
                    ChannelCapability(min_value=0, max_value=5, name="Strobe off"),
                    ChannelCapability(min_value=6, max_value=255, name="Strobe slow to fast"),
                ]
            ),
            ChannelConfig(
                offset=3,
                name="Pattern Rotation",
                channel_type=ChannelType.EFFECT,
                default_value=0,
                capabilities=[
                    ChannelCapability(min_value=0, max_value=0, name="No function"),
                    ChannelCapability(min_value=1, max_value=127, name="Manual rotation position"),
                    ChannelCapability(min_value=128, max_value=255, name="Auto rotation slow to fast"),
                ]
            ),
            ChannelConfig(
                offset=4,
                name="Strobe Effects",
                channel_type=ChannelType.EFFECT,
                default_value=0,
                capabilities=[
                    ChannelCapability(min_value=0, max_value=9, name="No function"),
                    ChannelCapability(min_value=10, max_value=19, name="Effect 1"),
                    ChannelCapability(min_value=20, max_value=29, name="Effect 2"),
                    ChannelCapability(min_value=30, max_value=39, name="Effect 3"),
                    ChannelCapability(min_value=40, max_value=49, name="Effect 4"),
                    ChannelCapability(min_value=50, max_value=59, name="Effect 5"),
                    ChannelCapability(min_value=60, max_value=69, name="Effect 6"),
                    ChannelCapability(min_value=70, max_value=79, name="Effect 7"),
                    ChannelCapability(min_value=80, max_value=89, name="Effect 8"),
                    ChannelCapability(min_value=90, max_value=99, name="Effect 9"),
                    ChannelCapability(min_value=100, max_value=109, name="Effect 10"),
                    ChannelCapability(min_value=110, max_value=119, name="Effect 11"),
                    ChannelCapability(min_value=120, max_value=129, name="Effect 12"),
                    ChannelCapability(min_value=130, max_value=139, name="Effect 13"),
                    ChannelCapability(min_value=140, max_value=149, name="Effect 14"),
                    ChannelCapability(min_value=150, max_value=159, name="Effect 15"),
                    ChannelCapability(min_value=160, max_value=169, name="Effect 16"),
                    ChannelCapability(min_value=170, max_value=179, name="Effect 17"),
                    ChannelCapability(min_value=180, max_value=255, name="Effect 18 (strobe always on)"),
                ]
            ),
        ],
    )


# Built-in fixture presets
FIXTURE_PRESETS: dict[str, FixtureProfile] = {
    "Purelight Muvy WashQ 14ch": _create_muvy_washq_profile(),
    "Generic RGB PAR": _create_generic_rgb_par(),
    "Generic RGBW PAR": _create_generic_rgbw_par(),
    "Generic Dimmer+RGBW": _create_generic_dimmer_rgbw(),
    "Showtec Techno Derby 4ch": _create_showtec_techno_derby(),
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
