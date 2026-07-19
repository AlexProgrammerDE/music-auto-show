use anyhow::Result;
#[cfg(not(target_os = "linux"))]
use anyhow::bail;

use crate::cli::HotspotCommand;

#[cfg(target_os = "linux")]
mod platform {
    use std::process::Output;

    use anyhow::{Context, Result, bail};
    use rand::{Rng, distr::Alphanumeric};
    use tokio::process::Command;

    use super::HotspotCommand;

    const PROFILE_NAME: &str = "music-auto-show-hotspot";
    const DEFAULT_SSID: &str = "Music Auto Show";

    pub async fn execute(command: &HotspotCommand) -> Result<()> {
        ensure_network_manager().await?;

        match command {
            HotspotCommand::Enable {
                ssid,
                password,
                interface,
            } => enable(ssid.as_deref(), password.as_deref(), interface.as_deref()).await,
            HotspotCommand::Disable => disable().await,
            HotspotCommand::Status => status().await,
            HotspotCommand::Remove => remove().await,
        }
    }

    async fn enable(
        ssid: Option<&str>,
        password: Option<&str>,
        interface: Option<&str>,
    ) -> Result<()> {
        run_nmcli(&["radio", "wifi", "on"], "enable the Wi-Fi radio").await?;

        let exists = profile_exists().await?;
        let generated_password = (!exists && password.is_none()).then(generate_password);
        let effective_ssid = match (ssid, exists) {
            (Some(ssid), _) => Some(ssid),
            (None, false) => Some(DEFAULT_SSID),
            (None, true) => None,
        };
        let effective_password = password.or(generated_password.as_deref());

        if exists {
            validate_profile_type().await?;
        } else {
            create_profile(
                effective_ssid.unwrap_or(DEFAULT_SSID),
                effective_password.context("new hotspot profile is missing a password")?,
                interface,
            )
            .await?;
        }

        update_profile(effective_ssid, effective_password, interface).await?;
        activate_profile(interface).await?;

        let active_ssid = profile_value("802-11-wireless.ssid")
            .await?
            .unwrap_or_else(|| DEFAULT_SSID.to_owned());
        println!("Hotspot '{active_ssid}' is active.");
        if let Some(password) = generated_password {
            println!("Generated Wi-Fi password: {password}");
            println!("Store this password now. Music Auto Show will not print it again.");
        }
        if let Some(address) = active_ipv4_address().await? {
            println!("Open http://{address}:3000 after Music Auto Show is running.");
        } else {
            println!("Start Music Auto Show, then open port 3000 on the hotspot gateway address.");
        }

        Ok(())
    }

    async fn disable() -> Result<()> {
        if !profile_exists().await? {
            println!("The Music Auto Show hotspot profile is not installed.");
            return Ok(());
        }

        run_nmcli(
            &[
                "connection",
                "modify",
                "id",
                PROFILE_NAME,
                "connection.autoconnect",
                "no",
            ],
            "disable hotspot autoconnect",
        )
        .await?;

        if profile_is_active().await? {
            run_nmcli(
                &["connection", "down", "id", PROFILE_NAME],
                "stop the hotspot",
            )
            .await?;
        }

        println!("The Music Auto Show hotspot is disabled.");
        Ok(())
    }

    async fn status() -> Result<()> {
        if !profile_exists().await? {
            println!("Hotspot profile: not installed");
            return Ok(());
        }

        let ssid = profile_value("802-11-wireless.ssid")
            .await?
            .unwrap_or_else(|| "unknown".into());
        let autoconnect = profile_value("connection.autoconnect")
            .await?
            .unwrap_or_else(|| "unknown".into());
        let active = profile_is_active().await?;

        println!("Hotspot profile: installed");
        println!("SSID: {ssid}");
        println!("Active: {}", if active { "yes" } else { "no" });
        println!("Start at boot: {autoconnect}");
        if active && let Some(address) = active_ipv4_address().await? {
            println!("Web UI: http://{address}:3000");
        }
        Ok(())
    }

    async fn remove() -> Result<()> {
        if !profile_exists().await? {
            println!("The Music Auto Show hotspot profile is not installed.");
            return Ok(());
        }

        if profile_is_active().await? {
            run_nmcli(
                &["connection", "down", "id", PROFILE_NAME],
                "stop the hotspot",
            )
            .await?;
        }
        run_nmcli(
            &["connection", "delete", "id", PROFILE_NAME],
            "remove the hotspot profile",
        )
        .await?;
        println!("The Music Auto Show hotspot profile was removed.");
        Ok(())
    }

    async fn ensure_network_manager() -> Result<()> {
        let output = run_nmcli(
            &["--terse", "--fields", "RUNNING", "general"],
            "query NetworkManager",
        )
        .await?;
        if output.trim() != "running" {
            bail!("NetworkManager is not running");
        }
        Ok(())
    }

    async fn profile_exists() -> Result<bool> {
        let output = Command::new("nmcli")
            .args(["connection", "show", "id", PROFILE_NAME])
            .output()
            .await
            .context("failed to run nmcli; install and start NetworkManager first")?;

        if output.status.success() {
            return Ok(true);
        }
        if output.status.code() == Some(10) {
            return Ok(false);
        }
        Err(nmcli_error(output, "look up the hotspot profile"))
    }

