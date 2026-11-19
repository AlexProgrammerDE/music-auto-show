#![warn(clippy::indexing_slicing, clippy::arithmetic_side_effects)]

use std::{fs::File, io::Read, path::Path};

use super::{ConfigError, MAX_CONFIG_BYTES, ValidatedShowConfig, default_show_config};

/// # Errors
/// Returns an error if the file cannot be read or its configuration is invalid.
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
    let contents = String::from_utf8(bytes)?;
    parse_json(&contents, simulate).map_err(|error| match error {
        ConfigError::Invalid(message) => ConfigError::Invalid(format!(
            "invalid show configuration in {}: {message}",
            path.display()
        )),
        other @ (ConfigError::Io { .. }
        | ConfigError::InvalidJson(_)
        | ConfigError::InvalidSchema(_)
        | ConfigError::Serialization(_)
        | ConfigError::InvalidEnum { .. }
        | ConfigError::InvalidUtf8(_)) => other,
    })
}

/// # Errors
/// Returns an error if the JSON or the resulting configuration is invalid.
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
