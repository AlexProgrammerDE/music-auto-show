use std::collections::HashMap;

use anyhow::{Result, bail};

use crate::proto::v1::{
    FixtureEmitter, FixtureEmitterKind, FixtureMesh, FixtureModelKind, FixtureModelNode,
    FixtureModelNodeKind, FixtureModelTransform, FixtureVisual, FixtureVisualKind, GrandMa2Channel,
    GrandMa2ChannelFunction, GrandMa2ChannelSet, GrandMa2FixtureFile, GrandMa2FixtureType,
};

mod input;

pub use input::import_fixture_file;
use input::parse_fixture_file;
#[cfg(test)]
use input::parse_xml;

pub const PURELIGHT_MUVY_WASHQ_ID: &str = "builtin:purelight-muvy-washq-14ch:0";
pub const SHOWTEC_TECHNO_DERBY_ID: &str = "builtin:showtec-techno-derby-4ch:0";
pub const LIXADA_MINI_BUTTERFLY_ID: &str = "builtin:lixada-mini-butterfly-7ch:0";

const MAX_FIXTURE_FILE_BYTES: usize = 2 * 1024 * 1024;

const BUILTIN_FILES: [(&str, &str, &str); 3] = [
    (
        "builtin:purelight-muvy-washq-14ch",
        "PURElight@MUVY_WashQ@14_channel.xml",
        include_str!("../fixtures/grandma2/purelight-muvy-washq-14ch.xml"),
    ),
    (
        "builtin:showtec-techno-derby-4ch",
        "Showtec@Techno_Derby@Default.xml",
        include_str!("../fixtures/grandma2/showtec-techno-derby-4ch.xml"),
    ),
    (
        "builtin:lixada-mini-butterfly-7ch",
        "Lixada@LED_Mini_Butterfly@7_channel.xml",
        include_str!("../fixtures/grandma2/lixada-mini-butterfly-7ch.xml"),
    ),
];

#[derive(Clone, Debug, Default)]
pub struct GrandMa2Library {
    fixture_types: Vec<ParsedFixtureType>,
    by_id: HashMap<String, usize>,
}

impl GrandMa2Library {
    /// # Errors
    /// Returns an error if a bundled or imported fixture definition is invalid.
    pub fn load(imported: &[GrandMa2FixtureFile]) -> Result<Self> {
        let mut fixture_types = Vec::new();
        for (id, filename, xml) in BUILTIN_FILES {
            let file = GrandMa2FixtureFile {
                id: id.into(),
                filename: filename.into(),
                xml: xml.into(),
            };
            fixture_types.extend(parse_fixture_file(&file, true)?.fixture_types);
        }
        for file in imported {
            fixture_types.extend(parse_fixture_file(file, false)?.fixture_types);
        }

        let mut by_id = HashMap::new();
        for (index, fixture_type) in fixture_types.iter().enumerate() {
            if by_id.insert(fixture_type.id.clone(), index).is_some() {
                bail!(
                    "grandMA2 fixture type id '{}' is used more than once",
                    fixture_type.id
                );
            }
        }
        Ok(Self {
            fixture_types,
            by_id,
        })
    }

    pub fn get(&self, id: &str) -> Option<&ParsedFixtureType> {
        self.by_id
            .get(id)
            .and_then(|index| self.fixture_types.get(*index))
    }

    pub fn fixture_types(&self) -> &[ParsedFixtureType] {
        &self.fixture_types
    }

    pub fn to_proto(&self) -> Vec<GrandMa2FixtureType> {
        self.fixture_types
            .iter()
            .map(ParsedFixtureType::to_proto)
            .collect()
    }
}

#[derive(Clone, Debug)]
pub struct ParsedFixtureType {
    pub id: String,
    pub file_id: String,
    pub name: String,
    pub manufacturer: String,
    pub mode: String,
    pub footprint: u32,
    pub built_in: bool,
    pub warnings: Vec<String>,
    pub channels: Vec<MappedChannel>,
    pub visual: FixtureVisual,
    pub revision: String,
}

impl ParsedFixtureType {
    pub fn has_semantic(&self, semantic: DmxSemantic) -> bool {
        self.channels
            .iter()
            .any(|channel| channel.has_semantic(semantic))
    }

    pub fn has_direct_color(&self) -> bool {
        self.channels.iter().any(MappedChannel::is_direct_color)
    }

