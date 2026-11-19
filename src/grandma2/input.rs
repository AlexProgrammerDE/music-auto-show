#![warn(clippy::indexing_slicing, clippy::arithmetic_side_effects)]

use std::collections::HashSet;

use anyhow::{Context, Result, bail};
use quick_xml::{
    Reader, XmlVersion,
    events::{BytesStart, Event},
};

use super::{
    DmxSemantic, MAX_FIXTURE_FILE_BYTES, MappedChannel, MappedChannelSet, MappedFunction,
    ParsedFixtureFile, ParsedFixtureType, RawChannel, RawChannelSet, RawFixtureType, RawFunction,
    RawInstance, RawModule, apply_builtin_model, classify_function, classify_token, color_hues,
    compile_visual, normalized,
};
use crate::proto::v1::GrandMa2FixtureFile;

/// # Errors
/// Returns an error if the name or XML does not describe a supported fixture file.
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
    let digest = crate::checkpoint::sha256_hex(xml.as_bytes());
    let digest_prefix = digest
        .get(..20)
        .context("fixture SHA-256 digest has an invalid prefix")?;
    let file = GrandMa2FixtureFile {
        id: format!("ma2:{digest_prefix}"),
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
            _ => result.push(character),
        }
    }
    if quote.is_some() {
        bail!("grandMA2 fixture XML contains an unterminated attribute");
    }
    Ok((result, repaired))
}

pub(super) fn parse_fixture_file(
    file: &GrandMa2FixtureFile,
    built_in: bool,
) -> Result<ParsedFixtureFile> {
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

#[derive(Clone, Copy)]
enum TextCapture {
    Manufacturer,
    Revision,
    Warning,
}

#[expect(
    clippy::too_many_lines,
    reason = "Keep the XML events and parser state transitions in one match"
)]
pub(super) fn parse_xml(xml: &str) -> Result<Vec<RawFixtureType>> {
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
                        capture = Some(TextCapture::Manufacturer);
                    }
                    "Info" if fixture.is_some() => {
                        capture =
                            match normalized(&attr(&reader, &element, "type")?.unwrap_or_default())
                                .as_str()
                            {
                                "WARNING" => Some(TextCapture::Warning),
                                "REVISION" => Some(TextCapture::Revision),
                                _ => None,
                            }
                    }
                    "Module" if fixture.is_some() => {
                        module = Some(raw_module(&reader, &element)?);
                    }
                    "Size" if module.is_some() => {
                        if let Some(module) = &mut module {
                            module.size = Some(raw_vector(&reader, &element, [0.0; 3])?);
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
                    _ => {}
                }
            }
            Event::Empty(element) => {
                let name = local_name(&element);
                match name.as_str() {
                    "Size" if module.is_some() => {
                        if let Some(module) = &mut module {
                            module.size = Some(raw_vector(&reader, &element, [0.0; 3])?);
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
                    _ => {}
                }
            }
            Event::Text(text) => {
                let value = text.into_inner().into_owned();
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
                    Some(TextCapture::Warning) => {
                        if let Some(fixture) = &mut fixture
                            && !value.trim().is_empty()
                        {
                            fixture.warnings.push(value);
                        }
                    }
                    None => {}
                }
            }
            Event::End(element) => {
                let name = element.local_name().as_ref().to_owned();
                match name.as_str() {
                    "manufacturer" | "Info" => capture = None,
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
            Event::CData(_)
            | Event::Comment(_)
            | Event::Decl(_)
            | Event::PI(_)
            | Event::GeneralRef(_) => {}
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

fn raw_vector(
    reader: &Reader<&[u8]>,
    element: &BytesStart<'_>,
    [x, y, z]: [f32; 3],
) -> Result<[f32; 3]> {
    Ok([
        float_attr(reader, element, "x")?.unwrap_or(x),
        float_attr(reader, element, "y")?.unwrap_or(y),
        float_attr(reader, element, "z")?.unwrap_or(z),
    ])
}

fn local_name(element: &BytesStart<'_>) -> String {
    element.local_name().as_ref().to_owned()
}

fn attr(_reader: &Reader<&[u8]>, element: &BytesStart<'_>, key: &str) -> Result<Option<String>> {
    for attribute in element.attributes().with_checks(false) {
        let attribute = attribute?;
        if attribute.key.local_name().as_ref() == key {
            return Ok(Some(
                attribute
                    .normalized_value(XmlVersion::Implicit1_0)?
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

#[expect(
    clippy::too_many_lines,
    reason = "Keep fixture patch validation and the resulting channel mapping together"
)]
fn compile_fixture_type(
    raw: RawFixtureType,
    id: String,
    file_id: &str,
    built_in: bool,
) -> Result<ParsedFixtureType> {
    if raw.name.trim().is_empty() {
        bail!("FixtureType has no name");
    }
    let mut warnings = raw.warnings.clone();
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
                let offset = patch
                    .checked_sub(1)
                    .context("DMX patch must be one-based")?;
                let coarse = offset
                    .checked_add(raw_channel.coarse)
                    .context("coarse DMX address overflows")?;
                let fine = raw_channel
                    .fine
                    .map(|fine| {
                        offset
                            .checked_add(fine)
                            .context("fine DMX address overflows")
                    })
                    .transpose()?;
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
                    let number = instance_index
                        .checked_add(1)
                        .context("DMX instance number overflows")?;
                    channel.name = format!("{} {number}", channel.name);
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

    let mut visual = compile_visual(&raw, &mut warnings);
    if built_in {
        apply_builtin_model(&id, &mut visual);
    } else {
        warnings.push(
            "grandMA2 custom models are separate media-database assets; this fixture XML uses the generated class model in the preview."
                .into(),
        );
    }
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

#[expect(
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    reason = "Fixture defaults are rounded and bounded before conversion to coarse DMX steps"
)]
#[expect(
    clippy::too_many_lines,
    reason = "Keep the fixture channel mapping and its validation together"
)]
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
    for (first, second) in functions.iter().zip(functions.iter().skip(1)) {
        if first.to_dmx >= second.from_dmx {
            warnings.push(format!(
                "Channel {} ({}) has overlapping function ranges {}-{} and {}-{}.",
                raw.coarse,
                raw.attribute,
                first.from_dmx,
                first.to_dmx,
                second.from_dmx,
                second.to_dmx
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
        raw.default_value.round().clamp(0.0, 65_535.0) as u32 >> 8
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
