use std::collections::{HashMap, HashSet};

use anyhow::{Context, Result, bail};
use base64::{Engine, prelude::BASE64_STANDARD};
use quick_xml::{
    Reader, XmlVersion,
    events::{BytesStart, Event},
};
use sha2::{Digest, Sha256};

use crate::proto::v1::{
    FixtureEmitter, FixtureEmitterKind, FixtureMesh, FixtureModelKind, FixtureVisual,
    FixtureVisualKind, GrandMa2Channel, GrandMa2ChannelFunction, GrandMa2ChannelSet,
    GrandMa2FixtureFile, GrandMa2FixtureType,
};

pub const PURELIGHT_MUVY_WASHQ_ID: &str = "builtin:purelight-muvy-washq-14ch:0";
pub const SHOWTEC_TECHNO_DERBY_ID: &str = "builtin:showtec-techno-derby-4ch:0";
pub const LIXADA_MINI_BUTTERFLY_ID: &str = "builtin:lixada-mini-butterfly-7ch:0";

const MAX_FIXTURE_FILE_BYTES: usize = 2 * 1024 * 1024;
const MAX_MESH_VERTICES: usize = 100_000;
const MAX_MESH_INDICES: usize = 600_000;

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

pub fn import_fixture_file(filename: &str, bytes: &[u8]) -> Result<ParsedFixtureFile> {
    if bytes.is_empty() {
        bail!("grandMA2 fixture file is empty");
    }
    if bytes.len() > MAX_FIXTURE_FILE_BYTES {
        bail!("grandMA2 fixture file exceeds the {MAX_FIXTURE_FILE_BYTES}-byte limit");
    }
    let filename = sanitize_filename(filename)?;
    let original = std::str::from_utf8(bytes).context("grandMA2 fixture file is not UTF-8")?;
    let (xml, repaired) = sanitize_xml(original)?;
    let mut hasher = Sha256::new();
    hasher.update(xml.as_bytes());
    let digest = hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let file = GrandMa2FixtureFile {
        id: format!("ma2:{}", &digest[..20]),
        filename,
        xml,
    };
    let mut parsed = parse_fixture_file(&file, false)?;
    if repaired {
        for fixture_type in &mut parsed.fixture_types {
            fixture_type
                .warnings
                .push("Repaired an unescaped '<' character inside an XML attribute.".into());
        }
    }
    Ok(parsed)
}

fn sanitize_filename(filename: &str) -> Result<String> {
    let filename = filename
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or_default()
        .trim();
    if filename.is_empty() || filename.len() > 200 {
        bail!("grandMA2 fixture filename is invalid");
    }
    if !filename.to_ascii_lowercase().ends_with(".xml") {
        bail!("grandMA2 fixture filename must end in .xml");
    }
    Ok(filename.into())
}

fn sanitize_xml(xml: &str) -> Result<(String, bool)> {
    let upper = xml.to_ascii_uppercase();
    if upper.contains("<!DOCTYPE") || upper.contains("<!ENTITY") {
        bail!("grandMA2 fixture XML may not contain DTD or entity declarations");
    }

    let mut result = String::with_capacity(xml.len());
    let mut quote = None;
    let mut in_tag = false;
    let mut repaired = false;
    for character in xml.chars() {
        match (in_tag, quote, character) {
            (false, _, '<') => {
                in_tag = true;
                result.push(character);
            }
            (false, _, _) => result.push(character),
            (true, None, '"' | '\'') => {
                quote = Some(character);
                result.push(character);
            }
            (true, Some(active), current) if current == active => {
                quote = None;
                result.push(current);
            }
            (true, Some(_), '<') => {
                result.push_str("&lt;");
                repaired = true;
            }
            (true, None, '>') => {
                in_tag = false;
                result.push(character);
            }
            (true, _, _) => result.push(character),
        }
    }
    if quote.is_some() {
        bail!("grandMA2 fixture XML contains an unterminated attribute");
    }
    Ok((result, repaired))
}