    pub fn to_proto(&self) -> GrandMa2FixtureType {
        GrandMa2FixtureType {
            id: self.id.clone(),
            file_id: self.file_id.clone(),
            name: self.name.clone(),
            manufacturer: self.manufacturer.clone(),
            mode: self.mode.clone(),
            channel_count: self.footprint,
            built_in: self.built_in,
            warnings: self.warnings.clone(),
            channels: self.channels.iter().map(MappedChannel::to_proto).collect(),
            visual: Some(self.visual.clone()),
            revision: self.revision.clone(),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DmxSemantic {
    NoFeature,
    Dimmer,
    Red,
    Green,
    Blue,
    White,
    Amber,
    Uv,
    Cyan,
    Magenta,
    Yellow,
    CustomColor,
    Pan,
    Tilt,
    PositionSpeed,
    Strobe,
    Shutter,
    ColorMacro,
    ColorMacroSpeed,
    Rotation,
    EffectSpeed,
    EffectPattern,
    Gobo,
    Prism,
    Zoom,
    Focus,
    Iris,
    Reset,
    Sound,
    Other,
}

#[derive(Clone, Debug)]
pub struct MappedChannel {
    pub coarse: u32,
    pub fine: Option<u32>,
    pub name: String,
    pub attribute: String,
    pub feature: String,
    pub default_value: u32,
    pub functions: Vec<MappedFunction>,
    pub color_hues: Vec<f32>,
}

impl MappedChannel {
    pub fn has_semantic(&self, semantic: DmxSemantic) -> bool {
        self.functions
            .iter()
            .any(|function| function.semantic == semantic)
    }

    pub fn is_direct_color(&self) -> bool {
        self.functions.iter().any(|function| {
            matches!(
                function.semantic,
                DmxSemantic::Red
                    | DmxSemantic::Green
                    | DmxSemantic::Blue
                    | DmxSemantic::White
                    | DmxSemantic::Amber
                    | DmxSemantic::Uv
                    | DmxSemantic::Cyan
                    | DmxSemantic::Magenta
                    | DmxSemantic::Yellow
                    | DmxSemantic::CustomColor
            )
        })
    }

    fn to_proto(&self) -> GrandMa2Channel {
        GrandMa2Channel {
            coarse: self.coarse,
            fine: self.fine,
            name: self.name.clone(),
            attribute: self.attribute.clone(),
            feature: self.feature.clone(),
            default_value: self.default_value,
            functions: self
                .functions
                .iter()
                .map(MappedFunction::to_proto)
                .collect(),
        }
    }
}

#[derive(Clone, Debug)]
pub struct MappedFunction {
    pub name: String,
    pub attribute: String,
    pub feature: String,
    pub subattribute: String,
    pub from_dmx: u32,
    pub to_dmx: u32,
    pub physical_from: f32,
    pub physical_to: f32,
    pub channel_sets: Vec<MappedChannelSet>,
    pub semantic: DmxSemantic,
}

impl MappedFunction {
    #[expect(
        clippy::cast_possible_truncation,
        clippy::cast_sign_loss,
        reason = "The normalized value is clamped and rounded to a whole DMX step"
    )]
    pub fn normalized_value(&self, value: f32) -> u32 {
        let value = value.clamp(0.0, 1.0);
        self.from_dmx + (value * self.to_dmx.saturating_sub(self.from_dmx) as f32).round() as u32
    }

    fn to_proto(&self) -> GrandMa2ChannelFunction {
        GrandMa2ChannelFunction {
            name: self.name.clone(),
            attribute: self.attribute.clone(),
            feature: self.feature.clone(),
            subattribute: self.subattribute.clone(),
            from_dmx: self.from_dmx,
            to_dmx: self.to_dmx,
            physical_from: self.physical_from,
            physical_to: self.physical_to,
            channel_sets: self
                .channel_sets
                .iter()
                .map(MappedChannelSet::to_proto)
                .collect(),
        }
    }
}

#[derive(Clone, Debug)]
pub struct MappedChannelSet {
    pub name: String,
    pub from_dmx: u32,
    pub to_dmx: u32,
}

impl MappedChannelSet {
    fn to_proto(&self) -> GrandMa2ChannelSet {
        GrandMa2ChannelSet {
            name: self.name.clone(),
            from_dmx: self.from_dmx,
            to_dmx: self.to_dmx,
        }
    }
}

pub struct ParsedFixtureFile {
    pub file: GrandMa2FixtureFile,
    pub fixture_types: Vec<ParsedFixtureType>,
}

#[derive(Default)]
struct RawFixtureType {
    name: String,
    mode: String,
    manufacturer: String,
    revision: String,
    warnings: Vec<String>,
    modules: Vec<RawModule>,
    instances: Vec<RawInstance>,
}

#[derive(Default)]
struct RawModule {
    index: u32,
    name: String,
    class: String,
    beam_type: String,
    beam_angle: f32,
    beam_intensity: f32,
    size: Option<[f32; 3]>,
    channels: Vec<RawChannel>,
}

#[derive(Default)]
struct RawChannel {
    coarse: u32,
    fine: Option<u32>,
    attribute: String,
    feature: String,
    default_value: f32,
    functions: Vec<RawFunction>,
}

#[derive(Default)]
struct RawFunction {
    name: String,
    attribute: String,
    feature: String,
    subattribute: String,
    min_dmx_24: Option<u32>,
    max_dmx_24: Option<u32>,
    physical_from: f32,
    physical_to: f32,
    sets: Vec<RawChannelSet>,
}

#[derive(Default)]
struct RawChannelSet {
    name: String,
    from_dmx: u32,
    to_dmx: u32,
}

#[derive(Default)]
struct RawInstance {
    index: u32,
    module_index: u32,
    patch: u32,
}

fn classify_function(
    channel: &RawChannel,
    function: &RawFunction,
    custom_color: bool,
) -> DmxSemantic {
    let combined = format!(
        "{} {} {}",
        function.subattribute, function.attribute, channel.attribute
    );
    let rotation = function.physical_from.abs() > 1.0 || function.physical_to.abs() > 1.0;
    classify_token(
        &combined,
        &format!("{} {}", function.feature, channel.feature),
        rotation,
        custom_color,
    )
}

fn classify_token(
    token: &str,
    feature: &str,
    physical_rotation: bool,
    custom_color: bool,
) -> DmxSemantic {
    let token = normalized(token);
    let feature = normalized(feature);
    if token.contains("NOFEATURE") || token == "DUMMY" {
        DmxSemantic::NoFeature
    } else if token.contains("FIXTUREGLOBALRESET") || feature.contains("RESET") {
        DmxSemantic::Reset
    } else if token.contains("SOUNDMODE") || token.contains("SOUND") {
        DmxSemantic::Sound
    } else if token.contains("POSITIONMSPEED") {
        DmxSemantic::PositionSpeed
    } else if token == "PAN" || token.starts_with("PAN ") {
        DmxSemantic::Pan
    } else if token == "TILT" || token.starts_with("TILT ") {
        DmxSemantic::Tilt
    } else if token.contains("STROBE") {
        DmxSemantic::Strobe
    } else if token.contains("SHUTTER") {
        DmxSemantic::Shutter
    } else if token.contains("COLORRGB1") {
        if custom_color {
            DmxSemantic::CustomColor
        } else {
            DmxSemantic::Red
        }
    } else if token.contains("COLORRGB2") {
        if custom_color {
            DmxSemantic::CustomColor
        } else {
            DmxSemantic::Green
        }
    } else if token.contains("COLORRGB3") {
        if custom_color {
            DmxSemantic::CustomColor
        } else {
            DmxSemantic::Blue
        }
    } else if token.contains("COLORRGB4") {
        DmxSemantic::Amber
    } else if token.contains("COLORRGB5") {
        DmxSemantic::White
    } else if token.contains("COLORRGB6") {
        DmxSemantic::Uv
    } else if token.contains("COLORRGB7") {
        DmxSemantic::Cyan
    } else if token.contains("COLORRGB8") {
        DmxSemantic::Magenta
    } else if token.contains("COLORRGB9") {
        DmxSemantic::Yellow
    } else if token == "DIM" || feature.contains("DIMMER") {
        DmxSemantic::Dimmer
    } else if token.contains("COLORMIXMACRORATE") {
        DmxSemantic::ColorMacroSpeed
    } else if token.contains("COLORMACROS") || token.contains("COLORMIX") {
        DmxSemantic::ColorMacro
    } else if token.contains("GOBO") && (token.contains("ROT") || physical_rotation) {
        DmxSemantic::Rotation
    } else if token.contains("EFFECTSPEED") {
        DmxSemantic::EffectSpeed
    } else if token.contains("EFFECTMACRO") {
        DmxSemantic::EffectPattern
    } else if token.contains("GOBO") {
        DmxSemantic::Gobo
    } else if token.contains("PRISM") {
        DmxSemantic::Prism
    } else if token.contains("ZOOM") {
        DmxSemantic::Zoom
    } else if token.contains("FOCUS") {
        DmxSemantic::Focus
    } else if token.contains("IRIS") {
        DmxSemantic::Iris
    } else {
        DmxSemantic::Other
    }
}

fn normalized(value: &str) -> String {
    value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() {
                character.to_ascii_uppercase()
            } else {
                ' '
            }
        })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn color_hues(name: &str) -> Vec<f32> {
    let lower = name.to_ascii_lowercase();
    let named = [
        ("red", 0.0),
        ("yellow", 1.0 / 6.0),
        ("green", 1.0 / 3.0),
        ("cyan", 0.5),
        ("blue", 2.0 / 3.0),
        ("violet", 5.0 / 6.0),
        ("magenta", 5.0 / 6.0),
        ("white", -1.0),
        ("amber", 0.1),
        ("uv", 0.75),
    ];
    let hues = named
        .iter()
        .filter(|(color, _)| lower.contains(color))
        .map(|(_, hue)| *hue)
        .collect::<Vec<_>>();
    if hues.len() >= 2 { hues } else { Vec::new() }
}

