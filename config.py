"""
Configuration data models for fixtures, effects, and the show.
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ChannelType(str, Enum):
    """DMX channel types supported by fixtures."""
    NONE = "none"
    RED = "red"
    GREEN = "green"
    BLUE = "blue"
    WHITE = "white"
    PAN = "pan"
    PAN_FINE = "pan_fine"
    TILT = "tilt"
    TILT_FINE = "tilt_fine"
    SPEED = "speed"
    STROBE = "strobe"
    DIMMER = "dimmer"
    COLOR_WHEEL = "color_wheel"
    GOBO = "gobo"


class VisualizationMode(str, Enum):
    """How audio is mapped to fixture output."""
    ENERGY = "energy"           # Overall energy drives intensity
    FREQUENCY_SPLIT = "frequency_split"  # Bass/mid/high split across fixtures
    BEAT_PULSE = "beat_pulse"   # Pulse on beats
    COLOR_CYCLE = "color_cycle" # Cycle colors based on tempo
    RAINBOW_WAVE = "rainbow_wave"  # Rainbow wave across fixtures
    STROBE_BEAT = "strobe_beat"  # Strobe on beats
    RANDOM_FLASH = "random_flash"  # Random flashes on beats


class ChannelConfig(BaseModel):
    """Configuration for a single DMX channel."""
    channel: int = Field(..., ge=1, le=512, description="DMX channel number (1-512)")
    channel_type: ChannelType = Field(..., description="Type of this channel")


class FixtureConfig(BaseModel):
    """Configuration for a single fixture."""
    name: str = Field(..., description="Fixture name")
    start_channel: int = Field(..., ge=1, le=512, description="Starting DMX channel")
    channels: list[ChannelConfig] = Field(default_factory=list, description="Channel mappings")
    
    # Position in the show (for effects ordering)
    position: int = Field(default=0, description="Order/position in fixture array (0=leftmost)")
    orientation: float = Field(default=0.0, description="Orientation angle in degrees (0=facing crowd)")
    
    # Movement limits
    pan_min: int = Field(default=0, ge=0, le=255, description="Minimum pan value")
    pan_max: int = Field(default=255, ge=0, le=255, description="Maximum pan value")
    tilt_min: int = Field(default=0, ge=0, le=255, description="Minimum tilt value")
    tilt_max: int = Field(default=255, ge=0, le=255, description="Maximum tilt value")
    
    # Strobe settings
    strobe_enabled: bool = Field(default=False, description="Enable strobe mode")
    strobe_speed: int = Field(default=128, ge=0, le=255, description="Strobe speed (0-255)")


class DMXConfig(BaseModel):
    """DMX interface configuration."""
    port: str = Field(default="", description="Serial port (auto-detect if empty)")
    universe_size: int = Field(default=512, ge=1, le=512, description="DMX universe size")
    fps: int = Field(default=40, ge=1, le=44, description="DMX refresh rate")


class SpotifyConfig(BaseModel):
    """Spotify API configuration."""
    client_id: str = Field(default="", description="Spotify API client ID")
    client_secret: str = Field(default="", description="Spotify API client secret")
    redirect_uri: str = Field(default="http://localhost:8888/callback", description="OAuth redirect URI")


class EffectsConfig(BaseModel):
    """Global effects configuration."""
    mode: VisualizationMode = Field(default=VisualizationMode.ENERGY, description="Visualization mode")
    intensity: float = Field(default=1.0, ge=0.0, le=1.0, description="Overall intensity multiplier")
    color_speed: float = Field(default=1.0, ge=0.1, le=10.0, description="Color change speed")
    beat_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0, description="Beat detection sensitivity")
    smooth_factor: float = Field(default=0.3, ge=0.0, le=1.0, description="Output smoothing (0=none, 1=max)")
    strobe_on_drop: bool = Field(default=False, description="Auto-strobe on energy drops")
    movement_enabled: bool = Field(default=True, description="Enable pan/tilt movement")
    movement_speed: float = Field(default=0.5, ge=0.0, le=1.0, description="Movement speed")


class ShowConfig(BaseModel):
    """Complete show configuration."""
    name: str = Field(default="My Light Show", description="Show name")
    dmx: DMXConfig = Field(default_factory=DMXConfig)
    spotify: SpotifyConfig = Field(default_factory=SpotifyConfig)
    effects: EffectsConfig = Field(default_factory=EffectsConfig)
    fixtures: list[FixtureConfig] = Field(default_factory=list)
    
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
