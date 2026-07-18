//! Cross-platform now-playing metadata and album-art color extraction.

use std::{
    collections::HashMap,
    fmt::Write,
    fs::File,
    io::{Cursor, Read},
    sync::Arc,
    time::Duration,
};

use image::{
    DynamicImage, ExtendedColorType, ImageReader, Limits, codecs::jpeg::JpegEncoder,
    imageops::FilterType,
};
use nowhear::{Artwork, MediaSource, MediaSourceBuilder, PlaybackState, PlayerInfo};
use sha2::{Digest, Sha256};
use tokio::sync::watch;
use tokio_util::sync::CancellationToken;
use tracing::{debug, warn};
use url::Url;

use crate::proto::v1::{MediaInfo, RgbColor};

const ARTWORK_MAX_EDGE: u32 = 256;
const ARTWORK_JPEG_QUALITY: u8 = 85;
const ARTWORK_MAX_SOURCE_EDGE: u32 = 8_192;
const ARTWORK_MAX_DECODE_BYTES: u64 = 64 * 1024 * 1024;
const ARTWORK_MAX_SOURCE_BYTES: usize = 10_000_000;

#[derive(Clone)]
struct ArtworkImage {
    revision: String,
    bytes: Arc<[u8]>,
}

#[derive(Default)]
pub struct MediaState {
    info: MediaInfo,
    artwork: Option<ArtworkImage>,
}

impl MediaState {
    pub fn info(&self) -> &MediaInfo {
        &self.info
    }

    pub fn artwork(&self, revision: &str) -> Option<Arc<[u8]>> {
        self.artwork
            .as_ref()
            .filter(|artwork| artwork.revision == revision)
            .map(|artwork| Arc::clone(&artwork.bytes))
    }
}

#[derive(Default)]
struct ProcessedArtwork {
    colors: Vec<RgbColor>,
    image: Option<ArtworkImage>,
}

pub async fn monitor(target: watch::Sender<Arc<MediaState>>, shutdown: CancellationToken) {
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
    let mut cached_artwork = None;

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
                let artwork = player
                    .current_track
                    .as_ref()
                    .and_then(|track| track.artwork.as_ref())
                    .filter(|artwork| {
                        !matches!(artwork, Artwork::Bytes { data, .. } if data.len() > ARTWORK_MAX_SOURCE_BYTES)
                    })
                    .cloned();
                let track_key = player
                    .current_track
                    .as_ref()
                    .map_or_else(String::new, |track| {
                        format!(
                            "{}|{}|{}|{}",
                            track.artist.join(", "),
                            track.title,
                            track.album.as_deref().unwrap_or_default(),
                            artwork
                                .as_ref()
                                .map_or_else(String::new, artwork_source_key),
                        )
                    });
                if track_key != last_track_key {
                    last_track_key = track_key;
                    let processed = tokio::select! {
                        biased;
                        () = shutdown.cancelled() => return,
                        result = tokio::task::spawn_blocking(move || {
                            artwork
                                .as_ref()
                                .and_then(read_artwork)
                                .map_or_else(ProcessedArtwork::default, |bytes| {
                                    process_artwork(&bytes, 5)
                                })
                        }) => result.unwrap_or_default(),
                    };
                    cached_colors = processed.colors;
                    cached_artwork = processed.image;
                }
                let info = player_to_proto(
                    player,
                    cached_colors.clone(),
                    cached_artwork
                        .as_ref()
                        .map(|artwork| format!("/media/artwork/{}", artwork.revision)),
                );
                target.send_replace(Arc::new(MediaState {
                    info,
                    artwork: cached_artwork.clone(),
                }));
            }
            Ok(None) => {
                target.send_replace(Arc::new(MediaState::default()));
            }
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

fn player_to_proto(
    player: PlayerInfo,
    album_colors: Vec<RgbColor>,
    artwork_url: Option<String>,
) -> MediaInfo {
    let (track_name, artist_name) = player.current_track.map_or_else(
        || (String::new(), String::new()),
        |track| (track.title, track.artist.join(", ")),
    );
    MediaInfo {
        track_name,
        artist_name,
        is_playing: player.playback_state == PlaybackState::Playing,
        album_colors,
        artwork_url: artwork_url.unwrap_or_default(),
    }
}

fn artwork_source_key(artwork: &Artwork) -> String {
    match artwork {
        Artwork::Url { url } => url.clone(),
        Artwork::Bytes { data, .. } if data.len() <= ARTWORK_MAX_SOURCE_BYTES => {
            artwork_revision(data)
        }
        Artwork::Bytes { data, .. } => format!("oversized:{}", data.len()),
    }
}