#[expect(
    clippy::too_many_lines,
    reason = "Keep the visual defaults and fixture geometry mapping together"
)]
fn compile_visual(raw: &RawFixtureType, warnings: &mut Vec<String>) -> FixtureVisual {
    let main_module = raw
        .modules
        .iter()
        .find(|module| !module.channels.is_empty())
        .or_else(|| raw.modules.first());
    let ma_dimensions = main_module
        .and_then(|module| module.size)
        .filter(|size| size.iter().all(|value| value.is_finite() && *value > 0.0))
        .unwrap_or_else(|| {
            warnings.push(
                "The grandMA2 file has no usable body dimensions; preview dimensions are estimated."
                    .into(),
            );
            [0.24, 0.18, 0.20]
        });
    let dimensions = ma_dimensions_to_stage(ma_dimensions);

    let moving_head = raw
        .modules
        .iter()
        .any(|module| normalized(&module.class).contains("HEADMOVER"));
    let effect = raw.modules.iter().any(|module| {
        module.channels.iter().any(|channel| {
            let token = normalized(&format!("{} {}", channel.attribute, channel.feature));
            token.contains("EFFECT") || token.contains("GOBO")
        })
    });
    let kind = if moving_head {
        FixtureVisualKind::MovingHead
    } else if effect {
        FixtureVisualKind::Effect
    } else if raw.modules.iter().any(|module| {
        let beam = normalized(&module.beam_type);
        matches!(beam.as_str(), "SPOT" | "WASH" | "EFFECT" | "FIBER" | "BEAM")
    }) {
        FixtureVisualKind::Par
    } else {
        FixtureVisualKind::Other
    };
    for module in &raw.modules {
        let beam = normalized(&module.beam_type);
        if !beam.is_empty()
            && !matches!(beam.as_str(), "NONE" | "SPOT" | "WASH" | "EFFECT" | "FIBER")
        {
            warnings.push(format!(
                "Module '{}' uses unsupported grandMA2 beam type '{}'; the preview treats it as Spot.",
                module.name, module.beam_type
            ));
        }
    }

    let visual_modules = visual_modules(raw);
    let mut emitters = visual_modules
        .iter()
        .copied()
        .filter(|module| {
            let beam_type = normalized(&module.1.beam_type);
            let class = normalized(&module.1.class);
            (!beam_type.is_empty() && beam_type != "NONE") || class == "LED"
        })
        .map(|(instance_index, module)| {
            let lower_name = module.name.to_ascii_lowercase();
            let emitter_kind = if lower_name.contains("strobe") {
                FixtureEmitterKind::Strobe
            } else if lower_name.contains("white") {
                FixtureEmitterKind::White
            } else {
                FixtureEmitterKind::Color
            };
            FixtureEmitter {
                id: format!("instance-{instance_index}-module-{}", module.index),
                name: module.name.clone(),
                kind: emitter_kind as i32,
                beam_angle_degrees: if module.beam_angle > 0.0 {
                    module.beam_angle.clamp(1.0, 179.0)
                } else {
                    0.0
                },
                beam_intensity: module.beam_intensity.max(0.0),
                x_m: 0.0,
                y_m: 0.0,
                z_m: dimensions[2] / 2.0,
                color_rgb: if emitter_kind == FixtureEmitterKind::White {
                    0xff_ff_ff
                } else {
                    0
                },
                direction_x: 0.0,
                direction_y: 0.0,
                direction_z: 1.0,
                casts_beam: normalized(&module.class) != "LED"
                    && normalized(&module.beam_type) != "NONE",
                aperture_m: if moving_head {
                    dimensions[0].min(dimensions[1]) * 0.4
                } else {
                    module
                        .size
                        .map_or(0.04, |size| size[0].min(size[2]))
                        .clamp(0.005, 0.5)
                },
                model_node_id: String::new(),
            }
        })
        .collect::<Vec<_>>();
    if emitters.is_empty() {
        emitters.push(FixtureEmitter {
            id: "estimated-emitter".into(),
            name: "Estimated beam".into(),
            kind: FixtureEmitterKind::Color as i32,
            beam_angle_degrees: main_module.map_or(0.0, |module| module.beam_angle),
            beam_intensity: main_module.map_or(0.0, |module| module.beam_intensity),
            x_m: 0.0,
            y_m: 0.0,
            z_m: dimensions[2] / 2.0,
            color_rgb: 0,
            direction_x: 0.0,
            direction_y: 0.0,
            direction_z: 1.0,
            casts_beam: true,
            aperture_m: dimensions[0].min(dimensions[1]) * 0.4,
            model_node_id: String::new(),
        });
        warnings.push(
            "The grandMA2 file has no beam module; the preview uses one estimated emitter.".into(),
        );
    }
    layout_emitters(&mut emitters, dimensions, moving_head, effect);
    let model_kind = if moving_head {
        FixtureModelKind::HeadMover
    } else if effect
        && emitters
            .iter()
            .any(|emitter| emitter.kind == FixtureEmitterKind::Strobe as i32)
    {
        FixtureModelKind::DerbyEffect
    } else if effect {
        FixtureModelKind::CompactEffect
    } else {
        FixtureModelKind::Generic
    };

    let (pan_min, pan_max) = physical_axis_range(raw, DmxSemantic::Pan).unwrap_or((0.0, 0.0));
    let (tilt_min, tilt_max) = physical_axis_range(raw, DmxSemantic::Tilt).unwrap_or((0.0, 0.0));
    let (zoom_from, zoom_to) = physical_axis_range(raw, DmxSemantic::Zoom).unwrap_or((0.0, 0.0));
    FixtureVisual {
        kind: kind as i32,
        width_m: dimensions[0],
        height_m: dimensions[1],
        depth_m: dimensions[2],
        pan_min_degrees: pan_min,
        pan_max_degrees: pan_max,
        tilt_min_degrees: tilt_min,
        tilt_max_degrees: tilt_max,
        emitters,
        model_nodes: Vec::new(),
        model_kind: model_kind as i32,
        zoom_physical_from_degrees: zoom_from,
        zoom_physical_to_degrees: zoom_to,
    }
}