fn parse_fixture_file(file: &GrandMa2FixtureFile, built_in: bool) -> Result<ParsedFixtureFile> {
    if file.id.trim().is_empty() {
        bail!("grandMA2 fixture file has no id");
    }
    if file.xml.len() > MAX_FIXTURE_FILE_BYTES {
        bail!("grandMA2 fixture file '{}' is too large", file.filename);
    }
    let (xml, _) = sanitize_xml(&file.xml)?;
    let raw_types = parse_xml(&xml)
        .with_context(|| format!("failed to parse grandMA2 fixture '{}'", file.filename))?;
    let mut fixture_types = Vec::with_capacity(raw_types.len());
    for (index, raw) in raw_types.into_iter().enumerate() {
        fixture_types.push(
            compile_fixture_type(raw, format!("{}:{index}", file.id), &file.id, built_in)
                .with_context(|| format!("invalid fixture type in '{}'", file.filename))?,
        );
    }
    Ok(ParsedFixtureFile {
        file: file.clone(),
        fixture_types,
    })
}

#[derive(Default)]
struct RawFixtureType {
    name: String,
    mode: String,
    manufacturer: String,
    revision: String,
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
    mesh: Option<RawMesh>,
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

#[derive(Default)]
struct RawMesh {
    name: String,
    vertices: String,
    faces: String,
    color_rgb: u32,
}

#[derive(Clone, Copy)]
enum TextCapture {
    Manufacturer,
    Revision,
    Vertices,
    Faces,
}

fn parse_xml(xml: &str) -> Result<Vec<RawFixtureType>> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(true);
    let mut fixture_types = Vec::new();
    let mut fixture = None::<RawFixtureType>;
    let mut module = None::<RawModule>;
    let mut channel = None::<RawChannel>;
    let mut function = None::<RawFunction>;
    let mut capture = None::<TextCapture>;
    let mut root_seen = false;