    async fn profile_is_active() -> Result<bool> {
        let profiles = run_nmcli(
            &[
                "--terse",
                "--fields",
                "NAME",
                "connection",
                "show",
                "--active",
            ],
            "query active NetworkManager profiles",
        )
        .await?;
        Ok(profiles.lines().any(|profile| profile == PROFILE_NAME))
    }

    async fn validate_profile_type() -> Result<()> {
        let profile_type = profile_value("connection.type").await?.unwrap_or_default();
        if profile_type != "802-11-wireless" && profile_type != "wifi" {
            bail!(
                "NetworkManager profile '{PROFILE_NAME}' already exists but is not a Wi-Fi profile"
            );
        }
        Ok(())
    }

    async fn create_profile(ssid: &str, password: &str, interface: Option<&str>) -> Result<()> {
        let mut args = vec!["device", "wifi", "hotspot"];
        if let Some(interface) = interface {
            args.extend(["ifname", interface]);
        }
        args.extend([
            "con-name",
            PROFILE_NAME,
            "ssid",
            ssid,
            "band",
            "bg",
            "password",
            password,
        ]);
        run_nmcli(&args, "create the hotspot profile").await?;
        Ok(())
    }

    async fn update_profile(
        ssid: Option<&str>,
        password: Option<&str>,
        interface: Option<&str>,
    ) -> Result<()> {
        let mut args = vec![
            "connection",
            "modify",
            "id",
            PROFILE_NAME,
            "connection.autoconnect",
            "yes",
            "802-11-wireless.mode",
            "ap",
            "802-11-wireless.band",
            "bg",
            "ipv4.method",
            "shared",
            "ipv6.method",
            "disabled",
        ];
        if let Some(interface) = interface {
            args.extend(["connection.interface-name", interface]);
        }
        if let Some(ssid) = ssid {
            args.extend(["802-11-wireless.ssid", ssid]);
        }
        if let Some(password) = password {
            args.extend([
                "802-11-wireless-security.key-mgmt",
                "wpa-psk",
                "802-11-wireless-security.psk",
                password,
            ]);
        }
        run_nmcli(&args, "configure the hotspot profile").await?;
        Ok(())
    }

    async fn activate_profile(interface: Option<&str>) -> Result<()> {
        let mut args = vec!["connection", "up", "id", PROFILE_NAME];
        if let Some(interface) = interface {
            args.extend(["ifname", interface]);
        }
        run_nmcli(&args, "activate the hotspot profile").await?;
        Ok(())
    }

    async fn profile_value(field: &str) -> Result<Option<String>> {
        let output = run_nmcli(
            &[
                "--get-values",
                field,
                "connection",
                "show",
                "id",
                PROFILE_NAME,
            ],
            "read the hotspot profile",
        )
        .await?;
        let value = output.trim();
        Ok((!value.is_empty()).then(|| value.to_owned()))
    }

    async fn active_ipv4_address() -> Result<Option<String>> {
        let device = run_nmcli(
            &[
                "--get-values",
                "GENERAL.DEVICES",
                "connection",
                "show",
                "--active",
                "id",
                PROFILE_NAME,
            ],
            "find the hotspot interface",
        )
        .await?;
        let Some(device) = device.lines().find(|line| !line.trim().is_empty()) else {
            return Ok(None);
        };
        let addresses = run_nmcli(
            &[
                "--get-values",
                "IP4.ADDRESS",
                "device",
                "show",
                device.trim(),
            ],
            "find the hotspot address",
        )
        .await?;
        Ok(addresses
            .lines()
            .find_map(|address| address.split('/').next())
            .filter(|address| !address.is_empty())
            .map(str::to_owned))
    }

    async fn run_nmcli(args: &[&str], operation: &str) -> Result<String> {
        let output = Command::new("nmcli")
            .args(args)
            .output()
            .await
            .context("failed to run nmcli; install and start NetworkManager first")?;
        if !output.status.success() {
            return Err(nmcli_error(output, operation));
        }
        String::from_utf8(output.stdout).context("nmcli returned non-UTF-8 output")
    }

    fn nmcli_error(output: Output, operation: &str) -> anyhow::Error {
        let details = String::from_utf8_lossy(&output.stderr);
        let details = details.trim();
        if details.is_empty() {
            anyhow::anyhow!("failed to {operation}: nmcli exited with {}", output.status)
        } else {
            anyhow::anyhow!("failed to {operation}: {details}")
        }
    }

    fn generate_password() -> String {
        rand::rng()
            .sample_iter(Alphanumeric)
            .take(20)
            .map(char::from)
            .collect()
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn generated_password_is_a_valid_wpa_passphrase() {
            let password = generate_password();

            assert_eq!(password.len(), 20);
            assert!(password.is_ascii());
            assert!(password.bytes().all(|byte| byte.is_ascii_alphanumeric()));
        }
    }
}

pub async fn execute(command: &HotspotCommand) -> Result<()> {
    #[cfg(target_os = "linux")]
    {
        platform::execute(command).await
    }

    #[cfg(not(target_os = "linux"))]
    {
        let _ = command;
        bail!("Wi-Fi hotspot management is supported only on Linux with NetworkManager")
    }
}
