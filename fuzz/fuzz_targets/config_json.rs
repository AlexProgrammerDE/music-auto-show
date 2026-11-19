//! Feeds arbitrary text to the show configuration JSON loader.
//!
//! The first byte selects the `--simulate` flag. `parse_json` enforces the
//! 16 MiB configuration limit itself. Loading may fail, but it must not panic,
//! and a configuration it accepts must survive a save and reload unchanged.

#![no_main]

use libfuzzer_sys::fuzz_target;
use music_auto_show::config::{parse_json, to_json};

fuzz_target!(|data: &[u8]| {
    let Some((&flags, text)) = data.split_first() else {
        return;
    };
    let simulate = flags & 1 == 1;
    let Ok(text) = std::str::from_utf8(text) else {
        return;
    };
    let Ok(config) = parse_json(text, simulate) else {
        return;
    };
    let json = to_json(&config)
        .unwrap_or_else(|error| panic!("validated configuration does not serialize: {error}"));
    let saved = parse_json(&json, simulate)
        .unwrap_or_else(|error| panic!("saved configuration does not load: {error}"));
    assert_eq!(
        saved.as_proto(),
        config.as_proto(),
        "saved configuration changed on reload"
    );
});