    loop {
        match reader.read_event()? {
            Event::Start(element) => {
                let name = local_name(&element);
                match name.as_str() {
                    "MA" => root_seen = true,
                    "FixtureType" => {
                        fixture = Some(RawFixtureType {
                            name: attr(&reader, &element, "name")?.unwrap_or_default(),
                            mode: attr(&reader, &element, "mode")?.unwrap_or_default(),
                            ..Default::default()
                        });
                    }
                    "manufacturer" if fixture.is_some() => {
                        capture = Some(TextCapture::Manufacturer)
                    }
                    "Info" if fixture.is_some() => capture = Some(TextCapture::Revision),
                    "Module" if fixture.is_some() => {
                        module = Some(raw_module(&reader, &element)?);
                    }
                    "Size" if module.is_some() => {
                        if let Some(module) = &mut module {
                            module.size = Some([
                                float_attr(&reader, &element, "x")?.unwrap_or_default(),
                                float_attr(&reader, &element, "y")?.unwrap_or_default(),
                                float_attr(&reader, &element, "z")?.unwrap_or_default(),
                            ]);
                        }
                    }
                    "ChannelType" if module.is_some() => {
                        channel = Some(raw_channel(&reader, &element)?);
                    }
                    "ChannelFunction" if channel.is_some() => {
                        function = Some(raw_function(&reader, &element)?);
                    }
                    "ChannelSet" if function.is_some() => {
                        if let Some(function) = &mut function {
                            function.sets.push(raw_channel_set(&reader, &element)?);
                        }
                    }
                    "Instance" if fixture.is_some() => {
                        if let Some(fixture) = &mut fixture {
                            fixture.instances.push(raw_instance(&reader, &element)?);
                        }
                    }
                    "Model" if module.is_some() => {
                        if attr(&reader, &element, "model_type")?.as_deref() == Some("Model")
                            && let Some(module) = &mut module
                        {
                            module.mesh = Some(RawMesh {
                                name: attr(&reader, &element, "name")?
                                    .unwrap_or_else(|| module.name.clone()),
                                color_rgb: 0x282828,
                                ..Default::default()
                            });
                        }
                    }
                    "Vertices" if module.as_ref().is_some_and(|module| module.mesh.is_some()) => {
                        capture = Some(TextCapture::Vertices);
                    }
                    "Faces" if module.as_ref().is_some_and(|module| module.mesh.is_some()) => {
                        capture = Some(TextCapture::Faces);
                    }
                    "DiffuseColor"
                        if module.as_ref().is_some_and(|module| module.mesh.is_some()) =>
                    {
                        if let Some(mesh) = module.as_mut().and_then(|module| module.mesh.as_mut())
                        {
                            mesh.color_rgb = rgb_from_floats(
                                float_attr(&reader, &element, "r")?.unwrap_or(0.16),
                                float_attr(&reader, &element, "g")?.unwrap_or(0.16),
                                float_attr(&reader, &element, "b")?.unwrap_or(0.16),
                            );
                        }
                    }
                    _ => {}
                }
            }
            Event::Empty(element) => {
                let name = local_name(&element);
                match name.as_str() {
                    "Size" if module.is_some() => {
                        if let Some(module) = &mut module {
                            module.size = Some([
                                float_attr(&reader, &element, "x")?.unwrap_or_default(),
                                float_attr(&reader, &element, "y")?.unwrap_or_default(),
                                float_attr(&reader, &element, "z")?.unwrap_or_default(),
                            ]);
                        }
                    }
                    "ChannelFunction" if channel.is_some() => {
                        if let Some(channel) = &mut channel {
                            channel.functions.push(raw_function(&reader, &element)?);
                        }
                    }
                    "ChannelSet" if function.is_some() => {
                        if let Some(function) = &mut function {
                            function.sets.push(raw_channel_set(&reader, &element)?);
                        }
                    }
                    "Instance" if fixture.is_some() => {
                        if let Some(fixture) = &mut fixture {
                            fixture.instances.push(raw_instance(&reader, &element)?);
                        }
                    }
                    "DiffuseColor"
                        if module.as_ref().is_some_and(|module| module.mesh.is_some()) =>
                    {
                        if let Some(mesh) = module.as_mut().and_then(|module| module.mesh.as_mut())
                        {
                            mesh.color_rgb = rgb_from_floats(
                                float_attr(&reader, &element, "r")?.unwrap_or(0.16),
                                float_attr(&reader, &element, "g")?.unwrap_or(0.16),
                                float_attr(&reader, &element, "b")?.unwrap_or(0.16),
                            );
                        }
                    }
                    _ => {}
                }
            }
            Event::Text(text) => {
                let value = text.decode()?.into_owned();
                match capture {
                    Some(TextCapture::Manufacturer) => {
                        if let Some(fixture) = &mut fixture {
                            fixture.manufacturer = value;
                        }
                    }
                    Some(TextCapture::Revision) => {
                        if let Some(fixture) = &mut fixture
                            && !value.trim().is_empty()
                        {
                            fixture.revision = value;
                        }
                    }
                    Some(TextCapture::Vertices) => {
                        if let Some(mesh) = module.as_mut().and_then(|module| module.mesh.as_mut())
                        {
                            mesh.vertices.push_str(&value);
                        }
                    }
                    Some(TextCapture::Faces) => {
                        if let Some(mesh) = module.as_mut().and_then(|module| module.mesh.as_mut())
                        {
                            mesh.faces.push_str(&value);
                        }
                    }
                    None => {}
                }
            }
            Event::End(element) => {
                let name = String::from_utf8_lossy(element.local_name().as_ref()).into_owned();
                match name.as_str() {
                    "manufacturer" | "Info" | "Vertices" | "Faces" => capture = None,
                    "ChannelFunction" => {
                        if let (Some(channel), Some(function)) = (&mut channel, function.take()) {
                            channel.functions.push(function);
                        }
                    }
                    "ChannelType" => {
                        if let (Some(module), Some(channel)) = (&mut module, channel.take()) {
                            module.channels.push(channel);
                        }
                    }
                    "Module" => {
                        if let (Some(fixture), Some(module)) = (&mut fixture, module.take()) {
                            fixture.modules.push(module);
                        }
                    }
                    "FixtureType" => {
                        if let Some(fixture) = fixture.take() {
                            fixture_types.push(fixture);
                        }
                    }
                    _ => {}
                }
            }
            Event::DocType(_) => bail!("DTD declarations are not supported"),
            Event::Eof => break,
            _ => {}
        }
    }

    if !root_seen {
        bail!("root element is not an MA document");
    }
    if fixture_types.is_empty() {
        bail!("document contains no FixtureType");
    }
    Ok(fixture_types)
}

fn local_name(element: &BytesStart<'_>) -> String {
    String::from_utf8_lossy(element.local_name().as_ref()).into_owned()
}

fn attr(reader: &Reader<&[u8]>, element: &BytesStart<'_>, key: &str) -> Result<Option<String>> {
    for attribute in element.attributes().with_checks(false) {
        let attribute = attribute?;
        if attribute.key.local_name().as_ref() == key.as_bytes() {
            return Ok(Some(
                attribute
                    .decoded_and_normalized_value(XmlVersion::Implicit1_0, reader.decoder())?
                    .into_owned(),
            ));
        }
    }
    Ok(None)
}

