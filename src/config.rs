use std::{fs, path::Path};

use anyhow::{Context, Result};
use serde_json::{Map, Value};

use crate::proto::v1::{
    AudioConfig, AudioInputMode, ChannelCapability, ChannelConfig, DmxConfig, DualColorMapping,
    EffectFixtureMode, EffectsConfig, FixtureConfig, FixtureProfile, MovementMode, RotationMode,
    ShowConfig, StrobeEffectMode, VisualizationMode,
};

pub fn load(path: &Path, simulate: bool) -> Result<ShowConfig> {
    if !path.exists() {
        return Ok(default_show_config(simulate));
    }

    let contents = fs::read_to_string(path)
        .with_context(|| format!("failed to read configuration from {}", path.display()))?;
    parse_json(&contents, simulate)
        .with_context(|| format!("invalid show configuration in {}", path.display()))
}

pub fn parse_json(contents: &str, simulate: bool) -> Result<ShowConfig> {
    let value: Value = serde_json::from_str(contents).context("invalid JSON")?;
    let migrated = migrate_legacy_config(value, simulate);
    serde_json::from_value(migrated).context("configuration does not match the show schema")
}

pub fn to_json(config: &ShowConfig) -> Result<String> {
    serde_json::to_string_pretty(config).context("failed to serialize show configuration")
}

pub fn save(path: &Path, config: &ShowConfig) -> Result<()> {
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty());
    if let Some(parent) = parent {
        fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }
    let json = to_json(config)?;
    fs::write(path, format!("{json}\n"))
        .with_context(|| format!("failed to save configuration to {}", path.display()))
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
            device_name: String::new(),
            pipewire_source_name: String::new(),
            simulate,
            gain: 1.0,
            beatnet_model_path: "models/beatnet-plus.pt".into(),
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
        audio
            .entry("pipewire_source_name")
            .or_insert(Value::String(String::new()));
        audio
            .entry("device_name")
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
            serde_json::to_value(default_profiles()).expect("default profiles serialize"),
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
        let config = parse_json(include_str!("../example_config.json"), false).unwrap();
        assert_eq!(config.name, "Example Light Show");
        assert_eq!(config.fixtures.len(), 2);
        assert_eq!(
            config.effects.unwrap().mode(),
            VisualizationMode::RainbowWave
        );
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
        let config: ShowConfig =
            serde_json::from_value(migrate_legacy_config(legacy, false)).unwrap();
        let fixture = &config.fixtures[0];
        assert_eq!(fixture.intensity_scale, 0.0);
        assert_eq!(fixture.pan_max, 0);
        assert_eq!(fixture.channels[0].offset, 1);
        assert_eq!(fixture.channels[0].channel_type, "intensity_red");
        assert_eq!(fixture.channels[1].offset, 4);
    }
}
