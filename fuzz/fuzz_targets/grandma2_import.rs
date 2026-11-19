//! Feeds arbitrary bytes to the grandMA2 fixture upload parser.
//!
//! `import_fixture_file` enforces the 2 MiB upload limit itself, so the target
//! does not cap the input. Parsing may fail, but it must not panic, and every
//! accepted fixture type must stay inside one DMX universe.

#![no_main]

use libfuzzer_sys::fuzz_target;
use music_auto_show::grandma2::import_fixture_file;

fuzz_target!(|data: &[u8]| {
    let Ok(parsed) = import_fixture_file("fixture.xml", data) else {
        return;
    };
    assert!(
        !parsed.fixture_types.is_empty(),
        "accepted file has no fixture types"
    );
    for fixture_type in &parsed.fixture_types {
        assert!(
            (1..=512).contains(&fixture_type.footprint),
            "footprint {} is outside one DMX universe",
            fixture_type.footprint
        );
        for channel in &fixture_type.channels {
            for address in std::iter::once(channel.coarse).chain(channel.fine) {
                assert!(
                    (1..=fixture_type.footprint).contains(&address),
                    "channel address {address} is outside the footprint"
                );
            }
            assert!(
                channel.default_value <= 255,
                "default value exceeds one DMX step"
            );
            for function in &channel.functions {
                assert!(
                    function.from_dmx <= function.to_dmx && function.to_dmx <= 255,
                    "function range {}..={} is not a DMX range",
                    function.from_dmx,
                    function.to_dmx
                );
                for value in [-1.0, 0.0, 0.5, 1.0, 2.0, f32::NAN] {
                    let output = function.normalized_value(value);
                    assert!(
                        (function.from_dmx..=function.to_dmx).contains(&output),
                        "normalized output {output} leaves the function range"
                    );
                }
            }
        }
        drop(fixture_type.to_proto());
    }
});