fn uint_attr(reader: &Reader<&[u8]>, element: &BytesStart<'_>, key: &str) -> Result<Option<u32>> {
    attr(reader, element, key)?
        .map(|value| {
            value
                .parse()
                .with_context(|| format!("attribute '{key}' must be an unsigned integer"))
        })
        .transpose()
}

fn float_attr(reader: &Reader<&[u8]>, element: &BytesStart<'_>, key: &str) -> Result<Option<f32>> {
    attr(reader, element, key)?
        .map(|value| {
            let parsed = value
                .parse()
                .with_context(|| format!("attribute '{key}' must be a number"))?;
            if !f32::is_finite(parsed) {
                bail!("attribute '{key}' must be finite");
            }
            Ok(parsed)
        })
        .transpose()
}

fn raw_module(reader: &Reader<&[u8]>, element: &BytesStart<'_>) -> Result<RawModule> {
    Ok(RawModule {
        index: uint_attr(reader, element, "index")?.unwrap_or_default(),
        name: attr(reader, element, "name")?.unwrap_or_default(),
        class: attr(reader, element, "class")?.unwrap_or_default(),
        beam_type: attr(reader, element, "beamtype")?.unwrap_or_default(),
        beam_angle: float_attr(reader, element, "beam_angle")?.unwrap_or_default(),
        beam_intensity: float_attr(reader, element, "beam_intensity")?.unwrap_or_default(),
        ..Default::default()
    })
}

fn raw_channel(reader: &Reader<&[u8]>, element: &BytesStart<'_>) -> Result<RawChannel> {
    Ok(RawChannel {
        coarse: uint_attr(reader, element, "coarse")?.unwrap_or_default(),
        fine: uint_attr(reader, element, "fine")?.filter(|fine| *fine > 0),
        attribute: attr(reader, element, "attribute")?.unwrap_or_default(),
        feature: attr(reader, element, "feature")?.unwrap_or_default(),
        default_value: float_attr(reader, element, "default")?.unwrap_or_default(),
        ..Default::default()
    })
}

fn raw_function(reader: &Reader<&[u8]>, element: &BytesStart<'_>) -> Result<RawFunction> {
    Ok(RawFunction {
        name: attr(reader, element, "subattribute_user_name")?
            .or(attr(reader, element, "attribute_user_name")?)
            .unwrap_or_default(),
        attribute: attr(reader, element, "attribute")?.unwrap_or_default(),
        feature: attr(reader, element, "feature")?.unwrap_or_default(),
        subattribute: attr(reader, element, "subattribute")?.unwrap_or_default(),
        min_dmx_24: uint_attr(reader, element, "min_dmx_24")?,
        max_dmx_24: uint_attr(reader, element, "max_dmx_24")?,
        physical_from: float_attr(reader, element, "physfrom")?.unwrap_or_default(),
        physical_to: float_attr(reader, element, "physto")?.unwrap_or_default(),
        ..Default::default()
    })
}

fn raw_channel_set(reader: &Reader<&[u8]>, element: &BytesStart<'_>) -> Result<RawChannelSet> {
    let from_dmx = uint_attr(reader, element, "from_dmx")?.unwrap_or_default();
    Ok(RawChannelSet {
        name: attr(reader, element, "name")?.unwrap_or_default(),
        from_dmx,
        to_dmx: uint_attr(reader, element, "to_dmx")?.unwrap_or(from_dmx),
    })
}

fn raw_instance(reader: &Reader<&[u8]>, element: &BytesStart<'_>) -> Result<RawInstance> {
    Ok(RawInstance {
        index: uint_attr(reader, element, "index")?.unwrap_or_default(),
        module_index: uint_attr(reader, element, "module_index")?.unwrap_or_default(),
        patch: uint_attr(reader, element, "patch")?.unwrap_or(1),
    })
}

