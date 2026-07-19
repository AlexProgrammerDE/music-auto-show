use std::{net::SocketAddr, path::PathBuf, time::Duration};

use clap::{Parser, Subcommand, ValueHint};

const DEFAULT_LISTEN_ADDRESS: &str = "0.0.0.0:3000";
const DEFAULT_CONFIG_PATH: &str = "config.json";
const DEFAULT_SHUTDOWN_TIMEOUT_SECONDS: u64 = 10;

#[derive(Debug, Parser)]
#[command(version, about)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Option<CliCommand>,

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

#[derive(Debug, Subcommand)]
pub enum CliCommand {
    /// Manage the optional NetworkManager Wi-Fi hotspot.
    Hotspot {
        #[command(subcommand)]
        command: HotspotCommand,
    },
}

#[derive(Debug, Subcommand)]
pub enum HotspotCommand {
    /// Create or update the hotspot profile and start it.
    Enable {
        /// Wi-Fi network name. Existing profiles keep their current SSID when omitted.
        #[arg(
            long,
            env = "MUSIC_AUTO_SHOW_HOTSPOT_SSID",
            value_name = "SSID",
            value_parser = parse_hotspot_ssid
        )]
        ssid: Option<String>,

        /// WPA password. A strong password is generated for new profiles when omitted.
        #[arg(
            long,
            env = "MUSIC_AUTO_SHOW_HOTSPOT_PASSWORD",
            hide_env_values = true,
            value_name = "PASSWORD",
            value_parser = parse_hotspot_password
        )]
        password: Option<String>,

        /// Wi-Fi interface to use, such as wlan0. NetworkManager chooses when omitted.
        #[arg(
            long,
            env = "MUSIC_AUTO_SHOW_HOTSPOT_INTERFACE",
            value_name = "INTERFACE"
        )]
        interface: Option<String>,
    },

    /// Stop the hotspot and prevent it from starting automatically.
    Disable,

    /// Show whether the hotspot profile is installed and active.
    Status,

    /// Stop the hotspot and delete its NetworkManager profile.
    Remove,
}

fn parse_hotspot_ssid(value: &str) -> Result<String, String> {
    let byte_count = value.len();
    if byte_count == 0 || byte_count > 32 {
        return Err("SSID must contain between 1 and 32 bytes".into());
    }
    Ok(value.to_owned())
}

fn parse_hotspot_password(value: &str) -> Result<String, String> {
    let byte_count = value.len();
    let valid_passphrase = value.is_ascii() && (8..=63).contains(&byte_count);
    let valid_hex_key = byte_count == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit());
    if !valid_passphrase && !valid_hex_key {
        return Err(
            "password must be 8 to 63 ASCII characters, or exactly 64 hexadecimal characters"
                .into(),
        );
    }
    Ok(value.to_owned())
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

        assert!(cli.command.is_none());
        assert_eq!(
            cli.listen,
            "0.0.0.0:3000"
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

        assert_eq!(
            cli.listen,
            "0.0.0.0:8080"
                .parse::<SocketAddr>()
                .expect("explicit address should be valid")
        );
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

    #[test]
    fn parses_hotspot_enable_options() {
        let cli = Cli::try_parse_from([
            "music-auto-show",
            "hotspot",
            "enable",
            "--ssid",
            "Stage Lights",
            "--password",
            "correct-horse-battery-staple",
            "--interface",
            "wlan0",
        ])
        .expect("hotspot options should parse");

        let Some(CliCommand::Hotspot {
            command:
                HotspotCommand::Enable {
                    ssid,
                    password,
                    interface,
                },
        }) = cli.command
        else {
            panic!("expected the hotspot enable command");
        };
        assert_eq!(ssid.as_deref(), Some("Stage Lights"));
        assert_eq!(password.as_deref(), Some("correct-horse-battery-staple"));
        assert_eq!(interface.as_deref(), Some("wlan0"));
    }

    #[test]
    fn rejects_invalid_hotspot_credentials() {
        let short_password = Cli::try_parse_from([
            "music-auto-show",
            "hotspot",
            "enable",
            "--password",
            "short",
        ])
        .expect_err("short hotspot passwords should be rejected");
        assert!(short_password.to_string().contains("8 to 63"));

        let long_ssid = Cli::try_parse_from([
            "music-auto-show",
            "hotspot",
            "enable",
            "--ssid",
            "this-network-name-is-over-thirty-two-bytes",
        ])
        .expect_err("long SSIDs should be rejected");
        assert!(long_ssid.to_string().contains("1 and 32"));
    }
}
