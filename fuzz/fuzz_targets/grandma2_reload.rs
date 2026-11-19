//! Round trip for an imported grandMA2 fixture file.
//!
//! The API stores an accepted upload in the show configuration, validates that
//! configuration, saves it as JSON, and loads it again on the next start. Each
//! step must accept the stored file and produce the same DMX mapping.

#![no_main]

use libfuzzer_sys::fuzz_target;
use music_auto_show::{
    config::{ValidatedShowConfig, default_show_config, parse_json, to_json},
    grandma2::import_fixture_file,
};

fuzz_target!(|data: &[u8]| {
    let Ok(imported) = import_fixture_file("fixture.xml", data) else {
        return;
    };
    let mut config = default_show_config(true);
    config.imported_fixture_files.push(imported.file.clone());
    let validated = ValidatedShowConfig::new(config, true)
        .unwrap_or_else(|error| panic!("imported fixture file is rejected on reload: {error}"));

    for expected in &imported.fixture_types {
        let reloaded = validated
            .grandma2()
            .get(&expected.id)
            .unwrap_or_else(|| panic!("fixture type {} is missing after reload", expected.id));
        assert_eq!(reloaded.footprint, expected.footprint, "footprint changed");
        assert_eq!(
            reloaded.channels.len(),
            expected.channels.len(),
            "channel count changed"
        );
        for (reloaded, expected) in reloaded.channels.iter().zip(&expected.channels) {
            assert_eq!(reloaded.coarse, expected.coarse, "coarse address changed");
            assert_eq!(reloaded.fine, expected.fine, "fine address changed");
            assert_eq!(
                reloaded.default_value, expected.default_value,
                "default changed"
            );
            assert_eq!(
                reloaded.functions.len(),
                expected.functions.len(),
                "function count changed"
            );
        }
    }

    let json = to_json(&validated)
        .unwrap_or_else(|error| panic!("validated configuration does not serialize: {error}"));
    let saved = parse_json(&json, true)
        .unwrap_or_else(|error| panic!("saved configuration does not load: {error}"));
    assert_eq!(
        saved.as_proto(),
        validated.as_proto(),
        "saved configuration changed on reload"
    );
});
