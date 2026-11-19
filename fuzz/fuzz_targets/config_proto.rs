//! Round trip for a show configuration received over gRPC-Web.
//!
//! `UpdateConfig` decodes a protobuf `ShowConfig`, validates it, and saves it
//! as JSON. The first byte selects the `--simulate` flag and the rest is the
//! protobuf message. Every configuration that validation accepts must save and
//! load again unchanged, or the next start would fail.

#![no_main]

use libfuzzer_sys::fuzz_target;
use music_auto_show::{
    config::{ValidatedShowConfig, parse_json, to_json},
    proto::v1::ShowConfig,
};
use prost::Message;

fuzz_target!(|data: &[u8]| {
    let Some((&flags, message)) = data.split_first() else {
        return;
    };
    let simulate = flags & 1 == 1;
    let Ok(config) = ShowConfig::decode(message) else {
        return;
    };
    let Ok(config) = ValidatedShowConfig::new(config, simulate) else {
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