fn visual_modules(raw: &RawFixtureType) -> Vec<(u32, &RawModule)> {
    if raw.instances.is_empty() {
        raw.modules
            .iter()
            .enumerate()
            .filter(|(_, module)| !module.channels.is_empty())
            .filter_map(|(index, module)| u32::try_from(index).ok().map(|index| (index, module)))
            .collect()
    } else {
        raw.instances
            .iter()
            .filter_map(|instance| {
                raw.modules
                    .iter()
                    .find(|module| {
                        module.index == instance.module_index && !module.channels.is_empty()
                    })
                    .map(|module| (instance.index, module))
            })
            .collect()
    }
}

fn ma_dimensions_to_stage(dimensions: [f32; 3]) -> [f32; 3] {
    [dimensions[0], dimensions[2], dimensions[1]]
}

fn apply_builtin_model(id: &str, visual: &mut FixtureVisual) {
    match id {
        PURELIGHT_MUVY_WASHQ_ID => apply_muvy_model(visual),
        SHOWTEC_TECHNO_DERBY_ID => apply_techno_derby_model(visual),
        LIXADA_MINI_BUTTERFLY_ID => apply_mini_butterfly_model(visual),
        _ => {}
    }
}

#[expect(
    clippy::too_many_lines,
    reason = "Keep the MUVY model geometry together so the dimensions and emitter positions can be reviewed together"
)]
fn apply_muvy_model(visual: &mut FixtureVisual) {
    let lens_positions = [
        (0.0, 0.0),
        (0.0, 0.046),
        (0.040, 0.023),
        (0.040, -0.023),
        (0.0, -0.046),
        (-0.040, -0.023),
        (-0.040, 0.023),
    ];
    let mut emitters = Vec::with_capacity(lens_positions.len());
    let mut head_children = vec![mesh_node(
        "muvy-head",
        "Lamp head",
        [0.0, 0.0, 0.0],
        [0.18, 0.11, 0.16],
    )];

    for (index, (x, z)) in lens_positions.into_iter().enumerate() {
        let emitter_id = format!("muvy-lens-{}", index + 1);
        let marker_id = format!("muvy-marker-{}", index + 1);
        let is_primary = index == 0;
        emitters.push(fixture_emitter(
            &emitter_id,
            &format!("RGBW lens {}", index + 1),
            FixtureEmitterKind::Color,
            45.0,
            [x, -0.061, z],
            [0.0, -1.0, 0.0],
            is_primary,
            if is_primary { 0.104 } else { 0.03 },
            &marker_id,
        ));
        head_children.push(marker_node(
            &marker_id,
            if is_primary { "_XLD" } else { "_XB" },
            if is_primary {
                FixtureModelNodeKind::BeamDiameter
            } else {
                FixtureModelNodeKind::Beam
            },
            [x, -0.061, z],
            [0.0, -1.0, 0.0],
            &emitter_id,
            if is_primary { 0.104 } else { 0.03 },
            0.0,
        ));
        if is_primary {
            head_children.push(marker_node(
                "muvy-marker-clip",
                "_XLC",
                FixtureModelNodeKind::BeamClip,
                [x, -0.061, z],
                [0.0, -1.0, 0.0],
                &emitter_id,
                0.0,
                0.012,
            ));
        }
    }

    let tilt = model_node(
        "muvy-tilt-axis",
        "_XAT",
        FixtureModelNodeKind::TiltAxis,
        [0.0, -0.14, 0.0],
        [0.0, 0.0, 0.0, 1.0],
        None,
        head_children,
        "",
        0.0,
        0.0,
    );
    let pan = model_node(
        "muvy-pan-axis",
        "_XAP",
        FixtureModelNodeKind::PanAxis,
        [0.0, -0.08, 0.0],
        [0.0, 0.0, 0.0, 1.0],
        None,
        vec![
            mesh_node(
                "muvy-yoke-crossbar",
                "Yoke crossbar",
                [0.0, -0.025, 0.0],
                [0.22, 0.05, 0.07],
            ),
            mesh_node(
                "muvy-yoke-left",
                "Yoke left",
                [-0.105, -0.08, 0.0],
                [0.025, 0.16, 0.07],
            ),
            mesh_node(
                "muvy-yoke-right",
                "Yoke right",
                [0.105, -0.08, 0.0],
                [0.025, 0.16, 0.07],
            ),
            tilt,
        ],
        "",
        0.0,
        0.0,
    );
    visual.emitters = emitters;
    visual.model_nodes = vec![
        mesh_node("muvy-base", "Base", [0.0, -0.04, 0.0], [0.27, 0.08, 0.22]),
        pan,
    ];
    visual.model_kind = FixtureModelKind::HeadMover as i32;
}