fn compile_fixture_type(
    raw: RawFixtureType,
    id: String,
    file_id: &str,
    built_in: bool,
) -> Result<ParsedFixtureType> {
    if raw.name.trim().is_empty() {
        bail!("FixtureType has no name");
    }
    let mut warnings = Vec::new();
    let mut channels = Vec::new();
    let mut occupied = HashSet::new();
    for module in &raw.modules {
        let module_instances = raw
            .instances
            .iter()
            .filter(|instance| instance.module_index == module.index)
            .collect::<Vec<_>>();
        let patches = if raw.instances.is_empty() {
            vec![(None, 1)]
        } else if module_instances.is_empty() {
            if !module.channels.is_empty() {
                warnings.push(format!(
                    "Module '{}' has DMX channels but no Instance and was ignored.",
                    module.name
                ));
            }
            continue;
        } else {
            module_instances
                .iter()
                .map(|instance| (Some(instance.index), instance.patch.max(1)))
                .collect()
        };
        for raw_channel in &module.channels {
            if raw_channel.coarse == 0 {
                warnings.push(format!(
                    "Virtual channel '{}' has no DMX address and was ignored.",
                    raw_channel.attribute
                ));
                continue;
            }
            for &(instance_index, patch) in &patches {
                let coarse = patch.saturating_add(raw_channel.coarse).saturating_sub(1);
                let fine = raw_channel
                    .fine
                    .map(|fine| patch.saturating_add(fine).saturating_sub(1));
                if coarse == 0 || coarse > 512 {
                    bail!(
                        "channel '{}' has invalid coarse address {}",
                        raw_channel.attribute,
                        coarse
                    );
                }
                if !occupied.insert(coarse) {
                    bail!("DMX address {coarse} is assigned more than once");
                }
                if let Some(fine) = fine
                    && (fine == 0 || fine > 512 || !occupied.insert(fine))
                {
                    bail!(
                        "channel '{}' has an invalid fine address",
                        raw_channel.attribute
                    );
                }
                let mut channel = compile_channel(raw_channel, &mut warnings);
                channel.coarse = coarse;
                channel.fine = fine;
                if let Some(instance_index) = instance_index
                    && module_instances.len() > 1
                {
                    channel.name = format!("{} {}", channel.name, instance_index + 1);
                }
                channels.push(channel);
            }
        }
    }
    channels.sort_by_key(|channel| channel.coarse);
    let footprint = channels
        .iter()
        .flat_map(|channel| [Some(channel.coarse), channel.fine])
        .flatten()
        .max()
        .unwrap_or_default();
    if footprint == 0 {
        bail!("FixtureType has no DMX channels");
    }

    let visual = compile_visual(&raw, &mut warnings);
    Ok(ParsedFixtureType {
        id,
        file_id: file_id.into(),
        name: raw.name,
        manufacturer: if raw.manufacturer.trim().is_empty() {
            warnings.push("The grandMA2 file does not identify a manufacturer.".into());
            "Unknown".into()
        } else {
            raw.manufacturer
        },
        mode: if raw.mode.trim().is_empty() {
            "Default".into()
        } else {
            raw.mode
        },
        footprint,
        built_in,
        warnings,
        channels,
        visual,
        revision: raw.revision,
    })
}

