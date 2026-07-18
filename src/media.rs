//! Cross-platform now-playing metadata and album-art color extraction.

use std::{collections::HashMap, fs, time::Duration};

use image::imageops::FilterType;
use nowhear::{Artwork, MediaSource, MediaSourceBuilder, PlaybackState, PlayerInfo};
use tokio::sync::RwLock;
use tokio_util::sync::CancellationToken;
use tracing::{debug, warn};
use url::Url;

use crate::proto::v1::{MediaInfo, RgbColor};

pub async fn monitor(target: &RwLock<MediaInfo>, shutdown: CancellationToken) {
    let source = match tokio::select! {
        biased;
        () = shutdown.cancelled() => return,
        result = MediaSourceBuilder::new().build() => result,
    } {
        Ok(source) => source,
        Err(error) => {
            warn!(%error, "system media integration is unavailable");
            return;
        }
    };
    let mut interval = tokio::time::interval(Duration::from_secs(2));
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut last_track_key = String::new();
    let mut cached_colors = Vec::new();

    loop {
        tokio::select! {
            biased;
            () = shutdown.cancelled() => return,
            _ = interval.tick() => {}
        }
        let player = tokio::select! {
            biased;
            () = shutdown.cancelled() => return,
            result = current_player(&source) => result,
        };
        match player {
            Ok(Some(player)) => {
                let track_key = player
                    .current_track
                    .as_ref()
                    .map_or_else(String::new, |track| {
                        format!(
                            "{}|{}|{}",
                            track.artist.join(", "),
                            track.title,
                            track.album.as_deref().unwrap_or_default()
                        )
                    });
                if track_key != last_track_key {
                    last_track_key = track_key;
                    let artwork = player
                        .current_track
                        .as_ref()
                        .and_then(|track| track.artwork.clone());
                    cached_colors = tokio::select! {
                        biased;
                        () = shutdown.cancelled() => return,
                        result = tokio::task::spawn_blocking(move || {
                            artwork
                                .as_ref()
                                .and_then(read_artwork)
                                .map_or_else(Vec::new, |bytes| extract_colors(&bytes, 5))
                        }) => result.unwrap_or_default(),
                    };
                }
                *target.write().await = player_to_proto(player, cached_colors.clone());
            }
            Ok(None) => *target.write().await = MediaInfo::default(),
            Err(error) => debug!(%error, "could not read active media session"),
        }
    }
}

async fn current_player(source: &impl MediaSource) -> nowhear::Result<Option<PlayerInfo>> {
    let players = source.list_players().await?;
    let mut fallback = None;
    for player_name in players {
        let player = match source.get_player(&player_name).await {
            Ok(player) => player,
            Err(error) => {
                debug!(%error, %player_name, "media player disappeared during polling");
                continue;
            }
        };
        if player.playback_state == PlaybackState::Playing {
            return Ok(Some(player));
        }
        if fallback.is_none() && player.current_track.is_some() {
            fallback = Some(player);
        }
    }
    Ok(fallback)
}

fn player_to_proto(player: PlayerInfo, album_colors: Vec<RgbColor>) -> MediaInfo {
    let (track_name, artist_name) = player.current_track.map_or_else(
        || (String::new(), String::new()),
        |track| (track.title, track.artist.join(", ")),
    );
    MediaInfo {
        track_name,
        artist_name,
        is_playing: player.playback_state == PlaybackState::Playing,
        album_colors,
    }
}

fn read_artwork(artwork: &Artwork) -> Option<Vec<u8>> {
    match artwork {
        Artwork::Bytes { data, .. } => Some(data.to_vec()),
        Artwork::Url { url } => {
            let parsed = Url::parse(url).ok()?;
            match parsed.scheme() {
                "file" => fs::read(parsed.to_file_path().ok()?).ok(),
                "http" | "https" => {
                    let mut response = ureq::get(url).call().ok()?;
                    response
                        .body_mut()
                        .with_config()
                        .limit(10_000_000)
                        .read_to_vec()
                        .ok()
                }
                _ => None,
            }
        }
    }
}

fn extract_colors(image_bytes: &[u8], count: usize) -> Vec<RgbColor> {
    let Ok(image) = image::load_from_memory(image_bytes) else {
        return Vec::new();
    };
    let pixels = image.resize_exact(100, 100, FilterType::Lanczos3).to_rgb8();
    let mut counts = HashMap::<(u8, u8, u8), usize>::new();
    for pixel in pixels.pixels() {
        let [red, green, blue] = pixel.0;
        let brightness = (u16::from(red) + u16::from(green) + u16::from(blue)) / 3;
        let spread = red.max(green).max(blue) - red.min(green).min(blue);
        if !(30..=240).contains(&brightness) || spread < 30 {
            continue;
        }
        *counts
            .entry(((red / 24) * 24, (green / 24) * 24, (blue / 24) * 24))
            .or_default() += 1;
    }
    if counts.is_empty() {
        for pixel in pixels.pixels() {
            let [red, green, blue] = pixel.0;
            *counts
                .entry(((red / 32) * 32, (green / 32) * 32, (blue / 32) * 32))
                .or_default() += 1;
        }
    }
    let mut ranked: Vec<_> = counts.into_iter().collect();
    ranked.sort_unstable_by_key(|entry| std::cmp::Reverse(entry.1));
    let mut selected: Vec<(u8, u8, u8)> = Vec::new();
    for (color, _) in ranked {
        let distinct = selected.iter().all(|existing| {
            u16::from(existing.0.abs_diff(color.0))
                + u16::from(existing.1.abs_diff(color.1))
                + u16::from(existing.2.abs_diff(color.2))
                >= 60
        });
        if distinct {
            selected.push(color);
            if selected.len() == count {
                break;
            }
        }
    }
    selected
        .into_iter()
        .map(|(red, green, blue)| RgbColor {
            red: red.into(),
            green: green.into(),
            blue: blue.into(),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_artwork_has_no_palette() {
        assert!(extract_colors(&[], 5).is_empty());
    }
}