fn apply_techno_derby_model(visual: &mut FixtureVisual) {
    let mut emitters = Vec::with_capacity(20);
    let mut nodes = vec![mesh_node(
        "techno-derby-body",
        "Housing",
        [0.0, 0.0, 0.0],
        [visual.width_m, visual.height_m, visual.depth_m],
    )];
    let color_positions = [
        (-0.070, 0.038),
        (0.070, 0.038),
        (-0.070, -0.038),
        (0.070, -0.038),
    ];
    for (index, (x, y)) in color_positions.into_iter().enumerate() {
        let emitter_id = format!("techno-derby-color-{}", index + 1);
        let marker_id = format!("techno-derby-beam-{}", index + 1);
        let direction = normalize_vector([x * 3.8, y * 3.2, 1.0]);
        emitters.push(fixture_emitter(
            &emitter_id,
            &format!("RGBW effect {}", index + 1),
            FixtureEmitterKind::Color,
            5.0,
            [x, y, visual.depth_m / 2.0],
            direction,
            true,
            0.042,
            &marker_id,
        ));
        nodes.push(marker_node(
            &marker_id,
            &format!("_XB{}", index + 1),
            FixtureModelNodeKind::Beam,
            [x, y, visual.depth_m / 2.0],
            direction,
            &emitter_id,
            0.042,
            0.0,
        ));
    }

    for index in 0..16 {
        let column = index % 8;
        let row = index / 8;
        let x = -0.108 + column as f32 * (0.216 / 7.0);
        let y = if row == 0 { 0.078 } else { -0.078 };
        let emitter_id = format!("techno-derby-strobe-{}", index + 1);
        let marker_id = format!("techno-derby-strobe-marker-{}", index + 1);
        emitters.push(fixture_emitter(
            &emitter_id,
            &format!("White strobe {}", index + 1),
            FixtureEmitterKind::Strobe,
            0.0,
            [x, y, visual.depth_m / 2.0 + 0.002],
            [0.0, 0.0, 1.0],
            false,
            0.015,
            &marker_id,
        ));
        nodes.push(marker_node(
            &marker_id,
            &format!("Strobe lens {}", index + 1),
            FixtureModelNodeKind::Beam,
            [x, y, visual.depth_m / 2.0 + 0.002],
            [0.0, 0.0, 1.0],
            &emitter_id,
            0.015,
            0.0,
        ));
    }

    visual.emitters = emitters;
    visual.model_nodes = nodes;
    visual.model_kind = FixtureModelKind::DerbyEffect as i32;
}

fn apply_mini_butterfly_model(visual: &mut FixtureVisual) {
    let mut emitters = Vec::with_capacity(4);
    let mut nodes = vec![mesh_node(
        "mini-butterfly-body",
        "Housing",
        [0.0, 0.0, 0.0],
        [visual.width_m, visual.height_m, visual.depth_m],
    )];
    let positions = [
        (-0.045, 0.030),
        (0.045, 0.030),
        (-0.045, -0.030),
        (0.045, -0.030),
    ];
    for (index, (x, y)) in positions.into_iter().enumerate() {
        let emitter_id = format!("mini-butterfly-color-{}", index + 1);
        let marker_id = format!("mini-butterfly-beam-{}", index + 1);
        let direction = normalize_vector([x * 5.0, y * 4.0, 1.0]);
        emitters.push(fixture_emitter(
            &emitter_id,
            &format!("Color effect {}", index + 1),
            FixtureEmitterKind::Color,
            5.0,
            [x, y, visual.depth_m / 2.0],
            direction,
            true,
            0.03,
            &marker_id,
        ));
        nodes.push(marker_node(
            &marker_id,
            &format!("_XB{}", index + 1),
            FixtureModelNodeKind::Beam,
            [x, y, visual.depth_m / 2.0],
            direction,
            &emitter_id,
            0.03,
            0.0,
        ));
    }
    visual.emitters = emitters;
    visual.model_nodes = nodes;
    visual.model_kind = FixtureModelKind::CompactEffect as i32;
}

#[expect(
    clippy::too_many_arguments,
    reason = "The parameters describe the fixture geometry at the call site"
)]
fn fixture_emitter(
    id: &str,
    name: &str,
    kind: FixtureEmitterKind,
    beam_angle_degrees: f32,
    position: [f32; 3],
    direction: [f32; 3],
    casts_beam: bool,
    aperture_m: f32,
    model_node_id: &str,
) -> FixtureEmitter {
    FixtureEmitter {
        id: id.into(),
        name: name.into(),
        kind: kind as i32,
        beam_angle_degrees,
        beam_intensity: 0.0,
        x_m: position[0],
        y_m: position[1],
        z_m: position[2],
        color_rgb: if matches!(kind, FixtureEmitterKind::White | FixtureEmitterKind::Strobe) {
            0xff_ff_ff
        } else {
            0
        },
        direction_x: direction[0],
        direction_y: direction[1],
        direction_z: direction[2],
        casts_beam,
        aperture_m,
        model_node_id: model_node_id.into(),
    }
}

#[expect(
    clippy::too_many_arguments,
    reason = "The parameters describe the fixture geometry at the call site"
)]
fn marker_node(
    id: &str,
    name: &str,
    kind: FixtureModelNodeKind,
    position: [f32; 3],
    direction: [f32; 3],
    emitter_id: &str,
    beam_diameter_m: f32,
    beam_clipping_distance_m: f32,
) -> FixtureModelNode {
    model_node(
        id,
        name,
        kind,
        position,
        quaternion_from_directions([0.0, 0.0, 1.0], direction),
        None,
        Vec::new(),
        emitter_id,
        beam_diameter_m,
        beam_clipping_distance_m,
    )
}

fn mesh_node(id: &str, name: &str, position: [f32; 3], dimensions: [f32; 3]) -> FixtureModelNode {
    model_node(
        id,
        name,
        FixtureModelNodeKind::Mesh,
        position,
        [0.0, 0.0, 0.0, 1.0],
        Some(box_mesh(id, name, dimensions)),
        Vec::new(),
        "",
        0.0,
        0.0,
    )
}

#[expect(
    clippy::too_many_arguments,
    reason = "The parameters describe the fixture geometry at the call site"
)]
fn model_node(
    id: &str,
    name: &str,
    kind: FixtureModelNodeKind,
    position: [f32; 3],
    quaternion: [f32; 4],
    mesh: Option<FixtureMesh>,
    children: Vec<FixtureModelNode>,
    emitter_id: &str,
    beam_diameter_m: f32,
    beam_clipping_distance_m: f32,
) -> FixtureModelNode {
    FixtureModelNode {
        id: id.into(),
        name: name.into(),
        kind: kind as i32,
        transform: Some(FixtureModelTransform {
            x_m: position[0],
            y_m: position[1],
            z_m: position[2],
            quaternion_x: quaternion[0],
            quaternion_y: quaternion[1],
            quaternion_z: quaternion[2],
            quaternion_w: quaternion[3],
            scale_x: 1.0,
            scale_y: 1.0,
            scale_z: 1.0,
        }),
        mesh,
        children,
        emitter_id: emitter_id.into(),
        beam_diameter_m,
        beam_clipping_distance_m,
    }
}

fn box_mesh(id: &str, name: &str, dimensions: [f32; 3]) -> FixtureMesh {
    let [half_x, half_y, half_z] = dimensions.map(|value| value / 2.0);
    FixtureMesh {
        id: format!("{id}-mesh"),
        name: name.into(),
        vertices: vec![
            -half_x, -half_y, -half_z, half_x, -half_y, -half_z, half_x, half_y, -half_z, -half_x,
            half_y, -half_z, -half_x, -half_y, half_z, half_x, -half_y, half_z, half_x, half_y,
            half_z, -half_x, half_y, half_z,
        ],
        normals: Vec::new(),
        indices: vec![
            0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 3, 7, 6, 3, 6, 2, 1, 2, 6, 1, 6,
            5, 0, 4, 7, 0, 7, 3,
        ],
        color_rgb: 0x35_3b_3d,
    }
}

