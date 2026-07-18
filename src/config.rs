use std::{
    collections::{HashMap, HashSet},
    fs::{self, File},
    io::{Read, Write},
    ops::Deref,
    path::{Path, PathBuf},
};

use anyhow::{Result as AnyResult, bail};
use serde_json::{Map, Value};
use tempfile::NamedTempFile;
use thiserror::Error;

use crate::proto::v1::{
    AudioConfig, AudioInputMode, ChannelCapability, ChannelConfig, DmxConfig, DualColorMapping,
    EffectFixtureMode, EffectsConfig, FixtureConfig, FixtureProfile, MovementMode, RotationMode,
    ShowConfig, StrobeEffectMode, VisualizationMode,
};

const MAX_CONFIG_BYTES: usize = 1024 * 1024;
const SUPPORTED_CHANNEL_TYPES: &[&str] = &[
    "nothing",
    "fixed",
    "maintenance",
    "intensity",
    "intensity_dimmer",
    "intensity_master_dimmer",
    "intensity_red",
    "intensity_green",
    "intensity_blue",
    "intensity_white",
    "intensity_amber",
    "intensity_uv",
    "intensity_cyan",
    "intensity_magenta",
    "intensity_yellow",
    "position_pan",
    "position_pan_fine",
    "position_tilt",
    "position_tilt_fine",
    "speed_pan_tilt_fast_slow",
    "speed_pan_tilt_slow_fast",
    "shutter_strobe",
    "shutter_strobe_slow_fast",
    "shutter_strobe_fast_slow",
    "color_macro",
    "color_wheel",
    "effect",
    "effect_speed",
    "effect_pattern",
    "effect_pattern_speed",
    "gobo_wheel",
    "gobo_index",
    "prism",
    "prism_rotation",
    "beam_zoom_small_big",
    "beam_zoom_big_small",
    "beam_focus_near_far",
    "beam_focus_far_near",
    "shutter_iris_min_to_max",
    "shutter_iris_max_to_min",
];

