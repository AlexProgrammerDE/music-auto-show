#![cfg_attr(
    not(test),
    deny(
        clippy::expect_used,
        clippy::panic,
        clippy::unreachable,
        clippy::unwrap_used
    )
)]

pub mod api;
pub mod app;
pub mod assets;
pub mod audio;
pub mod beatnet;
pub mod bluetooth;
pub mod checkpoint;
pub mod config;
pub mod dmx;
pub mod effects;
pub mod media;
pub mod proto;
mod timing;