fn quaternion_from_directions(from: [f32; 3], to: [f32; 3]) -> [f32; 4] {
    let from = normalize_vector(from);
    let to = normalize_vector(to);
    let dot = (from[0] * to[0] + from[1] * to[1] + from[2] * to[2]).clamp(-1.0, 1.0);
    if dot < -0.999_999 {
        return [1.0, 0.0, 0.0, 0.0];
    }
    let cross = [
        from[1] * to[2] - from[2] * to[1],
        from[2] * to[0] - from[0] * to[2],
        from[0] * to[1] - from[1] * to[0],
    ];
    let quaternion = [cross[0], cross[1], cross[2], 1.0 + dot];
    let length = quaternion
        .iter()
        .map(|component| component * component)
        .sum::<f32>()
        .sqrt();
    quaternion.map(|component| component / length)
}

fn physical_axis_range(raw: &RawFixtureType, semantic: DmxSemantic) -> Option<(f32, f32)> {
    raw.modules
        .iter()
        .flat_map(|module| &module.channels)
        .flat_map(|channel| {
            channel
                .functions
                .iter()
                .map(move |function| (channel, function))
        })
        .find_map(|(channel, function)| {
            (classify_function(channel, function, false) == semantic)
                .then_some((function.physical_from, function.physical_to))
        })
}

fn layout_emitters(
    emitters: &mut [FixtureEmitter],
    dimensions: [f32; 3],
    moving_head: bool,
    effect: bool,
) {
    if moving_head {
        return;
    }
    for kind in [
        FixtureEmitterKind::Color,
        FixtureEmitterKind::White,
        FixtureEmitterKind::Strobe,
    ] {
        let indices = emitters
            .iter()
            .enumerate()
            .filter(|(_, emitter)| emitter.kind == kind as i32)
            .map(|(index, _)| index)
            .collect::<Vec<_>>();
        if indices.is_empty() {
            continue;
        }
        if kind == FixtureEmitterKind::Strobe && indices.len() >= 4 {
            let row_length = indices.len().div_ceil(2);
            for (slot, index) in indices.into_iter().enumerate() {
                let column = slot % row_length;
                emitters[index].x_m = if row_length == 1 {
                    0.0
                } else {
                    -dimensions[0] * 0.41
                        + dimensions[0] * 0.82 * column as f32 / (row_length - 1) as f32
                };
                emitters[index].y_m = if slot < row_length {
                    dimensions[1] * 0.38
                } else {
                    -dimensions[1] * 0.38
                };
            }
            continue;
        }
        let columns = (indices.len() - 1).isqrt() + 1;
        let rows = indices.len().div_ceil(columns);
        let scale = if kind == FixtureEmitterKind::Strobe {
            0.82
        } else {
            0.52
        };
        let width = dimensions[0] * scale;
        let height = dimensions[1] * scale;
        for (slot, index) in indices.into_iter().enumerate() {
            let column = slot % columns;
            let row = slot / columns;
            emitters[index].x_m = if columns == 1 {
                0.0
            } else {
                -width / 2.0 + width * column as f32 / (columns - 1) as f32
            };
            emitters[index].y_m = if rows == 1 {
                0.0
            } else {
                height / 2.0 - height * row as f32 / (rows - 1) as f32
            };
        }
    }
    if effect {
        let half_width = (dimensions[0] * 0.5).max(0.01);
        let half_height = (dimensions[1] * 0.5).max(0.01);
        for emitter in emitters.iter_mut().filter(|emitter| emitter.casts_beam) {
            let direction = normalize_vector([
                emitter.x_m / half_width * 0.48,
                emitter.y_m / half_height * 0.34,
                1.0,
            ]);
            emitter.direction_x = direction[0];
            emitter.direction_y = direction[1];
            emitter.direction_z = direction[2];
        }
    }
}