#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("failed to {operation} {}: {source}", path.display())]
    Io {
        operation: &'static str,
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("invalid JSON: {0}")]
    InvalidJson(#[source] serde_json::Error),
    #[error("configuration does not match the show schema: {0}")]
    InvalidSchema(#[source] serde_json::Error),
    #[error("failed to serialize show configuration: {0}")]
    Serialization(#[source] serde_json::Error),
    #[error("{0}")]
    Invalid(String),
}

impl ConfigError {
    pub fn is_invalid_input(&self) -> bool {
        matches!(
            self,
            Self::InvalidJson(_) | Self::InvalidSchema(_) | Self::Invalid(_)
        )
    }
}

#[derive(Clone, Debug)]
pub struct ValidatedShowConfig {
    proto: ShowConfig,
    audio: AudioConfig,
    audio_mode: AudioInputMode,
    dmx: DmxConfig,
    effects: EffectsConfig,
    visualization_mode: VisualizationMode,
    movement_mode: MovementMode,
    effect_fixture_mode: EffectFixtureMode,
    rotation_mode: RotationMode,
    strobe_effect_mode: StrobeEffectMode,
}

impl ValidatedShowConfig {
    pub fn new(mut config: ShowConfig, simulate: bool) -> Result<Self, ConfigError> {
        normalize_config(&mut config, simulate)
            .map_err(|error| ConfigError::Invalid(error.to_string()))?;
        let audio = config
            .audio
            .clone()
            .ok_or_else(|| ConfigError::Invalid("audio configuration is missing".into()))?;
        let dmx = config
            .dmx
            .clone()
            .ok_or_else(|| ConfigError::Invalid("DMX configuration is missing".into()))?;
        let effects = config
            .effects
            .ok_or_else(|| ConfigError::Invalid("effects configuration is missing".into()))?;
        let audio_mode = AudioInputMode::try_from(audio.mode)
            .map_err(|_| ConfigError::Invalid("audio input mode is invalid".into()))?;
        let visualization_mode = VisualizationMode::try_from(effects.mode)
            .map_err(|_| ConfigError::Invalid("visualization mode is invalid".into()))?;
        let movement_mode = MovementMode::try_from(effects.movement_mode)
            .map_err(|_| ConfigError::Invalid("movement mode is invalid".into()))?;
        let effect_fixture_mode = EffectFixtureMode::try_from(effects.effect_fixture_mode)
            .map_err(|_| ConfigError::Invalid("effect fixture mode is invalid".into()))?;
        let rotation_mode = RotationMode::try_from(effects.rotation_mode)
            .map_err(|_| ConfigError::Invalid("rotation mode is invalid".into()))?;
        let strobe_effect_mode = StrobeEffectMode::try_from(effects.strobe_effect_mode)
            .map_err(|_| ConfigError::Invalid("strobe effect mode is invalid".into()))?;
        Ok(Self {
            proto: config,
            audio,
            audio_mode,
            dmx,
            effects,
            visualization_mode,
            movement_mode,
            effect_fixture_mode,
            rotation_mode,
            strobe_effect_mode,
        })
    }

    pub fn as_proto(&self) -> &ShowConfig {
        &self.proto
    }

    pub fn into_proto(self) -> ShowConfig {
        self.proto
    }

    pub fn audio(&self) -> &AudioConfig {
        &self.audio
    }

    pub fn audio_mode(&self) -> AudioInputMode {
        self.audio_mode
    }

    pub fn dmx(&self) -> &DmxConfig {
        &self.dmx
    }

    pub fn effects(&self) -> &EffectsConfig {
        &self.effects
    }

    pub fn visualization_mode(&self) -> VisualizationMode {
        self.visualization_mode
    }

    pub fn movement_mode(&self) -> MovementMode {
        self.movement_mode
    }

    pub fn effect_fixture_mode(&self) -> EffectFixtureMode {
        self.effect_fixture_mode
    }

    pub fn rotation_mode(&self) -> RotationMode {
        self.rotation_mode
    }

    pub fn strobe_effect_mode(&self) -> StrobeEffectMode {
        self.strobe_effect_mode
    }
}

impl Deref for ValidatedShowConfig {
    type Target = ShowConfig;

    fn deref(&self) -> &Self::Target {
        &self.proto
    }
}

pub fn load(path: &Path, simulate: bool) -> Result<ValidatedShowConfig, ConfigError> {
    if !path.exists() {
        return ValidatedShowConfig::new(default_show_config(simulate), simulate);
    }

    let mut file = File::open(path).map_err(|source| ConfigError::Io {
        operation: "open configuration at",
        path: path.to_owned(),
        source,
    })?;
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take((MAX_CONFIG_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|source| ConfigError::Io {
            operation: "read configuration from",
            path: path.to_owned(),
            source,
        })?;
    if bytes.len() > MAX_CONFIG_BYTES {
        return Err(ConfigError::Invalid(format!(
            "show configuration exceeds the {MAX_CONFIG_BYTES}-byte limit"
        )));
    }
    let contents = String::from_utf8(bytes)
        .map_err(|_| ConfigError::Invalid("show configuration is not valid UTF-8".into()))?;
    parse_json(&contents, simulate).map_err(|error| match error {
        ConfigError::Invalid(message) => ConfigError::Invalid(format!(
            "invalid show configuration in {}: {message}",
            path.display()
        )),
        other => other,
    })
}

pub fn parse_json(contents: &str, simulate: bool) -> Result<ValidatedShowConfig, ConfigError> {
    if contents.len() > MAX_CONFIG_BYTES {
        return Err(ConfigError::Invalid(format!(
            "show configuration exceeds the {MAX_CONFIG_BYTES}-byte limit"
        )));
    }
    let value: Value = serde_json::from_str(contents).map_err(ConfigError::InvalidJson)?;
    let migrated = migrate_legacy_config(value, simulate);
    reject_unknown_config_fields(&migrated)?;
    let config = serde_json::from_value(migrated).map_err(ConfigError::InvalidSchema)?;
    ValidatedShowConfig::new(config, simulate)
}

pub fn to_json(config: &ValidatedShowConfig) -> Result<String, ConfigError> {
    serde_json::to_string_pretty(config.as_proto()).map_err(ConfigError::Serialization)
}

pub fn save(path: &Path, config: &ValidatedShowConfig) -> Result<(), ConfigError> {
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent).map_err(|source| ConfigError::Io {
        operation: "create configuration directory",
        path: parent.to_owned(),
        source,
    })?;
    let json = to_json(config)?;
    if json.len() > MAX_CONFIG_BYTES {
        return Err(ConfigError::Invalid(format!(
            "show configuration exceeds the {MAX_CONFIG_BYTES}-byte limit"
        )));
    }
    let mut temporary = NamedTempFile::new_in(parent).map_err(|source| ConfigError::Io {
        operation: "create temporary configuration in",
        path: parent.to_owned(),
        source,
    })?;
    temporary
        .write_all(format!("{json}\n").as_bytes())
        .and_then(|()| temporary.as_file().sync_all())
        .map_err(|source| ConfigError::Io {
            operation: "write configuration to",
            path: path.to_owned(),
            source,
        })?;
    temporary.persist(path).map_err(|error| ConfigError::Io {
        operation: "atomically replace configuration at",
        path: path.to_owned(),
        source: error.error,
    })?;
    sync_directory(parent)
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), ConfigError> {
    let directory = fs::File::open(path).map_err(|source| ConfigError::Io {
        operation: "open configuration directory",
        path: path.to_owned(),
        source,
    })?;
    directory.sync_all().map_err(|source| ConfigError::Io {
        operation: "flush configuration directory",
        path: path.to_owned(),
        source,
    })
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), ConfigError> {
    Ok(())
}

fn reject_unknown_config_fields(value: &Value) -> Result<(), ConfigError> {
    let root = object_at(value, "configuration")?;
    reject_unknown_keys(
        root,
        &[
            "name",
            "dmx",
            "audio",
            "effects",
            "profiles",
            "fixtures",
            "allow_dmx_overlaps",
        ],
        "configuration",
    )?;
    validate_optional_object(root, "dmx", &["port", "universe_size", "fps", "simulate"])?;
    validate_optional_object(
        root,
        "audio",
        &[
            "mode",
            "simulate",
            "gain",
            "beatnet_model_path",
            "device_id",
        ],
    )?;
    validate_optional_object(
        root,
        "effects",
        &[
            "mode",
            "intensity",
            "force_max_brightness",
            "color_speed",
            "beat_sensitivity",
            "smooth_factor",
            "strobe_on_drop",
            "movement_enabled",
            "movement_speed",
            "movement_mode",
            "effect_fixture_mode",
            "rotation_mode",
            "strobe_effect_enabled",
            "strobe_effect_mode",
            "strobe_effect_speed",
        ],
    )?;
    validate_object_array(
        root,
        "profiles",
        &[
            "name",
            "manufacturer",
            "model",
            "fixture_type",
            "channel_count",
            "channels",
            "color_mixing",
            "dual_color_map",
            "pan_max_degrees",
            "tilt_max_degrees",
        ],
        |profile, _| {
            validate_object_array(profile, "channels", CHANNEL_KEYS, validate_channel)?;
            validate_object_array(
                profile,
                "dual_color_map",
                &["primary_hue", "secondary_hue"],
                |_, _| Ok(()),
            )?;
            Ok(())
        },
    )?;
    validate_object_array(
        root,
        "fixtures",
        &[
            "id",
            "name",
            "profile_name",
            "start_channel",
            "position",
            "intensity_scale",
            "pan_min",
            "pan_max",
            "tilt_min",
            "tilt_max",
            "channels",
        ],
        |fixture, _| validate_object_array(fixture, "channels", CHANNEL_KEYS, validate_channel),
    )?;
    Ok(())
}

const CHANNEL_KEYS: &[&str] = &[
    "offset",
    "name",
    "channel_type",
    "default_value",
    "fixed_value",
    "min_value",
    "max_value",
    "capabilities",
    "enabled",
];

fn validate_channel(channel: &Map<String, Value>, _path: &str) -> Result<(), ConfigError> {
    validate_object_array(
        channel,
        "capabilities",
        &[
            "min_value",
            "max_value",
            "name",
            "description",
            "usable",
            "is_off",
            "is_manual",
            "is_auto",
        ],
        |_, _| Ok(()),
    )
}

fn object_at<'a>(value: &'a Value, path: &str) -> Result<&'a Map<String, Value>, ConfigError> {
    value
        .as_object()
        .ok_or_else(|| ConfigError::Invalid(format!("{path} must be a JSON object")))
}

fn reject_unknown_keys(
    object: &Map<String, Value>,
    allowed: &[&str],
    path: &str,
) -> Result<(), ConfigError> {
    if let Some(key) = object.keys().find(|key| !allowed.contains(&key.as_str())) {
        return Err(ConfigError::Invalid(format!(
            "unknown field '{key}' in {path}"
        )));
    }
    Ok(())
}

fn validate_optional_object(
    parent: &Map<String, Value>,
    key: &str,
    allowed: &[&str],
) -> Result<(), ConfigError> {
    let Some(value) = parent.get(key) else {
        return Ok(());
    };
    let object = object_at(value, key)?;
    reject_unknown_keys(object, allowed, key)
}

