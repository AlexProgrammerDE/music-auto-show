use std::{fmt::Write as _, fs, io::Write, path::Path, time::Duration};

use anyhow::{Context, Result, bail};
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;

const BEATNET_PLUS_CHECKPOINT_URL: &str = "https://raw.githubusercontent.com/mjhydri/BeatNet-Plus/bb90eb0a9065b101a4b4c4cb2b2061950266cb4b/src/BeatNetPlus/models/generic_weights.pt";
const BEATNET_PLUS_CHECKPOINT_SHA256: &str =
    "ed52f90e27ff9b5ef3c63f59c6d4b37366f60a21a48ea1d46d7c3e18d6f1977e";
const MAX_CHECKPOINT_BYTES: u64 = 4 * 1024 * 1024;
const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CheckpointProvision {
    Present,
    Downloaded,
}

pub async fn ensure_beatnet_checkpoint(path: impl AsRef<Path>) -> Result<CheckpointProvision> {
    let path = path.as_ref();
    if path.as_os_str().is_empty() {
        bail!("BeatNet+ checkpoint path is empty");
    }
    if path.is_file() {
        return Ok(CheckpointProvision::Present);
    }
    if path.exists() {
        bail!(
            "BeatNet+ checkpoint path exists but is not a file: {}",
            path.display()
        );
    }

    let path = path.to_owned();
    tokio::task::spawn_blocking(move || {
        download_checkpoint(
            &path,
            BEATNET_PLUS_CHECKPOINT_URL,
            BEATNET_PLUS_CHECKPOINT_SHA256,
        )
    })
    .await
    .context("BeatNet+ checkpoint download task failed")?
}

fn download_checkpoint(
    path: &Path,
    url: &str,
    expected_sha256: &str,
) -> Result<CheckpointProvision> {
    if path.is_file() {
        return Ok(CheckpointProvision::Present);
    }
    if path.exists() {
        bail!(
            "BeatNet+ checkpoint path exists but is not a file: {}",
            path.display()
        );
    }

    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent)
        .with_context(|| format!("failed to create checkpoint directory {}", parent.display()))?;

    let config = ureq::Agent::config_builder()
        .timeout_global(Some(DOWNLOAD_TIMEOUT))
        .build();
    let agent = ureq::Agent::new_with_config(config);
    let mut response = agent
        .get(url)
        .call()
        .with_context(|| format!("failed to download BeatNet+ checkpoint from {url}"))?;
    let bytes = response
        .body_mut()
        .with_config()
        .limit(MAX_CHECKPOINT_BYTES)
        .read_to_vec()
        .context("failed to read BeatNet+ checkpoint response")?;
    let actual_sha256 = sha256_hex(&bytes);
    if actual_sha256 != expected_sha256 {
        bail!(
            "BeatNet+ checkpoint checksum mismatch: expected {expected_sha256}, got {actual_sha256}"
        );
    }

    let mut temporary = NamedTempFile::new_in(parent)
        .with_context(|| format!("failed to create temporary file in {}", parent.display()))?;
    temporary
        .write_all(&bytes)
        .context("failed to write BeatNet+ checkpoint")?;
    temporary
        .as_file()
        .sync_all()
        .context("failed to flush BeatNet+ checkpoint")?;

    match temporary.persist_noclobber(path) {
        Ok(_) => Ok(CheckpointProvision::Downloaded),
        Err(error) if error.error.kind() == std::io::ErrorKind::AlreadyExists && path.is_file() => {
            Ok(CheckpointProvision::Present)
        }
        Err(error) => Err(error.error).with_context(|| {
            format!(
                "failed to install BeatNet+ checkpoint at {}",
                path.display()
            )
        }),
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut output = String::with_capacity(digest.len() * 2);
    for byte in digest {
        let result = write!(output, "{byte:02x}");
        debug_assert!(result.is_ok(), "writing to a String should be infallible");
    }
    output
}

#[cfg(test)]
mod tests {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        thread,
    };

    use super::*;

    #[test]
    fn downloads_verified_checkpoint_atomically() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let path = directory.path().join("models/beatnet-plus.pt");
        let body = b"verified checkpoint".to_vec();
        let expected_sha256 = sha256_hex(&body);
        let (url, server) = serve_once(body.clone());

        let provision =
            download_checkpoint(&path, &url, &expected_sha256).expect("checkpoint should download");

        server.join().expect("test server should stop");
        assert_eq!(provision, CheckpointProvision::Downloaded);
        assert_eq!(fs::read(path).expect("checkpoint should be readable"), body);
    }

    #[test]
    fn rejects_checkpoint_with_wrong_checksum() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let path = directory.path().join("models/beatnet-plus.pt");
        let (url, server) = serve_once(b"untrusted checkpoint".to_vec());

        let error = download_checkpoint(&path, &url, BEATNET_PLUS_CHECKPOINT_SHA256)
            .expect_err("checkpoint should be rejected");

        server.join().expect("test server should stop");
        assert!(error.to_string().contains("checksum mismatch"));
        assert!(!path.exists());
    }

    #[test]
    fn preserves_existing_checkpoint_without_requesting_download() {
        let directory = tempfile::tempdir().expect("temporary directory should be created");
        let path = directory.path().join("beatnet-plus.pt");
        fs::write(&path, b"custom checkpoint").expect("checkpoint should be written");

        let provision = download_checkpoint(
            &path,
            "http://127.0.0.1:1/should-not-be-requested",
            BEATNET_PLUS_CHECKPOINT_SHA256,
        )
        .expect("existing checkpoint should be preserved");

        assert_eq!(provision, CheckpointProvision::Present);
        assert_eq!(
            fs::read(path).expect("checkpoint should be readable"),
            b"custom checkpoint"
        );
    }

    fn serve_once(body: Vec<u8>) -> (String, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("test server should bind");
        let address = listener
            .local_addr()
            .expect("test server should have an address");
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("test request should connect");
            let mut request = [0_u8; 1_024];
            let _ = stream.read(&mut request);
            write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            )
            .expect("test response headers should be written");
            stream
                .write_all(&body)
                .expect("test response body should be written");
        });
        (format!("http://{address}/checkpoint.pt"), server)
    }
}
