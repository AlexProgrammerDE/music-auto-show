//! Feeds arbitrary files to the BeatNet+ checkpoint integrity checks.
//!
//! Users can replace `models/beatnet-plus.pt`, and a file that is present is
//! loaded without a SHA-256 check. Before candle parses the file, the loader
//! reads every ZIP entry to check its size and CRC-32. These checks must
//! reject any file without panicking or allocating without bound.
//!
//! The target stops before candle. candle-core 0.11 can still allocate without
//! bound on a crafted pickle with valid CRCs, and libFuzzer can fix up CRCs
//! from its comparison traces, so fuzzing past the checks would keep reporting
//! that known upstream issue.

#![no_main]

use libfuzzer_sys::fuzz_target;
use music_auto_show::beatnet::verify_checkpoint_archive_bytes;

fuzz_target!(|data: &[u8]| {
    // The loader rejects files over 64 MiB before these checks run, and
    // libFuzzer inputs stay far below that.
    let _ = verify_checkpoint_archive_bytes(data);
});