fn validate_object_array(
    parent: &Map<String, Value>,
    key: &str,
    allowed: &[&str],
    validate_nested: impl Fn(&Map<String, Value>, &str) -> Result<(), ConfigError>,
) -> Result<(), ConfigError> {
    let Some(value) = parent.get(key) else {
        return Ok(());
    };
    let values = value
        .as_array()
        .ok_or_else(|| ConfigError::Invalid(format!("{key} must be a JSON array")))?;
    for (index, value) in values.iter().enumerate() {
        let path = format!("{key}[{index}]");
        let object = object_at(value, &path)?;
        reject_unknown_keys(object, allowed, &path)?;
        validate_nested(object, &path)?;
    }
    Ok(())
}

fn normalize_config(config: &mut ShowConfig, cli_simulate: bool) -> AnyResult<()> {
    if config.name.trim().is_empty() {
        config.name = "My Light Show".into();
    }
    let dmx = config.dmx.get_or_insert_with(Default::default);
    if dmx.universe_size == 0 {
        dmx.universe_size = 512;
    }
    if dmx.fps == 0 {
        dmx.fps = 40;
    }
    if cli_simulate {
        dmx.simulate = true;
    }
    crate::dmx::validate_config(dmx)?;

    let audio = config.audio.get_or_insert_with(Default::default);
    audio.mode = match AudioInputMode::try_from(audio.mode) {
        Ok(AudioInputMode::Unspecified) => AudioInputMode::Auto as i32,
        Ok(mode) => mode as i32,
        Err(_) => bail!("audio input mode {} is invalid", audio.mode),
    };
    if audio.gain == 0.0 {
        audio.gain = 1.0;
    }
    if audio.beatnet_model_path.trim().is_empty() {
        audio.beatnet_model_path = "models/beatnet-plus.pt".into();
    }
    if !(0.1..=5.0).contains(&audio.gain) {
        bail!("audio gain must be between 0.1 and 5.0");
    }
    if cli_simulate {
        audio.simulate = true;
    }

    if config.effects.is_none() {
        config.effects = default_show_config(cli_simulate).effects;
    }
    let effects = config
        .effects
        .as_mut()
        .ok_or_else(|| anyhow::anyhow!("effects configuration is missing"))?;
    effects.mode = normalized_enum(
        effects.mode,
        VisualizationMode::Unspecified as i32,
        VisualizationMode::Energy as i32,
        VisualizationMode::try_from,
        "visualization mode",
    )?;
    effects.movement_mode = normalized_enum(
        effects.movement_mode,
        MovementMode::Unspecified as i32,
        MovementMode::Standard as i32,
        MovementMode::try_from,
        "movement mode",
    )?;
    effects.effect_fixture_mode = normalized_enum(
        effects.effect_fixture_mode,
        EffectFixtureMode::Unspecified as i32,
        EffectFixtureMode::Balanced as i32,
        EffectFixtureMode::try_from,
        "effect fixture mode",
    )?;
    effects.rotation_mode = normalized_enum(
        effects.rotation_mode,
        RotationMode::Unspecified as i32,
        RotationMode::ManualSlow as i32,
        RotationMode::try_from,
        "rotation mode",
    )?;
    effects.strobe_effect_mode = normalized_enum(
        effects.strobe_effect_mode,
        StrobeEffectMode::Unspecified as i32,
        StrobeEffectMode::Auto as i32,
        StrobeEffectMode::try_from,
        "strobe effect mode",
    )?;
    validate_range("effects intensity", effects.intensity, 0.0, 1.0)?;
    validate_range("color speed", effects.color_speed, 0.05, 8.0)?;
    validate_range("beat sensitivity", effects.beat_sensitivity, 0.0, 1.0)?;
    validate_range("smooth factor", effects.smooth_factor, 0.0, 1.0)?;
    validate_range("movement speed", effects.movement_speed, 0.05, 8.0)?;
    validate_range(
        "strobe effect speed",
        effects.strobe_effect_speed,
        0.05,
        8.0,
    )?;

    let mut profiles = default_profiles();
    for mut custom in std::mem::take(&mut config.profiles) {
        custom.channel_count = u32::try_from(custom.channels.len())
            .map_err(|_| anyhow::anyhow!("fixture profile has too many channels"))?;
        if let Some(existing) = profiles
            .iter_mut()
            .find(|profile| profile.name == custom.name)
        {
            *existing = custom;
        } else {
            profiles.push(custom);
        }
    }
    let mut profile_names = HashSet::new();
    for profile in &profiles {
        if profile.name.trim().is_empty() {
            bail!("fixture profile has no name");
        }
        if !profile_names.insert(profile.name.to_lowercase()) {
            bail!("fixture profile '{}' is used more than once", profile.name);
        }
        validate_channels(&profile.name, &profile.channels)?;
        for mapping in &profile.dual_color_map {
            if mapping
                .primary_hue
                .is_some_and(|hue| !hue.is_finite() || !(0.0..=1.0).contains(&hue))
                || mapping
                    .secondary_hue
                    .is_some_and(|hue| !hue.is_finite() || !(0.0..=1.0).contains(&hue))
            {
                bail!(
                    "fixture profile '{}' has an invalid color hue",
                    profile.name
                );
            }
        }
    }
    config.profiles = profiles;
    let profile_channels: HashMap<_, _> = config
        .profiles
        .iter()
        .map(|profile| (profile.name.clone(), profile.channels.clone()))
        .collect();
    let universe_size = config.dmx.as_ref().map_or(512, |dmx| dmx.universe_size);
    let mut fixture_ids = HashSet::new();
    let mut fixture_names = HashSet::new();
    let mut occupied_channels = vec![None::<String>; universe_size as usize + 1];
    for (index, fixture) in config.fixtures.iter_mut().enumerate() {
        if fixture.id.is_empty() {
            fixture.id = stable_fixture_id(&fixture.name, fixture.start_channel, index);
        }
        if !fixture_ids.insert(fixture.id.clone()) {
            bail!("fixture id '{}' is used more than once", fixture.id);
        }
        if fixture.name.trim().is_empty() {
            bail!("fixture {} has no name", index + 1);
        }
        if !fixture_names.insert(fixture.name.trim().to_lowercase()) {
            bail!("fixture name '{}' is used more than once", fixture.name);
        }
        if fixture.start_channel == 0 || fixture.start_channel > universe_size {
            bail!("fixture '{}' has an invalid start channel", fixture.name);
        }
        if !fixture.intensity_scale.is_finite() || !(0.0..=1.0).contains(&fixture.intensity_scale) {
            bail!(
                "fixture '{}' intensity must be between 0 and 1",
                fixture.name
            );
        }
        if fixture.pan_min > fixture.pan_max || fixture.pan_max > 255 {
            bail!("fixture '{}' has invalid pan limits", fixture.name);
        }
        if fixture.tilt_min > fixture.tilt_max || fixture.tilt_max > 255 {
            bail!("fixture '{}' has invalid tilt limits", fixture.name);
        }
        let channels = if fixture.channels.is_empty() {
            profile_channels.get(&fixture.profile_name).ok_or_else(|| {
                anyhow::anyhow!(
                    "fixture '{}' references unknown profile '{}'",
                    fixture.name,
                    fixture.profile_name
                )
            })?
        } else {
            validate_channels(&fixture.name, &fixture.channels)?;
            &fixture.channels
        };
        let last_channel = fixture
            .start_channel
            .saturating_add(
                channels
                    .iter()
                    .map(|channel| channel.offset)
                    .max()
                    .unwrap_or(1),
            )
            .saturating_sub(1);
        if last_channel > universe_size {
            bail!(
                "fixture '{}' extends past DMX universe channel {}",
                fixture.name,
                universe_size
            );
        }
        for channel in channels.iter().filter(|channel| channel.enabled) {
            let absolute = fixture
                .start_channel
                .saturating_add(channel.offset)
                .saturating_sub(1);
            let occupied = &mut occupied_channels[absolute as usize];
            if let Some(previous) = occupied
                && !config.allow_dmx_overlaps
            {
                bail!(
                    "fixture '{}' overlaps fixture '{}' on DMX channel {}; set allow_dmx_overlaps to true only when shared addressing is intentional",
                    fixture.name,
                    previous,
                    absolute
                );
            }
            occupied.get_or_insert_with(|| fixture.name.clone());
        }
    }
    Ok(())
}

