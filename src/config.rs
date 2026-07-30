use std::{
    collections::{HashMap, HashSet},
    fs::{self, File},
    io::{Read, Write},
    ops::Deref,
    path::{Path, PathBuf},
};

use anyhow::{Result as AnyResult, bail};
use tempfile::NamedTempFile;
use thiserror::Error;

use crate::{
    grandma2::{
        GrandMa2Library, LIXADA_MINI_BUTTERFLY_ID, PURELIGHT_MUVY_WASHQ_ID, SHOWTEC_TECHNO_DERBY_ID,
    },
    proto::v1::{
        AudioConfig, AudioInputMode, DmxConfig, EffectFixtureMode, EffectsConfig, FixtureConfig,
        FixtureStagePlacement, MovementMode, RotationMode, ShowConfig, StrobeEffectMode,
        VisualizationMode,
    },
};

const MAX_CONFIG_BYTES: usize = 16 * 1024 * 1024;

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
    grandma2: GrandMa2Library,
}

impl ValidatedShowConfig {
    pub fn new(mut config: ShowConfig, simulate: bool) -> Result<Self, ConfigError> {
        let grandma2 = normalize_config(&mut config, simulate)
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
            grandma2,
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

    pub fn grandma2(&self) -> &GrandMa2Library {
        &self.grandma2
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
    let config = serde_json::from_str(contents).map_err(|error| {
        if error.classify() == serde_json::error::Category::Data {
            ConfigError::InvalidSchema(error)
        } else {
            ConfigError::InvalidJson(error)
        }
    })?;
    ValidatedShowConfig::new(config, simulate)
}

pub fn to_json(config: &ValidatedShowConfig) -> Result<String, ConfigError> {
    serde_json::to_string_pretty(config.as_proto()).map_err(ConfigError::Serialization)
}

pub fn save(path: &Path, config: &ValidatedShowConfig) -> Result<(), ConfigError> {
    let parent = configuration_directory(path);
    fs::create_dir_all(parent).map_err(|source| ConfigError::Io {
        operation: "create configuration directory for",
        path: path.to_owned(),
        source,
    })?;
    let mut temporary = NamedTempFile::new_in(parent).map_err(|source| ConfigError::Io {
        operation: "create temporary configuration beside",
        path: path.to_owned(),
        source,
    })?;
    let json = to_json(config)?;
    temporary
        .write_all(json.as_bytes())
        .and_then(|()| temporary.write_all(b"\n"))
        .and_then(|()| temporary.as_file_mut().sync_all())
        .map_err(|source| ConfigError::Io {
            operation: "write temporary configuration for",
            path: path.to_owned(),
            source,
        })?;
    temporary.persist(path).map_err(|error| ConfigError::Io {
        operation: "replace configuration at",
        path: path.to_owned(),
        source: error.error,
    })?;
    sync_directory(parent)?;
    Ok(())
}

fn configuration_directory(path: &Path) -> &Path {
    path.parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."))
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), ConfigError> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(|source| ConfigError::Io {
            operation: "sync configuration directory",
            path: path.to_owned(),
            source,
        })
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), ConfigError> {
    Ok(())
}

fn normalize_config(config: &mut ShowConfig, cli_simulate: bool) -> AnyResult<GrandMa2Library> {
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
    validate_range("audio gain", audio.gain, 0.1, 5.0)?;
    if cli_simulate {
        audio.simulate = true;
    }

    if config.effects.is_none() {
        config.effects = default_effects();
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
    validate_range("color speed", effects.color_speed, 0.1, 10.0)?;
    validate_increment("color speed", effects.color_speed, 0.1)?;
    validate_range("beat sensitivity", effects.beat_sensitivity, 0.0, 1.0)?;
    validate_range("smooth factor", effects.smooth_factor, 0.0, 1.0)?;
    validate_range("movement speed", effects.movement_speed, 0.0, 1.0)?;
    validate_range("strobe effect speed", effects.strobe_effect_speed, 0.0, 1.0)?;

    validate_imported_files(config)?;
    let library = GrandMa2Library::load(&config.imported_fixture_files)?;
    validate_fixture_patch(config, &library)?;
    Ok(library)
}

fn validate_imported_files(config: &ShowConfig) -> AnyResult<()> {
    let mut ids = HashSet::new();
    let mut total_xml_bytes = 0_usize;
    for file in &config.imported_fixture_files {
        total_xml_bytes = total_xml_bytes.saturating_add(file.xml.len());
        if total_xml_bytes > MAX_CONFIG_BYTES {
            bail!(
                "imported grandMA2 fixture XML exceeds the combined {MAX_CONFIG_BYTES}-byte limit"
            );
        }
        if !file.id.starts_with("ma2:")
            || file.id.len() < 8
            || !file.id.chars().all(|character| {
                character.is_ascii_lowercase() || character.is_ascii_digit() || character == ':'
            })
        {
            bail!(
                "imported grandMA2 fixture file has invalid id '{}'",
                file.id
            );
        }
        if !ids.insert(file.id.as_str()) {
            bail!(
                "grandMA2 fixture file id '{}' is used more than once",
                file.id
            );
        }
        if file.filename.trim().is_empty()
            || file.filename.len() > 200
            || !file.filename.to_ascii_lowercase().ends_with(".xml")
            || file.filename.contains(['/', '\\'])
        {
            bail!(
                "imported grandMA2 fixture file '{}' has an invalid filename",
                file.id
            );
        }
        if file.xml.trim().is_empty() {
            bail!(
                "imported grandMA2 fixture file '{}' is empty",
                file.filename
            );
        }
    }
    Ok(())
}

fn validate_fixture_patch(config: &mut ShowConfig, library: &GrandMa2Library) -> AnyResult<()> {
    let universe_size = config.dmx.as_ref().map_or(512, |dmx| dmx.universe_size);
    let fixture_count = config.fixtures.len();
    let mut fixture_ids = HashSet::new();
    let mut fixture_names = HashSet::new();
    let mut occupied_channels = HashMap::<u32, String>::new();

    for (index, fixture) in config.fixtures.iter_mut().enumerate() {
        if fixture.stage_placement.is_none() {
            fixture.stage_placement = Some(default_stage_placement(index, fixture_count));
        }
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
        let fixture_type = library.get(&fixture.fixture_type_id).ok_or_else(|| {
            anyhow::anyhow!(
                "fixture '{}' references unknown grandMA2 fixture type '{}'",
                fixture.name,
                fixture.fixture_type_id
            )
        })?;
        if fixture.start_channel == 0 || fixture.start_channel > universe_size {
            bail!("fixture '{}' has an invalid start channel", fixture.name);
        }
        validate_range(
            &format!("fixture '{}' intensity", fixture.name),
            fixture.intensity_scale,
            0.0,
            1.0,
        )?;
        validate_movement_range(
            &fixture.name,
            "pan",
            fixture.movement_pan_min,
            fixture.movement_pan_max,
        )?;
        validate_movement_range(
            &fixture.name,
            "tilt",
            fixture.movement_tilt_min,
            fixture.movement_tilt_max,
        )?;
        let stage_placement = fixture.stage_placement.as_ref().ok_or_else(|| {
            anyhow::anyhow!(
                "fixture '{}' is missing its normalized stage placement",
                fixture.name
            )
        })?;
        validate_stage_placement(&fixture.name, stage_placement)?;

        let last_channel = fixture
            .start_channel
            .saturating_add(fixture_type.footprint)
            .saturating_sub(1);
        if last_channel > universe_size {
            bail!(
                "fixture '{}' extends past DMX universe channel {}",
                fixture.name,
                universe_size
            );
        }
        for offset in 0..fixture_type.footprint {
            let absolute = fixture.start_channel + offset;
            if let Some(previous) = occupied_channels.get(&absolute)
                && !config.allow_dmx_overlaps
            {
                bail!(
                    "fixture '{}' overlaps fixture '{}' on DMX channel {}; set allow_dmx_overlaps to true only when shared addressing is intentional",
                    fixture.name,
                    previous,
                    absolute
                );
            }
            occupied_channels
                .entry(absolute)
                .or_insert_with(|| fixture.name.clone());
        }
    }
    Ok(())
}

fn default_stage_placement(index: usize, fixture_count: usize) -> FixtureStagePlacement {
    let x_m = if fixture_count <= 1 {
        0.0
    } else {
        -3.0 + index as f32 / (fixture_count - 1) as f32 * 6.0
    };
    FixtureStagePlacement {
        x_m,
        y_m: 3.35,
        z_m: 0.0,
        rotation_x_degrees: 0.0,
        rotation_y_degrees: 0.0,
        rotation_z_degrees: 0.0,
        focus_target_enabled: true,
        focus_target_x_m: x_m,
        focus_target_y_m: 0.0,
        focus_target_z_m: 4.2,
    }
}

fn validate_stage_placement(name: &str, placement: &FixtureStagePlacement) -> AnyResult<()> {
    for (axis, value, minimum, maximum) in [
        ("stage X", placement.x_m, -100.0, 100.0),
        ("stage Y", placement.y_m, -10.0, 100.0),
        ("stage Z", placement.z_m, -100.0, 100.0),
        (
            "mount rotation X",
            placement.rotation_x_degrees,
            -360.0,
            360.0,
        ),
        (
            "mount rotation Y",
            placement.rotation_y_degrees,
            -360.0,
            360.0,
        ),
        (
            "mount rotation Z",
            placement.rotation_z_degrees,
            -360.0,
            360.0,
        ),
        ("focus target X", placement.focus_target_x_m, -100.0, 100.0),
        ("focus target Y", placement.focus_target_y_m, -10.0, 100.0),
        ("focus target Z", placement.focus_target_z_m, -100.0, 100.0),
    ] {
        validate_range(&format!("fixture '{name}' {axis}"), value, minimum, maximum)?;
    }
    Ok(())
}

fn validate_movement_range(name: &str, axis: &str, minimum: f32, maximum: f32) -> AnyResult<()> {
    if !minimum.is_finite()
        || !maximum.is_finite()
        || minimum < 0.0
        || maximum > 1.0
        || minimum > maximum
    {
        bail!("fixture '{name}' {axis} movement limits must be ordered values between 0 and 1");
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

fn validate_increment(name: &str, value: f32, increment: f32) -> AnyResult<()> {
    let steps = value / increment;
    if !steps.is_finite() || (steps - steps.round()).abs() > 0.0001 {
        bail!("{name} must use increments of {increment}");
    }
    Ok(())
}

fn stable_fixture_id(name: &str, channel: u32, index: usize) -> String {
    let slug = name
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

fn default_effects() -> Option<EffectsConfig> {
    Some(EffectsConfig {
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
        harmony_palette_enabled: false,
    })
}

pub fn default_show_config(simulate: bool) -> ShowConfig {
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
        effects: default_effects(),
        imported_fixture_files: Vec::new(),
        fixtures: vec![
            FixtureConfig {
                id: "techno-derby".into(),
                name: "Techno Derby".into(),
                fixture_type_id: SHOWTEC_TECHNO_DERBY_ID.into(),
                start_channel: 1,
                position: 0,
                intensity_scale: 1.0,
                movement_pan_min: 0.0,
                movement_pan_max: 1.0,
                movement_tilt_min: 0.0,
                movement_tilt_max: 1.0,
                stage_placement: Some(default_stage_placement(0, 3)),
            },
            FixtureConfig {
                id: "muvy-washq".into(),
                name: "MUVY WashQ".into(),
                fixture_type_id: PURELIGHT_MUVY_WASHQ_ID.into(),
                start_channel: 5,
                position: 1,
                intensity_scale: 1.0,
                movement_pan_min: 0.0,
                movement_pan_max: 1.0,
                movement_tilt_min: 0.0,
                movement_tilt_max: 1.0,
                stage_placement: Some(default_stage_placement(1, 3)),
            },
            FixtureConfig {
                id: "mini-butterfly".into(),
                name: "Mini Butterfly".into(),
                fixture_type_id: LIXADA_MINI_BUTTERFLY_ID.into(),
                start_channel: 19,
                position: 2,
                intensity_scale: 1.0,
                movement_pan_min: 0.0,
                movement_pan_max: 1.0,
                movement_tilt_min: 0.0,
                movement_tilt_max: 1.0,
                stage_placement: Some(default_stage_placement(2, 3)),
            },
        ],
        allow_dmx_overlaps: false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bare_config_filename_uses_current_directory() {
        assert_eq!(
            configuration_directory(Path::new("config.json")),
            Path::new(".")
        );
    }

    #[test]
    fn defaults_use_only_bundled_grandma2_fixture_types() {
        let config =
            ValidatedShowConfig::new(default_show_config(true), true).expect("defaults validate");
        assert_eq!(config.grandma2().fixture_types().len(), 3);
        assert_eq!(config.fixtures.len(), 3);
        assert!(config.imported_fixture_files.is_empty());
        assert!(
            config
                .fixtures
                .iter()
                .all(|fixture| config.grandma2().get(&fixture.fixture_type_id).is_some())
        );
    }

    #[test]
    fn migrates_missing_stage_placements_to_explicit_coordinates() {
        let mut config = default_show_config(true);
        for fixture in &mut config.fixtures {
            fixture.stage_placement = None;
        }

        let validated = ValidatedShowConfig::new(config, true)
            .expect("legacy fixture positions should migrate");
        let placements = validated
            .fixtures
            .iter()
            .map(|fixture| {
                fixture
                    .stage_placement
                    .as_ref()
                    .expect("migration should assign a placement")
            })
            .collect::<Vec<_>>();

        assert_eq!(placements.len(), 3);
        assert_eq!(placements[0].x_m, -3.0);
        assert_eq!(placements[1].x_m, 0.0);
        assert_eq!(placements[2].x_m, 3.0);
        assert!(placements.iter().all(|placement| {
            placement.focus_target_enabled
                && placement.y_m == 3.35
                && placement.focus_target_y_m == 0.0
                && placement.focus_target_z_m == 4.2
        }));
    }

    #[test]
    fn rejects_legacy_fixture_profile_fields() {
        let json = r#"{
          "name": "Legacy",
          "profiles": [],
          "fixtures": []
        }"#;
        assert!(matches!(
            parse_json(json, true),
            Err(ConfigError::InvalidSchema(_))
        ));
    }

    #[test]
    fn rejects_fixture_overlap_using_parsed_footprints() {
        let mut config = default_show_config(true);
        config.fixtures[1].start_channel = 4;
        let error = ValidatedShowConfig::new(config, true).expect_err("overlap must fail");
        assert!(error.to_string().contains("overlaps"));
    }

    #[test]
    fn rejects_missing_fixture_type() {
        let mut config = default_show_config(true);
        config.fixtures[0].fixture_type_id = "missing".into();
        let error = ValidatedShowConfig::new(config, true).expect_err("missing type must fail");
        assert!(error.to_string().contains("unknown grandMA2 fixture type"));
    }

    #[test]
    fn serialized_configuration_round_trips_without_profiles() {
        let config =
            ValidatedShowConfig::new(default_show_config(true), true).expect("defaults validate");
        let json = to_json(&config).expect("configuration serializes");
        assert!(!json.contains("\"profiles\""));
        let parsed = parse_json(&json, true).expect("configuration parses");
        assert_eq!(parsed.fixtures, config.fixtures);
    }

    #[test]
    fn color_speed_requires_one_decimal_increments() {
        for color_speed in [3.3, 3.4] {
            let mut config = default_show_config(true);
            config
                .effects
                .as_mut()
                .expect("effects configuration")
                .color_speed = color_speed;
            ValidatedShowConfig::new(config, true)
                .expect("one-decimal color speed should validate");
        }

        let mut config = default_show_config(true);
        config
            .effects
            .as_mut()
            .expect("effects configuration")
            .color_speed = 3.35;
        let error =
            ValidatedShowConfig::new(config, true).expect_err("fractional step must be rejected");
        assert!(
            error
                .to_string()
                .contains("color speed must use increments of 0.1")
        );
    }
}