fn compile_channel(raw: &RawChannel, warnings: &mut Vec<String>) -> MappedChannel {
    let resolution_shift = if raw.fine.is_some() { 8 } else { 0 };
    let color_hues = color_hues(
        raw.functions
            .first()
            .map_or(raw.attribute.as_str(), |function| function.name.as_str()),
    );
    let mut functions = raw
        .functions
        .iter()
        .map(|function| {
            let sets = function
                .sets
                .iter()
                .map(|set| MappedChannelSet {
                    name: set.name.clone(),
                    from_dmx: (set.from_dmx >> resolution_shift).min(255),
                    to_dmx: (set.to_dmx >> resolution_shift).min(255),
                })
                .collect::<Vec<_>>();
            let from_dmx = function
                .min_dmx_24
                .map(|value| value >> 16)
                .or_else(|| sets.iter().map(|set| set.from_dmx).min())
                .unwrap_or_default()
                .min(255);
            let to_dmx = function
                .max_dmx_24
                .map(|value| value >> 16)
                .or_else(|| sets.iter().map(|set| set.to_dmx).max())
                .unwrap_or(255)
                .min(255);
            let semantic = classify_function(raw, function, !color_hues.is_empty());
            MappedFunction {
                name: if function.name.is_empty() {
                    function.subattribute.clone()
                } else {
                    function.name.clone()
                },
                attribute: function.attribute.clone(),
                feature: function.feature.clone(),
                subattribute: function.subattribute.clone(),
                from_dmx,
                to_dmx: to_dmx.max(from_dmx),
                physical_from: function.physical_from,
                physical_to: function.physical_to,
                channel_sets: sets,
                semantic,
            }
        })
        .collect::<Vec<_>>();
    functions.sort_by_key(|function| function.from_dmx);
    for pair in functions.windows(2) {
        if pair[0].to_dmx >= pair[1].from_dmx {
            warnings.push(format!(
                "Channel {} ({}) has overlapping function ranges {}-{} and {}-{}.",
                raw.coarse,
                raw.attribute,
                pair[0].from_dmx,
                pair[0].to_dmx,
                pair[1].from_dmx,
                pair[1].to_dmx
            ));
        }
    }
    if functions.is_empty() {
        functions.push(MappedFunction {
            name: raw.attribute.clone(),
            attribute: raw.attribute.clone(),
            feature: raw.feature.clone(),
            subattribute: raw.attribute.clone(),
            from_dmx: 0,
            to_dmx: 255,
            physical_from: 0.0,
            physical_to: 1.0,
            channel_sets: Vec::new(),
            semantic: classify_token(&raw.attribute, &raw.feature, false, !color_hues.is_empty()),
        });
    }
    if functions.iter().all(|function| {
        matches!(
            function.semantic,
            DmxSemantic::NoFeature | DmxSemantic::Other
        )
    }) {
        warnings.push(format!(
            "Channel {} ({}) has no supported live semantic and will use its default value.",
            raw.coarse, raw.attribute
        ));
    }
    let default_value = if raw.fine.is_some() {
        (raw.default_value.round().max(0.0) as u32 >> 8).min(255)
    } else {
        raw.default_value.round().clamp(0.0, 255.0) as u32
    };
    MappedChannel {
        coarse: raw.coarse,
        fine: raw.fine,
        name: functions
            .iter()
            .find(|function| function.semantic != DmxSemantic::NoFeature)
            .map_or_else(|| raw.attribute.clone(), |function| function.name.clone()),
        attribute: raw.attribute.clone(),
        feature: raw.feature.clone(),
        default_value,
        functions,
        color_hues,
    }
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

    let emitter_modules = if raw.instances.is_empty() {
        raw.modules
            .iter()
            .enumerate()
            .map(|(index, module)| (index as u32, module))
            .collect::<Vec<_>>()
    } else {
        raw.instances
            .iter()
            .filter_map(|instance| {
                raw.modules
                    .iter()
                    .find(|module| module.index == instance.module_index)
                    .map(|module| (instance.index, module))
            })
            .collect::<Vec<_>>()
    };
    let mut emitters = emitter_modules
        .into_iter()
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
                    0xffffff
                } else {
                    0
                },
                direction_x: 0.0,
                direction_y: 0.0,
                direction_z: 1.0,
                casts_beam: normalized(&module.class) != "LED"
                    && normalized(&module.beam_type) != "NONE",
                aperture_m: module
                    .size
                    .map_or(0.04, |size| size[0].min(size[2]))
                    .clamp(0.005, 0.5),
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

    let mut meshes = Vec::new();
    for module in &raw.modules {
        if let Some(raw_mesh) = &module.mesh {
            match decode_mesh(raw_mesh, module.index) {
                Ok(mesh) => meshes.push(mesh),
                Err(error) => warnings.push(format!(
                    "Could not decode embedded model '{}': {error}",
                    raw_mesh.name
                )),
            }
        }
    }

    let (pan_min, pan_max) = physical_axis_range(raw, DmxSemantic::Pan).unwrap_or((0.0, 0.0));
    let (tilt_min, tilt_max) = physical_axis_range(raw, DmxSemantic::Tilt).unwrap_or((0.0, 0.0));
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
        meshes,
        model_kind: model_kind as i32,
    }
}

fn ma_dimensions_to_stage(dimensions: [f32; 3]) -> [f32; 3] {
    [dimensions[0], dimensions[2], dimensions[1]]
}