fn normalized_enum<T>(
    value: i32,
    unspecified: i32,
    default: i32,
    parse: impl Fn(i32) -> Result<T, prost::UnknownEnumValue>,
    name: &str,
) -> AnyResult<i32> {
    if value == unspecified {
        return Ok(default);
    }
    parse(value)
        .map(|_| value)
        .map_err(|_| anyhow::anyhow!("{name} {value} is invalid"))
}

fn validate_range(name: &str, value: f32, minimum: f32, maximum: f32) -> AnyResult<()> {
    if !value.is_finite() || !(minimum..=maximum).contains(&value) {
        bail!("{name} must be between {minimum} and {maximum}");
    }
    Ok(())
}

fn validate_channels(owner: &str, channels: &[ChannelConfig]) -> AnyResult<()> {
    let mut offsets = HashSet::new();
    for channel in channels {
        if channel.offset == 0 || !offsets.insert(channel.offset) {
            bail!(
                "fixture or profile '{}' has an invalid or duplicate channel offset {}",
                owner,
                channel.offset
            );
        }
        if !SUPPORTED_CHANNEL_TYPES.contains(&channel.channel_type.as_str()) {
            bail!(
                "fixture or profile '{}' channel '{}' has unsupported channel type '{}'",
                owner,
                channel.name,
                channel.channel_type
            );
        }
        if channel.min_value > channel.max_value
            || channel.max_value > 255
            || channel.default_value > 255
            || channel.fixed_value.is_some_and(|value| value > 255)
        {
            bail!(
                "fixture or profile '{}' channel '{}' has invalid DMX values",
                owner,
                channel.name
            );
        }
        for capability in &channel.capabilities {
            if capability.min_value > capability.max_value
                || capability.max_value > 255
                || (capability.usable
                    && (capability.min_value < channel.min_value
                        || capability.max_value > channel.max_value))
            {
                bail!(
                    "fixture or profile '{}' channel '{}' has an invalid capability range",
                    owner,
                    channel.name
                );
            }
        }
    }
    Ok(())
}