fn normalize_vector(vector: [f32; 3]) -> [f32; 3] {
    let length = vector
        .iter()
        .map(|component| component * component)
        .sum::<f32>()
        .sqrt();
    if length <= f32::EPSILON {
        [0.0, 0.0, 1.0]
    } else {
        [vector[0] / length, vector[1] / length, vector[2] / length]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_ma_coordinates_to_the_stage_coordinate_system() {
        assert_eq!(
            ma_dimensions_to_stage([0.27, 0.22, 0.26]),
            [0.27, 0.26, 0.22]
        );
    }

    #[test]
    #[expect(
        clippy::too_many_lines,
        reason = "Check all related metadata from the same loaded fixture library"
    )]
    fn builtins_expose_documented_physical_metadata() {
        let library = GrandMa2Library::load(&[]).expect("built-ins should parse");
        assert_eq!(library.fixture_types().len(), 3);

        let muvy = library
            .get(PURELIGHT_MUVY_WASHQ_ID)
            .expect("MUVY fixture should exist");
        assert_eq!(muvy.footprint, 14);
        assert_eq!(muvy.visual.kind(), FixtureVisualKind::MovingHead);
        assert_eq!(muvy.visual.pan_min_degrees, -270.0);
        assert_eq!(muvy.visual.pan_max_degrees, 270.0);
        assert_eq!(muvy.visual.tilt_min_degrees, -135.0);
        assert_eq!(muvy.visual.tilt_max_degrees, 135.0);
        assert_eq!(muvy.visual.width_m, 0.27);
        assert_eq!(muvy.visual.height_m, 0.26);
        assert_eq!(muvy.visual.depth_m, 0.22);
        assert_eq!(muvy.visual.model_kind(), FixtureModelKind::HeadMover);
        assert_eq!(muvy.visual.emitters.len(), 7);
        assert!(muvy.visual.emitters[0].casts_beam);
        assert_eq!(muvy.visual.emitters[0].beam_angle_degrees, 45.0);
        assert_eq!(muvy.visual.emitters[0].beam_intensity, 0.0);
        approx::assert_abs_diff_eq!(muvy.visual.emitters[0].aperture_m, 0.104, epsilon = 0.0001);
        assert_eq!(muvy.warnings, [] as [String; 0]);
        let color_macro = muvy
            .channels
            .iter()
            .find(|channel| channel.coarse == 11)
            .expect("MUVY color macro channel");
        let macro_sets = &color_macro
            .functions
            .iter()
            .find(|function| function.semantic == DmxSemantic::ColorMacro)
            .expect("MUVY color macro function")
            .channel_sets;
        assert_eq!(macro_sets[0].name, "RGB");
        assert_eq!(macro_sets[4].name, "No function");
        assert_eq!(macro_sets[8].name, "RGB");

        let derby = library
            .get(SHOWTEC_TECHNO_DERBY_ID)
            .expect("Techno Derby fixture should exist");
        assert_eq!(derby.footprint, 4);
        assert_eq!(derby.visual.emitters.len(), 20);
        assert_eq!(derby.visual.width_m, 0.265);
        assert_eq!(derby.visual.height_m, 0.19);
        assert_eq!(derby.visual.depth_m, 0.19);
        assert_eq!(derby.visual.model_kind(), FixtureModelKind::DerbyEffect);
        assert_eq!(
            derby
                .visual
                .emitters
                .iter()
                .filter(|emitter| emitter.casts_beam)
                .count(),
            4
        );
        assert!(
            derby
                .visual
                .emitters
                .iter()
                .all(|emitter| emitter.beam_intensity == 0.0)
        );
        assert!(
            derby
                .visual
                .emitters
                .iter()
                .filter(|emitter| emitter.casts_beam)
                .all(|emitter| emitter.beam_angle_degrees.to_bits() == 5.0_f32.to_bits())
        );
        assert!(
            derby
                .visual
                .emitters
                .iter()
                .filter(|emitter| emitter.kind == FixtureEmitterKind::Strobe as i32)
                .all(|emitter| !emitter.casts_beam)
        );
        assert_eq!(derby.warnings.len(), 1);
        assert!(derby.warnings[0].contains("schematic previews"));

        let lixada = library
            .get(LIXADA_MINI_BUTTERFLY_ID)
            .expect("Lixada fixture should exist");
        assert_eq!(lixada.footprint, 7);
        assert_eq!(lixada.visual.emitters.len(), 4);
        assert_eq!(lixada.visual.width_m, 0.2);
        assert_eq!(lixada.visual.height_m, 0.14);
        assert_eq!(lixada.visual.depth_m, 0.17);
        assert_eq!(lixada.visual.model_kind(), FixtureModelKind::CompactEffect);
        assert!(
            lixada
                .visual
                .emitters
                .iter()
                .all(|emitter| emitter.casts_beam
                    && emitter.direction_z > 0.85
                    && emitter.beam_angle_degrees.to_bits() == 5.0_f32.to_bits()
                    && emitter.beam_intensity == 0.0)
        );
        assert!(
            lixada
                .channels
                .iter()
                .filter(|channel| channel.has_semantic(DmxSemantic::CustomColor))
                .all(|channel| channel.color_hues.len() == 2)
        );
        assert_eq!(lixada.warnings.len(), 1);
        assert!(lixada.warnings[0].contains("preview estimates"));
        for fixture_type in library.fixture_types() {
            for channel in &fixture_type.channels {
                assert!(
                    channel
                        .functions
                        .windows(2)
                        .all(|pair| pair[0].to_dmx < pair[1].from_dmx),
                    "{} channel {} has overlapping functions",
                    fixture_type.name,
                    channel.coarse
                );
            }
        }
    }

    #[test]
    fn builtins_keep_fixture_modules_controllable_and_models_separate() {
        for xml in [
            include_str!("../fixtures/grandma2/purelight-muvy-washq-14ch.xml"),
            include_str!("../fixtures/grandma2/showtec-techno-derby-4ch.xml"),
            include_str!("../fixtures/grandma2/lixada-mini-butterfly-7ch.xml"),
        ] {
            let fixture_types = parse_xml(xml).expect("built-in fixture should parse");
            assert!(
                fixture_types
                    .iter()
                    .flat_map(|fixture_type| &fixture_type.modules)
                    .all(|module| !module.channels.is_empty())
            );
        }

        let library = GrandMa2Library::load(&[]).expect("built-ins should parse");
        let muvy = library
            .get(PURELIGHT_MUVY_WASHQ_ID)
            .expect("MUVY fixture should exist");
        let diameter =
            find_model_kind(&muvy.visual.model_nodes, FixtureModelNodeKind::BeamDiameter)
                .expect("MUVY model should include _XLD");
        let clip = find_model_kind(&muvy.visual.model_nodes, FixtureModelNodeKind::BeamClip)
            .expect("MUVY model should include _XLC");
        assert_eq!(diameter.emitter_id, muvy.visual.emitters[0].id);
        assert_eq!(clip.emitter_id, muvy.visual.emitters[0].id);
        assert_eq!(muvy.visual.emitters[0].model_node_id, diameter.id);
        approx::assert_abs_diff_eq!(diameter.beam_diameter_m, 0.104, epsilon = 0.0001);
        approx::assert_abs_diff_eq!(clip.beam_clipping_distance_m, 0.012, epsilon = 0.0001);
    }

    fn find_model_kind(
        nodes: &[FixtureModelNode],
        kind: FixtureModelNodeKind,
    ) -> Option<&FixtureModelNode> {
        nodes.iter().find_map(|node| {
            (node.kind() == kind)
                .then_some(node)
                .or_else(|| find_model_kind(&node.children, kind))
        })
    }

    #[test]
    fn preserves_reversed_physical_axes_and_zoom_ranges() {
        let xml = br#"<?xml version="1.0"?>
          <MA xmlns="http://schemas.malighting.de/grandma2/xml/MA">
            <FixtureType name="Reversed mover" mode="Default">
              <manufacturer>Example</manufacturer>
              <Modules>
                <Module index="0" name="Main" class="Headmover" beamtype="Wash" beam_angle="30">
                  <Body><Size x="0.2" y="0.2" z="0.3"/></Body>
                  <ChannelType attribute="PAN" feature="POSITION" coarse="1">
                    <ChannelFunction subattribute="PAN" attribute="PAN" feature="POSITION" min_dmx_24="0" max_dmx_24="16777215" physfrom="270" physto="-270"/>
                  </ChannelType>
                  <ChannelType attribute="ZOOM" feature="BEAM" coarse="2">
                    <ChannelFunction subattribute="ZOOM" attribute="ZOOM" feature="BEAM" min_dmx_24="0" max_dmx_24="16777215" physfrom="50" physto="10"/>
                  </ChannelType>
                </Module>
              </Modules>
            </FixtureType>
          </MA>"#;
        let parsed = import_fixture_file("reversed.xml", xml).expect("fixture should parse");
        let visual = &parsed.fixture_types[0].visual;
        assert_eq!(visual.pan_min_degrees, 270.0);
        assert_eq!(visual.pan_max_degrees, -270.0);
        assert_eq!(visual.zoom_physical_from_degrees, 50.0);
        assert_eq!(visual.zoom_physical_to_degrees, 10.0);
    }

    #[test]
    fn import_repairs_literal_angle_bracket_in_attribute() {
        let xml = br#"<?xml version="1.0"?>
          <MA xmlns="http://schemas.malighting.de/grandma2/xml/MA">
            <FixtureType name="Fixture" mode="Default">
              <manufacturer>Maker</manufacturer>
              <Modules><Module index="0" name="Main" class="Conventional">
                <ChannelType attribute="EFFECTMACROS" feature="EFFECTMACROS" coarse="1">
                  <ChannelFunction subattribute="EFFECTMACROSELECT" attribute="EFFECTMACROS" feature="EFFECTMACROS" min_dmx_24="0" max_dmx_24="16777215">
                    <ChannelSet name="Pattern <>" from_dmx="0" to_dmx="255"/>
                  </ChannelFunction>
                </ChannelType>
              </Module></Modules>
            </FixtureType>
          </MA>"#;
        let parsed = import_fixture_file("fixture.xml", xml).expect("file should be repaired");
        assert!(parsed.file.xml.contains("Pattern &lt;>"));
        assert!(
            parsed.fixture_types[0]
                .warnings
                .iter()
                .any(|warning| warning.contains("Repaired"))
        );
    }

    #[test]
    fn rejects_entity_declarations() {
        let xml = br#"<!DOCTYPE MA [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><MA/>"#;
        assert!(import_fixture_file("fixture.xml", xml).is_err());
    }

    #[test]
    fn rejects_non_finite_physical_metadata() {
        let xml = br#"<?xml version="1.0"?>
          <MA xmlns="http://schemas.malighting.de/grandma2/xml/MA">
            <FixtureType name="Broken" mode="Default">
              <Modules>
                <Module index="0" name="Main" beamtype="Wash" beam_angle="NaN">
                  <ChannelType attribute="DIM" feature="DIMMER" coarse="1"/>
                </Module>
              </Modules>
            </FixtureType>
          </MA>"#;
        assert!(import_fixture_file("broken.xml", xml).is_err());
    }

    #[test]
    fn expands_reusable_module_channels_from_instance_patches() {
        let xml = br#"<?xml version="1.0"?>
          <MA xmlns="http://schemas.malighting.de/grandma2/xml/MA">
            <FixtureType name="Pixel bar" mode="Two cells">
              <manufacturer>Example</manufacturer>
              <Modules>
                <Module index="7" name="Cell" class="Conventional" beamtype="Wash">
                  <ChannelType attribute="DIM" feature="DIMMER" coarse="1">
                    <ChannelFunction subattribute="DIM" attribute="DIM" feature="DIMMER" min_dmx_24="0" max_dmx_24="16777215"/>
                  </ChannelType>
                </Module>
              </Modules>
              <Instances>
                <Instance index="0" module_index="7" patch="1"/>
                <Instance index="1" module_index="7" patch="5"/>
              </Instances>
            </FixtureType>
          </MA>"#;
        let xml_text = std::str::from_utf8(xml).expect("test fixture is UTF-8");
        let overflowing_patch = xml_text.replace(r#"patch="5""#, r#"patch="4294967295""#);
        for (malformed, reason) in [
            (
                xml_text.replace(
                    r#"index="1" module_index="7""#,
                    r#"index="4294967295" module_index="7""#,
                ),
                "DMX instance number overflows",
            ),
            (
                overflowing_patch.replace(r#"coarse="1""#, r#"coarse="2""#),
                "coarse DMX address overflows",
            ),
            (
                overflowing_patch.replace(r#"coarse="1""#, r#"coarse="1" fine="2""#),
                "fine DMX address overflows",
            ),
        ] {
            let error = import_fixture_file("pixel-bar.xml", malformed.as_bytes())
                .map(|_| ())
                .expect_err("overflowing fixture input should be rejected");
            assert_eq!(error.root_cause().to_string(), reason);
        }

        let parsed = import_fixture_file("pixel-bar.xml", xml).expect("fixture should parse");
        let fixture_type = &parsed.fixture_types[0];
        assert_eq!(fixture_type.footprint, 5);
        assert_eq!(
            fixture_type
                .channels
                .iter()
                .map(|channel| channel.coarse)
                .collect::<Vec<_>>(),
            vec![1, 5]
        );
        assert_eq!(fixture_type.visual.kind(), FixtureVisualKind::Par);
        assert_eq!(fixture_type.visual.emitters.len(), 2);
    }

    #[test]
    fn ignores_virtual_and_uninstantiated_module_channels() {
        let xml = br#"<?xml version="1.0"?>
          <MA xmlns="http://schemas.malighting.de/grandma2/xml/MA">
            <FixtureType name="Virtual channels" mode="Default">
              <manufacturer>Example</manufacturer>
              <Modules>
                <Module index="0" name="Patched" class="Conventional" beamtype="Wash">
                  <ChannelType attribute="DIM" feature="DIMMER" coarse="1">
                    <ChannelFunction subattribute="DIM" attribute="DIM" feature="DIMMER" min_dmx_24="0" max_dmx_24="16777215"/>
                  </ChannelType>
                  <ChannelType attribute="VIRTUALDIM" feature="DIMMER">
                    <ChannelFunction subattribute="DIM" attribute="DIM" feature="DIMMER" min_dmx_24="0" max_dmx_24="16777215"/>
                  </ChannelType>
                </Module>
                <Module index="1" name="Unused" class="Conventional">
                  <ChannelType attribute="SHUTTER" feature="SHUTTER" coarse="1">
                    <ChannelFunction subattribute="STROBE" attribute="SHUTTER" feature="SHUTTER" min_dmx_24="0" max_dmx_24="16777215"/>
                  </ChannelType>
                </Module>
              </Modules>
              <Instances>
                <Instance index="0" module_index="0" patch="1"/>
              </Instances>
            </FixtureType>
          </MA>"#;
        let parsed = import_fixture_file("virtual.xml", xml).expect("fixture should parse");
        let fixture_type = &parsed.fixture_types[0];
        assert_eq!(fixture_type.channels.len(), 1);
        assert_eq!(fixture_type.channels[0].coarse, 1);
        assert!(
            fixture_type
                .warnings
                .iter()
                .any(|warning| warning.contains("Virtual channel"))
        );
        assert!(
            fixture_type
                .warnings
                .iter()
                .any(|warning| warning.contains("no Instance"))
        );
    }
}