fn ma_vector_to_stage(vector: [f32; 3]) -> [f32; 3] {
    [vector[0], vector[2], -vector[1]]
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
            (classify_function(channel, function, false) == semantic).then_some((
                function.physical_from.min(function.physical_to),
                function.physical_from.max(function.physical_to),
            ))
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
        let columns = (indices.len() as f32).sqrt().ceil() as usize;
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

fn decode_mesh(raw: &RawMesh, module_index: u32) -> Result<FixtureMesh> {
    let vertices = BASE64_STANDARD
        .decode(raw.vertices.trim())
        .context("vertices are not valid base64")?;
    let faces = BASE64_STANDARD
        .decode(raw.faces.trim())
        .context("faces are not valid base64")?;
    if vertices.len() < 4 || faces.len() < 4 {
        bail!(
            "mesh data is truncated ({} vertex bytes, {} face bytes)",
            vertices.len(),
            faces.len()
        );
    }
    let vertex_count = u32::from_le_bytes(vertices[0..4].try_into()?) as usize;
    let index_count = u32::from_le_bytes(faces[0..4].try_into()?) as usize;
    if vertex_count < 3 || index_count < 3 || index_count % 3 != 0 {
        bail!("mesh does not contain complete triangles");
    }
    if vertex_count > MAX_MESH_VERTICES || index_count > MAX_MESH_INDICES {
        bail!("mesh exceeds preview limits");
    }
    if vertices.len() != 4 + vertex_count * 32 {
        bail!("vertex buffer has an unsupported layout");
    }
    if faces.len() != 4 + index_count * 2 {
        bail!("face buffer has an unsupported layout");
    }

    let mut positions = Vec::with_capacity(vertex_count * 3);
    let mut normals = Vec::with_capacity(vertex_count * 3);
    for vertex in vertices[4..].chunks_exact(32) {
        let mut ma_position = [0.0; 3];
        for (component, offset) in [0, 4, 8].into_iter().enumerate() {
            let value = f32::from_le_bytes(vertex[offset..offset + 4].try_into()?);
            if !value.is_finite() {
                bail!("mesh contains a non-finite vertex position");
            }
            ma_position[component] = value;
        }
        positions.extend(ma_vector_to_stage(ma_position));
        let mut ma_normal = [0.0; 3];
        for (component, offset) in [12, 16, 20].into_iter().enumerate() {
            let value = f32::from_le_bytes(vertex[offset..offset + 4].try_into()?);
            if !value.is_finite() {
                bail!("mesh contains a non-finite vertex normal");
            }
            ma_normal[component] = value;
        }
        normals.extend(ma_vector_to_stage(ma_normal));
    }
    let indices = faces[4..]
        .chunks_exact(2)
        .map(|bytes| u16::from_le_bytes([bytes[0], bytes[1]]) as u32)
        .collect::<Vec<_>>();
    if indices.iter().any(|index| *index as usize >= vertex_count) {
        bail!("mesh contains an out-of-range vertex index");
    }
    Ok(FixtureMesh {
        id: format!("module-{module_index}-mesh"),
        name: raw.name.clone(),
        vertices: positions,
        normals,
        indices,
        color_rgb: raw.color_rgb,
    })
}

fn rgb_from_floats(red: f32, green: f32, blue: f32) -> u32 {
    let component = |value: f32| (value.clamp(0.0, 1.0) * 255.0).round() as u32;
    (component(red) << 16) | (component(green) << 8) | component(blue)
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
        assert_eq!(ma_vector_to_stage([1.0, 2.0, 3.0]), [1.0, 3.0, -2.0]);
    }

    #[test]
    fn builtins_are_clean_and_expose_physical_metadata() {
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
        assert!(muvy.visual.emitters[0].casts_beam);
        assert_eq!(muvy.visual.emitters[0].beam_angle_degrees, 45.0);
        assert_eq!(muvy.visual.emitters[0].beam_intensity, 0.0);

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
                .all(|emitter| emitter.beam_angle_degrees == 0.0 && emitter.beam_intensity == 0.0)
        );
        assert!(
            derby
                .visual
                .emitters
                .iter()
                .filter(|emitter| emitter.kind == FixtureEmitterKind::Strobe as i32)
                .all(|emitter| !emitter.casts_beam)
        );

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
                    && emitter.beam_angle_degrees == 0.0
                    && emitter.beam_intensity == 0.0)
        );
        assert!(
            lixada
                .channels
                .iter()
                .filter(|channel| channel.has_semantic(DmxSemantic::CustomColor))
                .all(|channel| channel.color_hues.len() == 2)
        );
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