fn stable_fixture_id(name: &str, channel: u32, index: usize) -> String {
    let slug: String = name
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() {
                character.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect::<String>()
        .split('-')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("-");
    format!(
        "{}-{}-{}",
        if slug.is_empty() { "fixture" } else { &slug },
        channel,
        index + 1
    )
}

pub fn default_show_config(simulate: bool) -> ShowConfig {
    let profiles = default_profiles();
    let fixtures = vec![
        FixtureConfig {
            id: "wash-left".into(),
            name: "Wash left".into(),
            profile_name: "Generic Dimmer+RGBW".into(),
            start_channel: 1,
            position: 0,
            intensity_scale: 1.0,
            pan_min: 0,
            pan_max: 255,
            tilt_min: 0,
            tilt_max: 255,
            channels: Vec::new(),
        },
        FixtureConfig {
            id: "wash-right".into(),
            name: "Wash right".into(),
            profile_name: "Generic Dimmer+RGBW".into(),
            start_channel: 6,
            position: 1,
            intensity_scale: 1.0,
            pan_min: 0,
            pan_max: 255,
            tilt_min: 0,
            tilt_max: 255,
            channels: Vec::new(),
        },
    ];

    ShowConfig {
        name: "My Light Show".into(),
        dmx: Some(DmxConfig {
            port: String::new(),
            universe_size: 512,
            fps: 40,
            simulate,
        }),
        audio: Some(AudioConfig {
            mode: AudioInputMode::Auto as i32,
            simulate,
            gain: 1.0,
            beatnet_model_path: "models/beatnet-plus.pt".into(),
            device_id: String::new(),
        }),
        effects: Some(EffectsConfig {
            mode: VisualizationMode::Energy as i32,
            intensity: 1.0,
            force_max_brightness: false,
            color_speed: 1.0,
            beat_sensitivity: 0.5,
            smooth_factor: 0.3,
            strobe_on_drop: false,
            movement_enabled: true,
            movement_speed: 0.5,
            movement_mode: MovementMode::Standard as i32,
            effect_fixture_mode: EffectFixtureMode::Balanced as i32,
            rotation_mode: RotationMode::ManualSlow as i32,
            strobe_effect_enabled: true,
            strobe_effect_mode: StrobeEffectMode::Auto as i32,
            strobe_effect_speed: 0.5,
        }),
        profiles,
        fixtures,
        allow_dmx_overlaps: false,
    }
}

pub fn default_profiles() -> Vec<FixtureProfile> {
    vec![
        purelight_muvy_washq(),
        profile(
            "Generic RGB PAR",
            "Generic",
            "RGB PAR",
            "par",
            "standard_rgb",
            vec![
                channel(1, "Red", "intensity_red", 0),
                channel(2, "Green", "intensity_green", 0),
                channel(3, "Blue", "intensity_blue", 0),
            ],
        ),
        profile(
            "Generic RGBW PAR",
            "Generic",
            "RGBW PAR",
            "par",
            "standard_rgbw",
            vec![
                channel(1, "Red", "intensity_red", 0),
                channel(2, "Green", "intensity_green", 0),
                channel(3, "Blue", "intensity_blue", 0),
                channel(4, "White", "intensity_white", 0),
            ],
        ),
        profile(
            "Generic Dimmer+RGBW",
            "Generic",
            "Dimmer+RGBW PAR",
            "par",
            "standard_rgbw",
            vec![
                channel(1, "Dimmer", "intensity_master_dimmer", 255),
                channel(2, "Red", "intensity_red", 0),
                channel(3, "Green", "intensity_green", 0),
                channel(4, "Blue", "intensity_blue", 0),
                channel(5, "White", "intensity_white", 0),
            ],
        ),
        showtec_techno_derby(),
        lixada_dj_projektor(),
    ]
}

fn profile(
    name: &str,
    manufacturer: &str,
    model: &str,
    fixture_type: &str,
    color_mixing: &str,
    channels: Vec<ChannelConfig>,
) -> FixtureProfile {
    FixtureProfile {
        name: name.into(),
        manufacturer: manufacturer.into(),
        model: model.into(),
        fixture_type: fixture_type.into(),
        channel_count: channels.len() as u32,
        channels,
        color_mixing: color_mixing.into(),
        dual_color_map: Vec::new(),
        pan_max_degrees: 540,
        tilt_max_degrees: 270,
    }
}

fn channel(offset: u32, name: &str, channel_type: &str, default_value: u32) -> ChannelConfig {
    ChannelConfig {
        offset,
        name: name.into(),
        channel_type: channel_type.into(),
        default_value,
        fixed_value: None,
        min_value: 0,
        max_value: 255,
        capabilities: Vec::new(),
        enabled: true,
    }
}

fn capability(min_value: u32, max_value: u32, name: &str) -> ChannelCapability {
    ChannelCapability {
        min_value,
        max_value,
        name: name.into(),
        description: String::new(),
        usable: true,
        is_off: false,
        is_manual: false,
        is_auto: false,
    }
}

fn channel_with_capabilities(
    offset: u32,
    name: &str,
    channel_type: &str,
    default_value: u32,
    capabilities: Vec<ChannelCapability>,
) -> ChannelConfig {
    ChannelConfig {
        capabilities,
        ..channel(offset, name, channel_type, default_value)
    }
}

fn purelight_muvy_washq() -> FixtureProfile {
    let mut profile = profile(
        "Purelight Muvy WashQ 14ch",
        "Purelight",
        "Muvy WashQ",
        "moving_head",
        "standard_rgbw",
        vec![
            channel(1, "Pan", "position_pan", 128),
            channel(2, "Pan Fine", "position_pan_fine", 0),
            channel(3, "Tilt", "position_tilt", 128),
            channel(4, "Tilt Fine", "position_tilt_fine", 0),
            channel(5, "XY Speed", "speed_pan_tilt_fast_slow", 0),
            channel_with_capabilities(
                6,
                "Dimmer/Shutter",
                "intensity_master_dimmer",
                255,
                vec![
                    capability(0, 7, "Off"),
                    capability(8, 134, "Master Dimmer"),
                    capability(135, 239, "Strobe (slow to fast)"),
                    capability(240, 255, "Open"),
                ],
            ),
            channel(7, "Red", "intensity_red", 0),
            channel(8, "Green", "intensity_green", 0),
            channel(9, "Blue", "intensity_blue", 0),
            channel(10, "White", "intensity_white", 0),
            channel_with_capabilities(
                11,
                "Color Macro",
                "color_macro",
                0,
                vec![
                    capability(0, 8, "No function (manual RGB)"),
                    capability(9, 20, "RGBW"),
                    capability(21, 34, "Red"),
                    capability(35, 49, "Green"),
                    capability(50, 63, "Blue"),
                    capability(64, 77, "White"),
                    capability(78, 91, "RGB"),
                    capability(92, 105, "RB"),
                    capability(106, 119, "RG"),
                    capability(120, 133, "RGBW"),
                    capability(134, 147, "RG"),
                    capability(148, 161, "RGB"),
                    capability(162, 189, "RGBW"),
                    capability(190, 201, "RBW"),
                    capability(202, 217, "Warm White (RGBW Mix)"),
                    capability(218, 232, "Cool White (RGBW Mix)"),
                    capability(233, 255, "Macro Color (speed via Ch12)"),
                ],
            ),
            channel(12, "Color Speed", "effect_speed", 0),
            channel_with_capabilities(
                13,
                "Macro P/T/M",
                "effect",
                0,
                vec![
                    capability(0, 0, "Manual operation"),
                    capability(1, 255, "Movement patterns and color change"),
                ],
            ),
            channel_with_capabilities(
                14,
                "Reset",
                "maintenance",
                0,
                vec![
                    capability(0, 249, "No function"),
                    capability(250, 255, "Reset (hold 3+ sec)"),
                ],
            ),
        ],
    );
    profile.pan_max_degrees = 545;
    profile.tilt_max_degrees = 184;
    profile
}

fn showtec_techno_derby() -> FixtureProfile {
    profile(
        "Showtec Techno Derby 4ch",
        "Showtec",
        "Techno Derby",
        "effect",
        "color_macro",
        vec![
            channel_with_capabilities(
                1,
                "Color",
                "color_macro",
                0,
                vec![
                    capability(0, 5, "No function"),
                    capability(6, 20, "Red"),
                    capability(21, 35, "Green"),
                    capability(36, 50, "Blue"),
                    capability(51, 65, "White"),
                    capability(66, 80, "Red + Green"),
                    capability(81, 95, "Red + Blue"),
                    capability(96, 110, "Red + White"),
                    capability(111, 125, "Green + Blue"),
                    capability(126, 140, "Green + White"),
                    capability(141, 155, "Blue + White"),
                    capability(156, 170, "Red + Green + Blue"),
                    capability(171, 185, "Red + Green + White"),
                    capability(186, 200, "Green + Blue + White"),
                    capability(201, 215, "Red + Green + Blue + White"),
                    capability(216, 229, "Color change slow"),
                    capability(230, 255, "Color change fast"),
                ],
            ),
            channel_with_capabilities(
                2,
                "Strobe",
                "shutter_strobe_slow_fast",
                0,
                vec![
                    capability(0, 5, "Strobe off"),
                    capability(6, 255, "Strobe slow to fast"),
                ],
            ),
            {
                let mut manual = capability(1, 127, "Manual rotation position");
                manual.is_manual = true;
                let mut off = capability(0, 0, "No function");
                off.is_off = true;
                let mut automatic = capability(128, 255, "Auto rotation slow to fast");
                automatic.usable = false;
                automatic.is_auto = true;
                let mut result = channel_with_capabilities(
                    3,
                    "Pattern Rotation",
                    "effect",
                    0,
                    vec![off, manual, automatic],
                );
                result.max_value = 127;
                result
            },
            channel_with_capabilities(4, "LED Array Patterns", "effect_pattern", 0, {
                let mut off = capability(0, 9, "No function");
                off.is_off = true;
                let mut ranges = vec![off];
                for pattern in 1..=17 {
                    let start = pattern * 10;
                    ranges.push(capability(start, start + 9, &format!("Pattern {pattern}")));
                }
                ranges.push(capability(180, 255, "Pattern 18 (all on)"));
                ranges
            }),
        ],
    )
}

fn lixada_dj_projektor() -> FixtureProfile {
    let mut profile = profile(
        "Lixada DJ Projektor 7ch",
        "Lixada",
        "DJ Projektor",
        "effect",
        "dual_color_channels",
        vec![
            channel_with_capabilities(
                1,
                "Master Dimmer",
                "intensity_master_dimmer",
                255,
                vec![capability(0, 0, "Off"), capability(1, 255, "Dimmer 0-100%")],
            ),
            channel_with_capabilities(
                2,
                "Red/Yellow",
                "intensity_red",
                0,
                vec![
                    capability(0, 0, "Off"),
                    capability(1, 255, "Red/Yellow intensity"),
                ],
            ),
            channel_with_capabilities(
                3,
                "Green/Violet",
                "intensity_green",
                0,
                vec![
                    capability(0, 0, "Off"),
                    capability(1, 255, "Green/Violet intensity"),
                ],
            ),
            channel_with_capabilities(
                4,
                "Blue/White",
                "intensity_blue",
                0,
                vec![
                    capability(0, 0, "Off"),
                    capability(1, 255, "Blue/White intensity"),
                ],
            ),
            channel_with_capabilities(
                5,
                "Strobe",
                "shutter_strobe_slow_fast",
                0,
                vec![
                    capability(0, 0, "Strobe off"),
                    capability(1, 255, "Strobe slow to fast"),
                ],
            ),
            {
                let mut manual = capability(0, 135, "Manual position");
                manual.is_manual = true;
                let mut automatic = capability(136, 255, "Auto motion");
                automatic.usable = false;
                automatic.is_auto = true;
                channel_with_capabilities(6, "Motor Position", "effect", 0, vec![manual, automatic])
            },
            channel_with_capabilities(
                7,
                "Color Presets",
                "color_macro",
                0,
                vec![
                    capability(0, 0, "Manual control (no preset)"),
                    capability(1, 255, "Color presets"),
                ],
            ),
        ],
    );
    profile.dual_color_map = vec![
        DualColorMapping {
            primary_hue: Some(0.0),
            secondary_hue: Some(0.12),
        },
        DualColorMapping {
            primary_hue: Some(0.33),
            secondary_hue: Some(0.83),
        },
        DualColorMapping {
            primary_hue: Some(0.67),
            secondary_hue: None,
        },
    ];
    profile
}

fn migrate_legacy_config(mut value: Value, simulate: bool) -> Value {
    let Value::Object(root) = &mut value else {
        return value;
    };
    root.entry("name")
        .or_insert(Value::String("My Light Show".into()));

    let audio_gain = root
        .get("effects")
        .and_then(Value::as_object)
        .and_then(|effects| effects.get("audio_gain"))
        .cloned();

    let audio = root
        .entry("audio")
        .or_insert_with(|| Value::Object(Map::new()));
    if let Value::Object(audio) = audio {
        if !audio.contains_key("mode") {
            let legacy_mode = audio
                .remove("fallback_mode")
                .and_then(|mode| mode.as_str().map(str::to_owned))
                .unwrap_or_else(|| "auto".into());
            let mode = if legacy_mode == "loopback" {
                "system_audio"
            } else {
                legacy_mode.as_str()
            };
            audio.insert("mode".into(), Value::String(mode.into()));
        }
        normalize_audio_mode(audio);
        audio.remove("pipewire_source_name");
        audio.remove("device_name");
        audio
            .entry("device_id")
            .or_insert(Value::String(String::new()));
        audio.entry("simulate").or_insert(Value::Bool(simulate));
        audio
            .entry("gain")
            .or_insert(audio_gain.unwrap_or(Value::from(1.0)));
        audio
            .entry("beatnet_model_path")
            .or_insert(Value::String("models/beatnet-plus.pt".into()));
    }

    let dmx = root
        .entry("dmx")
        .or_insert_with(|| Value::Object(Map::new()));
    if let Value::Object(dmx) = dmx {
        dmx.entry("port").or_insert(Value::String(String::new()));
        dmx.entry("universe_size").or_insert(Value::from(512));
        dmx.entry("fps").or_insert(Value::from(40));
        dmx.entry("simulate").or_insert(Value::Bool(simulate));
    }

    let effects = root
        .entry("effects")
        .or_insert_with(|| Value::Object(Map::new()));
    if let Value::Object(effects) = effects {
        effects.remove("audio_gain");
        effects
            .entry("mode")
            .or_insert(Value::String("energy".into()));
        effects.entry("intensity").or_insert(Value::from(1.0));
        effects
            .entry("force_max_brightness")
            .or_insert(Value::Bool(false));
        effects.entry("color_speed").or_insert(Value::from(1.0));
        effects
            .entry("beat_sensitivity")
            .or_insert(Value::from(0.5));
        effects.entry("smooth_factor").or_insert(Value::from(0.3));
        effects
            .entry("strobe_on_drop")
            .or_insert(Value::Bool(false));
        effects
            .entry("movement_enabled")
            .or_insert(Value::Bool(true));
        effects.entry("movement_speed").or_insert(Value::from(0.5));
        effects
            .entry("movement_mode")
            .or_insert(Value::String("standard".into()));
        effects
            .entry("effect_fixture_mode")
            .or_insert(Value::String("balanced".into()));
        effects
            .entry("rotation_mode")
            .or_insert(Value::String("manual_slow".into()));
        effects
            .entry("strobe_effect_enabled")
            .or_insert(Value::Bool(true));
        effects
            .entry("strobe_effect_mode")
            .or_insert(Value::String("auto".into()));
        effects
            .entry("strobe_effect_speed")
            .or_insert(Value::from(0.5));
        if effects.get("movement_mode") == Some(&Value::String("figure_8".into())) {
            effects.insert("movement_mode".into(), Value::String("figure8".into()));
        }
        normalize_effect_enums(effects);
    }

    if !root.contains_key("profiles") {
        root.insert(
            "profiles".into(),
            serde_json::to_value(default_profiles()).unwrap_or_default(),
        );
    }
    let fixtures = root
        .entry("fixtures")
        .or_insert_with(|| Value::Array(Vec::new()));
    if let Value::Array(fixtures) = fixtures {
        for (position, fixture) in fixtures.iter_mut().enumerate() {
            let Value::Object(fixture) = fixture else {
                continue;
            };
            let start_channel = fixture
                .get("start_channel")
                .and_then(Value::as_u64)
                .unwrap_or(1);
            fixture
                .entry("id")
                .or_insert_with(|| Value::String(String::new()));
            fixture
                .entry("profile_name")
                .or_insert_with(|| Value::String(String::new()));
            fixture
                .entry("position")
                .or_insert_with(|| Value::from(position));
            fixture
                .entry("intensity_scale")
                .or_insert_with(|| Value::from(1.0));
            fixture.entry("pan_min").or_insert_with(|| Value::from(0));
            fixture.entry("pan_max").or_insert_with(|| Value::from(255));
            fixture.entry("tilt_min").or_insert_with(|| Value::from(0));
            fixture
                .entry("tilt_max")
                .or_insert_with(|| Value::from(255));
            let channels = fixture
                .entry("channels")
                .or_insert_with(|| Value::Array(Vec::new()));
            if let Value::Array(channels) = channels {
                for channel in channels {
                    migrate_legacy_channel(channel, start_channel);
                }
            }
        }
    }

    value
}

fn normalize_audio_mode(audio: &mut Map<String, Value>) {
    let Some(Value::String(mode)) = audio.get("mode") else {
        return;
    };
    let value = match mode.as_str() {
        "auto" => AudioInputMode::Auto,
        "loopback" | "system_audio" => AudioInputMode::SystemAudio,
        "manual_device" => AudioInputMode::ManualDevice,
        "pipewire_sink" => AudioInputMode::PipewireSink,
        "microphone" => AudioInputMode::Microphone,
        _ => AudioInputMode::Unspecified,
    };
    audio.insert("mode".into(), Value::from(value as i32));
}

fn normalize_effect_enums(effects: &mut Map<String, Value>) {
    normalize_enum(effects, "mode", |value| match value {
        "energy" => VisualizationMode::Energy as i32,
        "frequency_split" => VisualizationMode::FrequencySplit as i32,
        "beat_pulse" => VisualizationMode::BeatPulse as i32,
        "color_cycle" => VisualizationMode::ColorCycle as i32,
        "rainbow_wave" => VisualizationMode::RainbowWave as i32,
        "strobe_beat" => VisualizationMode::StrobeBeat as i32,
        "random_flash" => VisualizationMode::RandomFlash as i32,
        _ => VisualizationMode::Unspecified as i32,
    });
    normalize_enum(effects, "movement_mode", |value| match value {
        "subtle" => MovementMode::Subtle as i32,
        "standard" => MovementMode::Standard as i32,
        "dramatic" => MovementMode::Dramatic as i32,
        "wall_wash" => MovementMode::WallWash as i32,
        "sweep" => MovementMode::Sweep as i32,
        "random" => MovementMode::Random as i32,
        "circle" => MovementMode::Circle as i32,
        "figure8" | "figure_8" => MovementMode::Figure8 as i32,
        "ballyhoo" => MovementMode::Ballyhoo as i32,
        "fan" => MovementMode::Fan as i32,
        "chase" => MovementMode::Chase as i32,
        "strobe_position" => MovementMode::StrobePosition as i32,
        "crazy" => MovementMode::Crazy as i32,
        _ => MovementMode::Unspecified as i32,
    });
    normalize_enum(effects, "effect_fixture_mode", |value| match value {
        "balanced" => EffectFixtureMode::Balanced as i32,
        "strobe_focus" => EffectFixtureMode::StrobeFocus as i32,
        "movement_focus" => EffectFixtureMode::MovementFocus as i32,
        "strobe_only" => EffectFixtureMode::StrobeOnly as i32,
        "movement_only" => EffectFixtureMode::MovementOnly as i32,
        _ => EffectFixtureMode::Unspecified as i32,
    });
    normalize_enum(effects, "rotation_mode", |value| match value {
        "off" => RotationMode::Off as i32,
        "manual_slow" => RotationMode::ManualSlow as i32,
        "manual_beat" => RotationMode::ManualBeat as i32,
        "auto_slow" => RotationMode::AutoSlow as i32,
        "auto_medium" => RotationMode::AutoMedium as i32,
        "auto_fast" => RotationMode::AutoFast as i32,
        "auto_music" => RotationMode::AutoMusic as i32,
        _ => RotationMode::Unspecified as i32,
    });
    normalize_enum(effects, "strobe_effect_mode", |value| match value {
        "off" => StrobeEffectMode::Off as i32,
        "auto" => StrobeEffectMode::Auto as i32,
        "effect_1" => StrobeEffectMode::Effect1 as i32,
        "effect_2" => StrobeEffectMode::Effect2 as i32,
        "effect_3" => StrobeEffectMode::Effect3 as i32,
        "effect_4" => StrobeEffectMode::Effect4 as i32,
        "effect_5" => StrobeEffectMode::Effect5 as i32,
        "effect_6" => StrobeEffectMode::Effect6 as i32,
        "effect_7" => StrobeEffectMode::Effect7 as i32,
        "effect_8" => StrobeEffectMode::Effect8 as i32,
        "effect_9" => StrobeEffectMode::Effect9 as i32,
        "effect_10" => StrobeEffectMode::Effect10 as i32,
        "effect_11" => StrobeEffectMode::Effect11 as i32,
        "effect_12" => StrobeEffectMode::Effect12 as i32,
        "effect_13" => StrobeEffectMode::Effect13 as i32,
        "effect_14" => StrobeEffectMode::Effect14 as i32,
        "effect_15" => StrobeEffectMode::Effect15 as i32,
        "effect_16" => StrobeEffectMode::Effect16 as i32,
        "effect_17" => StrobeEffectMode::Effect17 as i32,
        "effect_18_strobe" => StrobeEffectMode::Effect18Strobe as i32,
        _ => StrobeEffectMode::Unspecified as i32,
    });
}

fn normalize_enum(object: &mut Map<String, Value>, key: &str, parse: impl FnOnce(&str) -> i32) {
    let Some(Value::String(value)) = object.get(key) else {
        return;
    };
    object.insert(key.into(), Value::from(parse(value)));
}

fn migrate_legacy_channel(value: &mut Value, start_channel: u64) {
    let Value::Object(channel) = value else {
        return;
    };
    if !channel.contains_key("offset") {
        let absolute = channel
            .remove("channel")
            .and_then(|value| value.as_u64())
            .unwrap_or(start_channel);
        channel.insert(
            "offset".into(),
            Value::from(absolute.saturating_sub(start_channel) + 1),
        );
    }
    if let Some(Value::String(channel_type)) = channel.get_mut("channel_type") {
        *channel_type = match channel_type.as_str() {
            "red" => "intensity_red",
            "green" => "intensity_green",
            "blue" => "intensity_blue",
            "white" => "intensity_white",
            "amber" => "intensity_amber",
            "uv" => "intensity_uv",
            "cyan" => "intensity_cyan",
            "magenta" => "intensity_magenta",
            "yellow" => "intensity_yellow",
            "dimmer" => "intensity_master_dimmer",
            "pan" => "position_pan",
            "pan_fine" => "position_pan_fine",
            "tilt" => "position_tilt",
            "tilt_fine" => "position_tilt_fine",
            "speed" => "speed_pan_tilt_fast_slow",
            "strobe" => "shutter_strobe_slow_fast",
            "gobo" => "gobo_wheel",
            "none" => "nothing",
            current => current,
        }
        .into();
    }
    channel
        .entry("name")
        .or_insert_with(|| Value::String(String::new()));
    channel
        .entry("channel_type")
        .or_insert_with(|| Value::String("nothing".into()));
    channel
        .entry("default_value")
        .or_insert_with(|| Value::from(0));
    channel.entry("min_value").or_insert_with(|| Value::from(0));
    channel
        .entry("max_value")
        .or_insert_with(|| Value::from(255));
    channel
        .entry("capabilities")
        .or_insert_with(|| Value::Array(Vec::new()));
    channel
        .entry("enabled")
        .or_insert_with(|| Value::Bool(true));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn migrates_legacy_audio_gain_and_mode() {
        let legacy = serde_json::json!({
            "name": "Legacy",
            "dmx": { "port": "", "universe_size": 512, "fps": 40 },
            "audio": { "device_name": "", "fallback_mode": "loopback" },
            "effects": { "audio_gain": 1.75, "movement_mode": "figure_8" },
            "fixtures": []
        });
        let migrated = migrate_legacy_config(legacy, false);
        assert_eq!(migrated["audio"]["gain"], 1.75);
        assert_eq!(
            migrated["audio"]["mode"],
            AudioInputMode::SystemAudio as i32
        );
        assert_eq!(migrated["audio"]["device_id"], "");
        assert!(migrated["audio"].get("device_name").is_none());
        assert_eq!(
            migrated["effects"]["movement_mode"],
            MovementMode::Figure8 as i32
        );
    }

    #[test]
    fn built_in_profiles_match_the_legacy_fixture_library() {
        let profiles = default_profiles();
        let names: Vec<_> = profiles
            .iter()
            .map(|profile| profile.name.as_str())
            .collect();
        assert_eq!(
            names,
            vec![
                "Purelight Muvy WashQ 14ch",
                "Generic RGB PAR",
                "Generic RGBW PAR",
                "Generic Dimmer+RGBW",
                "Showtec Techno Derby 4ch",
                "Lixada DJ Projektor 7ch",
            ]
        );
        assert_eq!(profiles[0].channels.len(), 14);
        assert_eq!(profiles[4].channels[2].max_value, 127);
        assert_eq!(profiles[5].dual_color_map.len(), 3);
    }

    #[test]
    fn example_configuration_matches_the_rust_schema() {
        let config = parse_json(include_str!("../example_config.json"), false)
            .expect("example configuration should parse");
        assert_eq!(config.name, "Example Light Show");
        assert_eq!(config.fixtures.len(), 2);
        assert_eq!(config.effects().mode(), VisualizationMode::RainbowWave);
    }

    #[test]
    fn rejects_unknown_configuration_fields() {
        let mut value = serde_json::to_value(default_show_config(true))
            .expect("default configuration should serialize");
        value
            .as_object_mut()
            .expect("configuration should be an object")
            .insert("intenisty".into(), Value::from(1));

        let error = parse_json(
            &serde_json::to_string(&value).expect("configuration should serialize"),
            true,
        )
        .expect_err("unknown field should be rejected");

        assert!(error.to_string().contains("unknown field 'intenisty'"));
    }

    #[test]
    fn rejects_oversized_configuration_input() {
        let input = " ".repeat(MAX_CONFIG_BYTES + 1);
        let error = parse_json(&input, true).expect_err("oversized input should be rejected");

        assert!(error.to_string().contains("exceeds"));
    }

    #[test]
    fn rejects_unsupported_fixture_channel_types() {
        let mut config = default_show_config(true);
        config.profiles[0].channels[0].channel_type = "typo_dimmer".into();

        let error = ValidatedShowConfig::new(config, true)
            .expect_err("unknown channel behavior should be rejected");

        assert!(error.to_string().contains("unsupported channel type"));
    }

    #[test]
    fn rejects_fixture_channel_overlap_without_explicit_opt_in() {
        let mut config = default_show_config(true);
        config.fixtures[1].start_channel = 5;

        let error = ValidatedShowConfig::new(config.clone(), true)
            .expect_err("overlapping fixtures should be rejected");
        assert!(error.to_string().contains("overlaps"));

        config.allow_dmx_overlaps = true;
        ValidatedShowConfig::new(config, true)
            .expect("explicit shared addressing should remain available");
    }

    #[test]
    fn save_atomically_replaces_an_existing_configuration() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let path = directory.path().join("config.json");
        fs::write(&path, "truncated")
            .expect("existing configuration placeholder should be written");
        let mut raw = default_show_config(true);
        raw.name = "Atomic replacement".into();
        let config =
            ValidatedShowConfig::new(raw, true).expect("replacement configuration should validate");

        save(&path, &config).expect("configuration should be replaced");

        let loaded = load(&path, true).expect("replacement configuration should load");
        assert_eq!(loaded.name, "Atomic replacement");
    }

    #[test]
    fn validation_preserves_explicit_zero_fixture_controls() {
        let mut config = default_show_config(true);
        config.fixtures[0].intensity_scale = 0.0;
        config.fixtures[0].pan_min = 0;
        config.fixtures[0].pan_max = 0;
        config.fixtures[0].tilt_min = 0;
        config.fixtures[0].tilt_max = 0;

        let config = ValidatedShowConfig::new(config, true)
            .expect("configuration with zero fixture controls should validate");
        let fixture = &config.fixtures[0];
        assert_eq!(fixture.intensity_scale, 0.0);
        assert_eq!(fixture.pan_max, 0);
        assert_eq!(fixture.tilt_max, 0);
    }

    #[test]
    fn validation_rejects_duplicate_fixture_names() {
        let mut config = default_show_config(true);
        config.fixtures[1].name = config.fixtures[0].name.to_uppercase();
        assert!(ValidatedShowConfig::new(config, true).is_err());
    }

    #[test]
    fn migrates_absolute_legacy_channel_numbers() {
        let legacy = serde_json::json!({
            "fixtures": [{
                "name": "Legacy PAR",
                "start_channel": 5,
                "intensity_scale": 0.0,
                "pan_max": 0,
                "tilt_max": 0,
                "channels": [
                    { "channel": 5, "channel_type": "red" },
                    { "channel": 8, "channel_type": "dimmer" }
                ]
            }]
        });
        let config: ShowConfig = serde_json::from_value(migrate_legacy_config(legacy, false))
            .expect("legacy configuration should migrate");
        let fixture = &config.fixtures[0];
        assert_eq!(fixture.intensity_scale, 0.0);
        assert_eq!(fixture.pan_max, 0);
        assert_eq!(fixture.channels[0].offset, 1);
        assert_eq!(fixture.channels[0].channel_type, "intensity_red");
        assert_eq!(fixture.channels[1].offset, 4);
    }
}