fn read_artwork(artwork: &Artwork) -> Option<Vec<u8>> {
    match artwork {
        Artwork::Bytes { data, .. } => {
            (data.len() <= ARTWORK_MAX_SOURCE_BYTES).then(|| data.to_vec())
        }
        Artwork::Url { url } => {
            let parsed = Url::parse(url).ok()?;
            match parsed.scheme() {
                "file" => read_limited(File::open(parsed.to_file_path().ok()?).ok()?),
                "http" | "https" => {
                    let mut response = ureq::get(url).call().ok()?;
                    let bytes = response
                        .body_mut()
                        .with_config()
                        .limit(ARTWORK_MAX_SOURCE_BYTES as u64 + 1)
                        .read_to_vec()
                        .ok()?;
                    (bytes.len() <= ARTWORK_MAX_SOURCE_BYTES).then_some(bytes)
                }
                _ => None,
            }
        }
    }
}

fn read_limited(reader: impl Read) -> Option<Vec<u8>> {
    let mut bytes = Vec::new();
    reader
        .take(ARTWORK_MAX_SOURCE_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .ok()?;
    (bytes.len() <= ARTWORK_MAX_SOURCE_BYTES).then_some(bytes)
}

fn process_artwork(image_bytes: &[u8], color_count: usize) -> ProcessedArtwork {
    let Ok(mut reader) = ImageReader::new(Cursor::new(image_bytes)).with_guessed_format() else {
        return ProcessedArtwork::default();
    };
    let mut limits = Limits::default();
    limits.max_image_width = Some(ARTWORK_MAX_SOURCE_EDGE);
    limits.max_image_height = Some(ARTWORK_MAX_SOURCE_EDGE);
    limits.max_alloc = Some(ARTWORK_MAX_DECODE_BYTES);
    reader.limits(limits);
    let Ok(image) = reader.decode() else {
        return ProcessedArtwork::default();
    };
    let colors = extract_colors(&image, color_count);
    let image = encode_artwork(&image).map(|bytes| ArtworkImage {
        revision: artwork_revision(&bytes),
        bytes: Arc::from(bytes),
    });
    ProcessedArtwork { colors, image }
}

fn encode_artwork(image: &DynamicImage) -> Option<Vec<u8>> {
    let image = image
        .resize(ARTWORK_MAX_EDGE, ARTWORK_MAX_EDGE, FilterType::Lanczos3)
        .to_rgb8();
    let mut bytes = Vec::new();
    JpegEncoder::new_with_quality(&mut bytes, ARTWORK_JPEG_QUALITY)
        .encode(
            image.as_raw(),
            image.width(),
            image.height(),
            ExtendedColorType::Rgb8,
        )
        .ok()?;
    Some(bytes)
}

fn artwork_revision(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .fold(String::with_capacity(64), |mut revision, byte| {
            let result = write!(revision, "{byte:02x}");
            debug_assert!(result.is_ok(), "writing to a String should be infallible");
            revision
        })
}

fn extract_colors(image: &DynamicImage, count: usize) -> Vec<RgbColor> {
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
    use image::{ImageBuffer, Rgb};

    #[test]
    fn empty_artwork_has_no_palette() {
        assert!(process_artwork(&[], 5).colors.is_empty());
    }

    #[test]
    fn artwork_is_resized_and_encoded_as_a_stable_jpeg() {
        let source = DynamicImage::ImageRgb8(ImageBuffer::from_fn(640, 320, |x, y| {
            Rgb([(x % 255) as u8, (y % 255) as u8, 128])
        }));
        let first = encode_artwork(&source).expect("artwork should encode");
        let second = encode_artwork(&source).expect("artwork should encode consistently");
        let decoded = image::load_from_memory(&first).expect("encoded artwork should decode");

        assert!(first.starts_with(&[0xff, 0xd8]));
        assert_eq!(decoded.width(), ARTWORK_MAX_EDGE);
        assert_eq!(decoded.height(), ARTWORK_MAX_EDGE / 2);
        assert_eq!(artwork_revision(&first), artwork_revision(&second));
    }

    #[test]
    fn media_state_only_serves_the_current_revision() {
        let bytes: Arc<[u8]> = Arc::from([1, 2, 3]);
        let state = MediaState {
            info: MediaInfo::default(),
            artwork: Some(ArtworkImage {
                revision: "current".into(),
                bytes: Arc::clone(&bytes),
            }),
        };

        assert_eq!(state.artwork("current").as_deref(), Some(bytes.as_ref()));
        assert!(state.artwork("stale").is_none());
    }
}
