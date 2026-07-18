use std::{net::SocketAddr, path::PathBuf, time::Duration};

use clap::{Parser, ValueHint};

const DEFAULT_LISTEN_ADDRESS: &str = "127.0.0.1:3000";
const DEFAULT_CONFIG_PATH: &str = "config.json";
const DEFAULT_SHUTDOWN_TIMEOUT_SECONDS: u64 = 10;

#[derive(Debug, Parser)]
#[command(version, about)]
pub struct Cli {
    /// Address used by both the bundled SPA and gRPC-Web API.
    #[arg(
        long,
        env = "MUSIC_AUTO_SHOW_LISTEN",
        default_value = DEFAULT_LISTEN_ADDRESS,
        value_name = "ADDRESS"
    )]
    pub listen: SocketAddr,

    /// Load and save the show configuration at this path.
    #[arg(
        long,
        env = "MUSIC_AUTO_SHOW_CONFIG",
        default_value = DEFAULT_CONFIG_PATH,
        value_hint = ValueHint::FilePath,
        value_name = "PATH"
    )]
    pub config: PathBuf,

    /// Use generated audio and an in-memory DMX interface.
    #[arg(long, env = "MUSIC_AUTO_SHOW_SIMULATE")]
    pub simulate: bool,

    /// Maximum time to wait for a graceful shutdown.
    #[arg(
        long = "shutdown-timeout",
        env = "MUSIC_AUTO_SHOW_SHUTDOWN_TIMEOUT",
        default_value_t = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
        value_parser = clap::value_parser!(u64).range(1..=300),
        value_name = "SECONDS"
    )]
    shutdown_timeout_seconds: u64,
}

impl Cli {
    pub fn shutdown_timeout(&self) -> Duration {
        Duration::from_secs(self.shutdown_timeout_seconds)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_runtime_defaults() {
        let cli = Cli::try_parse_from(["music-auto-show"]).expect("default CLI should parse");

        assert_eq!(
            cli.listen,
            DEFAULT_LISTEN_ADDRESS
                .parse::<SocketAddr>()
                .expect("default address should be valid")
        );
        assert_eq!(cli.config, PathBuf::from(DEFAULT_CONFIG_PATH));
        assert!(!cli.simulate);
        assert_eq!(
            cli.shutdown_timeout(),
            Duration::from_secs(DEFAULT_SHUTDOWN_TIMEOUT_SECONDS)
        );
    }

    #[test]
    fn parses_explicit_runtime_options() {
        let cli = Cli::try_parse_from([
            "music-auto-show",
            "--listen",
            "0.0.0.0:8080",
            "--config",
            "/tmp/show.json",
            "--simulate",
            "--shutdown-timeout",
            "30",
        ])
        .expect("explicit CLI should parse");

        assert_eq!(cli.listen, "0.0.0.0:8080".parse().unwrap());
        assert_eq!(cli.config, PathBuf::from("/tmp/show.json"));
        assert!(cli.simulate);
        assert_eq!(cli.shutdown_timeout(), Duration::from_secs(30));
    }

    #[test]
    fn rejects_unbounded_shutdown_timeout() {
        let error = Cli::try_parse_from(["music-auto-show", "--shutdown-timeout", "301"])
            .expect_err("shutdown timeout should be bounded");

        assert!(error.to_string().contains("300"));
    }
}
