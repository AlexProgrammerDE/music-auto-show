use anyhow::Result;

use crate::proto::v1::BluetoothReceiverStatus;

pub struct BluetoothReceiver {
    platform: platform::PlatformReceiver,
}

impl BluetoothReceiver {
    pub fn new() -> Self {
        Self {
            platform: platform::PlatformReceiver::new(),
        }
    }

    pub async fn status(&self) -> BluetoothReceiverStatus {
        self.platform.status().await
    }

    pub async fn set_pairing(
        &self,
        enabled: bool,
        timeout_seconds: u32,
    ) -> Result<BluetoothReceiverStatus> {
        self.platform
            .set_pairing(enabled, timeout_seconds.clamp(30, 600))
            .await
    }

    pub async fn connect(&self, device_id: &str) -> Result<BluetoothReceiverStatus> {
        self.platform.connect(device_id).await
    }

    pub async fn disconnect(&self, device_id: &str) -> Result<BluetoothReceiverStatus> {
        self.platform.disconnect(device_id).await
    }

    pub async fn forget(&self, device_id: &str) -> Result<BluetoothReceiverStatus> {
        self.platform.forget(device_id).await
    }
}

impl Default for BluetoothReceiver {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(target_os = "linux")]
mod platform {
    use std::str::FromStr;

    use anyhow::{Context, Result};
    use bluer::{
        Adapter, Address, Session,
        agent::{Agent, AgentHandle},
    };
    use tokio::sync::{Mutex, MutexGuard};

    use crate::proto::v1::{BluetoothReceiverDevice, BluetoothReceiverStatus};

    const A2DP_SINK_UUID_PREFIX: &str = "0000110b";
    const A2DP_SOURCE_UUID_PREFIX: &str = "0000110a";

    pub struct PlatformReceiver {
        state: Mutex<Option<LinuxState>>,
    }

    struct LinuxState {
        session: Session,
        adapter: Adapter,
        pairing_session: Option<PairingSession>,
    }

    struct PairingSession {
        _agent: AgentHandle,
        previous_adapter_state: AdapterState,
    }

    struct AdapterState {
        powered: bool,
        pairable: bool,
        pairable_timeout: u32,
        discoverable: bool,
        discoverable_timeout: u32,
    }

    impl PlatformReceiver {
        pub fn new() -> Self {
            Self {
                state: Mutex::new(None),
            }
        }

        pub async fn status(&self) -> BluetoothReceiverStatus {
            match self.status_result().await {
                Ok(status) => status,
                Err(error) => BluetoothReceiverStatus {
                    supported: true,
                    platform: "Linux / BlueZ".into(),
                    status_message: "Bluetooth receiver is unavailable".into(),
                    last_error: format!("{error:#}"),
                    setup_hint: linux_setup_hint(),
                    ..Default::default()
                },
            }
        }

        pub async fn set_pairing(
            &self,
            enabled: bool,
            timeout_seconds: u32,
        ) -> Result<BluetoothReceiverStatus> {
            let mut state = self.initialized_state().await?;
            let state = state
                .as_mut()
                .context("Bluetooth receiver state was not initialized")?;
            if enabled {
                if state.pairing_session.is_none() {
                    let previous_adapter_state = AdapterState::capture(&state.adapter).await?;
                    let agent = state
                        .session
                        .register_agent(Agent {
                            request_default: true,
                            ..Default::default()
                        })
                        .await
                        .context("could not register the Bluetooth pairing agent")?;
                    state.pairing_session = Some(PairingSession {
                        _agent: agent,
                        previous_adapter_state,
                    });
                }
                if let Err(error) = enable_pairing(&state.adapter, timeout_seconds).await {
                    if let Some(pairing_session) = &state.pairing_session {
                        let _ = pairing_session
                            .previous_adapter_state
                            .restore(&state.adapter)
                            .await;
                    }
                    state.pairing_session = None;
                    return Err(error);
                }
            } else if let Some(pairing_session) = &state.pairing_session {
                pairing_session
                    .previous_adapter_state
                    .restore(&state.adapter)
                    .await?;
                state.pairing_session = None;
            } else {
                state.adapter.set_discoverable(false).await?;
                state.adapter.set_pairable(false).await?;
            }
            build_status(state).await
        }

        pub async fn connect(&self, device_id: &str) -> Result<BluetoothReceiverStatus> {
            let address = parse_address(device_id)?;
            let mut state = self.initialized_state().await?;
            let state = state
                .as_mut()
                .context("Bluetooth receiver state was not initialized")?;
            let device = state.adapter.device(address)?;
            if !device.is_paired().await? {
                let _temporary_agent = if state.pairing_session.is_none() {
                    Some(
                        state
                            .session
                            .register_agent(Agent::default())
                            .await
                            .context("could not register the Bluetooth pairing agent")?,
                    )
                } else {
                    None
                };
                device.pair().await?;
            }
            device.set_trusted(true).await?;
            if !device.is_connected().await? {
                device.connect().await?;
            }
            build_status(state).await
        }

        pub async fn disconnect(&self, device_id: &str) -> Result<BluetoothReceiverStatus> {
            let address = parse_address(device_id)?;
            let mut state = self.initialized_state().await?;
            let state = state
                .as_mut()
                .context("Bluetooth receiver state was not initialized")?;
            let device = state.adapter.device(address)?;
            if device.is_connected().await? {
                device.disconnect().await?;
            }
            build_status(state).await
        }

        pub async fn forget(&self, device_id: &str) -> Result<BluetoothReceiverStatus> {
            let address = parse_address(device_id)?;
            let mut state = self.initialized_state().await?;
            let state = state
                .as_mut()
                .context("Bluetooth receiver state was not initialized")?;
            state.adapter.remove_device(address).await?;
            build_status(state).await
        }

        async fn status_result(&self) -> Result<BluetoothReceiverStatus> {
            let mut state = self.initialized_state().await?;
            let state = state
                .as_mut()
                .context("Bluetooth receiver state was not initialized")?;
            build_status(state).await
        }

        async fn initialized_state(&self) -> Result<MutexGuard<'_, Option<LinuxState>>> {
            let mut state = self.state.lock().await;
            if state.is_none() {
                *state = Some(initialize().await?);
            }
            Ok(state)
        }
    }

    impl AdapterState {
        async fn capture(adapter: &Adapter) -> Result<Self> {
            Ok(Self {
                powered: adapter.is_powered().await?,
                pairable: adapter.is_pairable().await?,
                pairable_timeout: adapter.pairable_timeout().await?,
                discoverable: adapter.is_discoverable().await?,
                discoverable_timeout: adapter.discoverable_timeout().await?,
            })
        }

        async fn restore(&self, adapter: &Adapter) -> Result<()> {
            adapter.set_powered(true).await?;
            adapter.set_pairable_timeout(self.pairable_timeout).await?;
            adapter
                .set_discoverable_timeout(self.discoverable_timeout)
                .await?;
            adapter.set_pairable(self.pairable).await?;
            adapter.set_discoverable(self.discoverable).await?;
            adapter.set_powered(self.powered).await?;
            Ok(())
        }
    }

    async fn enable_pairing(adapter: &Adapter, timeout_seconds: u32) -> Result<()> {
        adapter.set_powered(true).await?;
        adapter.set_pairable_timeout(timeout_seconds).await?;
        adapter.set_discoverable_timeout(timeout_seconds).await?;
        adapter.set_pairable(true).await?;
        adapter.set_discoverable(true).await?;
        Ok(())
    }

    async fn initialize() -> Result<LinuxState> {
        let session = Session::new()
            .await
            .context("could not connect to the BlueZ system service")?;
        let adapter = session
            .default_adapter()
            .await
            .context("no system Bluetooth adapter is available")?;
        Ok(LinuxState {
            session,
            adapter,
            pairing_session: None,
        })
    }

    async fn build_status(state: &LinuxState) -> Result<BluetoothReceiverStatus> {
        let adapter = &state.adapter;
        let receiver_ready = adapter
            .uuids()
            .await?
            .unwrap_or_default()
            .iter()
            .any(|uuid| uuid.to_string().starts_with(A2DP_SINK_UUID_PREFIX));
        let mut devices = Vec::new();
        for address in adapter.device_addresses().await? {
            let device = adapter.device(address)?;
            let paired = device.is_paired().await.unwrap_or(false);
            let connected = device.is_connected().await.unwrap_or(false);
            if !paired && !connected {
                continue;
            }
            let name = device.alias().await.unwrap_or_else(|_| address.to_string());
            let audio_capable = device.uuids().await.ok().flatten().is_some_and(|uuids| {
                uuids
                    .iter()
                    .any(|uuid| uuid.to_string().starts_with(A2DP_SOURCE_UUID_PREFIX))
            });
            if !audio_capable {
                continue;
            }
            let trusted = device.is_trusted().await.unwrap_or(false);
            devices.push(BluetoothReceiverDevice {
                id: address.to_string(),
                name,
                paired,
                trusted,
                connected,
                audio_capable,
            });
        }
        devices.sort_by(|left, right| {
            right
                .connected
                .cmp(&left.connected)
                .then_with(|| left.name.to_lowercase().cmp(&right.name.to_lowercase()))
        });
        let powered = adapter.is_powered().await?;
        let discoverable = adapter.is_discoverable().await?;
        let pairable = adapter.is_pairable().await?;
        let status_message = if !receiver_ready {
            "A2DP receiver profile is missing"
        } else if devices.iter().any(|device| device.connected) {
            "Bluetooth audio source connected"
        } else if discoverable {
            "Ready to pair"
        } else {
            "Waiting for a paired device"
        };
        Ok(BluetoothReceiverStatus {
            supported: true,
            receiver_ready,
            platform: "Linux / BlueZ".into(),
            adapter_name: adapter.alias().await?,
            powered,
            discoverable,
            pairable,
            status_message: status_message.into(),
            last_error: String::new(),
            setup_hint: linux_setup_hint(),
            devices,
        })
    }

    fn parse_address(device_id: &str) -> Result<Address> {
        Address::from_str(device_id).context("Bluetooth device ID is not a valid address")
    }

    fn linux_setup_hint() -> String {
        "BlueZ and WirePlumber must be running with the PipeWire BlueZ monitor enabled. The service user also needs permission to control the system Bluetooth adapter.".into()
    }
}

#[cfg(target_os = "windows")]
mod platform {
    use std::collections::HashMap;

    use anyhow::{Context, Result, bail};
    use tokio::sync::Mutex;
    use windows::{
        Devices::Enumeration::{DeviceInformation, DeviceUnpairingResultStatus},
        Foundation::Uri,
        Media::Audio::{
            AudioPlaybackConnection, AudioPlaybackConnectionOpenResultStatus,
            AudioPlaybackConnectionState,
        },
        System::Launcher,
        core::HSTRING,
    };

    use crate::proto::v1::{BluetoothReceiverDevice, BluetoothReceiverStatus};

    pub struct PlatformReceiver {
        connections: Mutex<HashMap<String, AudioPlaybackConnection>>,
    }

    impl PlatformReceiver {
        pub fn new() -> Self {
            Self {
                connections: Mutex::new(HashMap::new()),
            }
        }

        pub async fn status(&self) -> BluetoothReceiverStatus {
            match self.status_result().await {
                Ok(status) => status,
                Err(error) => BluetoothReceiverStatus {
                    supported: true,
                    platform: "Windows / AudioPlaybackConnection".into(),
                    adapter_name: "Windows Bluetooth".into(),
                    status_message: "Bluetooth receiver is unavailable".into(),
                    last_error: format!("{error:#}"),
                    setup_hint: windows_setup_hint(),
                    ..Default::default()
                },
            }
        }

        pub async fn set_pairing(
            &self,
            enabled: bool,
            _timeout_seconds: u32,
        ) -> Result<BluetoothReceiverStatus> {
            if enabled {
                let uri = Uri::CreateUri(&HSTRING::from("ms-settings:bluetooth"))?;
                let launched = Launcher::LaunchUriAsync(&uri)?.await?;
                if !launched {
                    bail!("Windows could not open Bluetooth settings");
                }
            }
            self.status_result().await
        }

        pub async fn connect(&self, device_id: &str) -> Result<BluetoothReceiverStatus> {
            let connection = AudioPlaybackConnection::TryCreateFromId(&HSTRING::from(device_id))
                .context("Windows could not create the Bluetooth audio receiver connection")?;
            connection.StartAsync()?.await?;
            let result = connection.OpenAsync()?.await?;
            if result.Status()? != AudioPlaybackConnectionOpenResultStatus::Success {
                bail!(
                    "Windows rejected the Bluetooth audio receiver connection: {:?}",
                    result.Status()?
                );
            }
            self.connections
                .lock()
                .await
                .insert(device_id.into(), connection);
            self.status_result().await
        }

        pub async fn disconnect(&self, device_id: &str) -> Result<BluetoothReceiverStatus> {
            if let Some(connection) = self.connections.lock().await.remove(device_id) {
                connection.Close()?;
            }
            self.status_result().await
        }

        pub async fn forget(&self, device_id: &str) -> Result<BluetoothReceiverStatus> {
            if let Some(connection) = self.connections.lock().await.remove(device_id) {
                connection.Close()?;
            }
            let information =
                DeviceInformation::CreateFromIdAsync(&HSTRING::from(device_id))?.await?;
            let result = information.Pairing()?.UnpairAsync()?.await?;
            if !matches!(
                result.Status()?,
                DeviceUnpairingResultStatus::Unpaired
                    | DeviceUnpairingResultStatus::AlreadyUnpaired
            ) {
                bail!(
                    "Windows could not forget the Bluetooth device: {:?}",
                    result.Status()?
                );
            }
            self.status_result().await
        }

        async fn status_result(&self) -> Result<BluetoothReceiverStatus> {
            let selector = AudioPlaybackConnection::GetDeviceSelector()?;
            let information = DeviceInformation::FindAllAsyncAqsFilter(&selector)?.await?;
            let connections = self.connections.lock().await;
            let mut devices = Vec::new();
            for index in 0..information.Size()? {
                let device = information.GetAt(index)?;
                let id = device.Id()?.to_string_lossy();
                let connected = connections.get(&id).is_some_and(|connection| {
                    connection.State().ok() == Some(AudioPlaybackConnectionState::Opened)
                });
                devices.push(BluetoothReceiverDevice {
                    id,
                    name: device.Name()?.to_string_lossy(),
                    paired: true,
                    trusted: true,
                    connected,
                    audio_capable: true,
                });
            }
            devices.sort_by(|left, right| {
                right
                    .connected
                    .cmp(&left.connected)
                    .then_with(|| left.name.to_lowercase().cmp(&right.name.to_lowercase()))
            });
            let status_message = if devices.iter().any(|device| device.connected) {
                "Bluetooth audio connection open"
            } else if devices.is_empty() {
                "Pair a phone in Windows Bluetooth settings"
            } else {
                "Select a paired phone to receive audio"
            };
            Ok(BluetoothReceiverStatus {
                supported: true,
                receiver_ready: true,
                platform: "Windows / AudioPlaybackConnection".into(),
                adapter_name: "Windows Bluetooth".into(),
                powered: true,
                discoverable: false,
                pairable: false,
                status_message: status_message.into(),
                last_error: String::new(),
                setup_hint: windows_setup_hint(),
                devices,
            })
        }
    }

    fn windows_setup_hint() -> String {
        "Pair phones in Windows Bluetooth settings, then connect them here. Incoming audio is played through the default Windows output and captured through WASAPI loopback.".into()
    }
}

#[cfg(not(any(target_os = "linux", target_os = "windows")))]
mod platform {
    use anyhow::{Result, bail};

    use crate::proto::v1::BluetoothReceiverStatus;

    pub struct PlatformReceiver;

    impl PlatformReceiver {
        pub fn new() -> Self {
            Self
        }

        pub async fn status(&self) -> BluetoothReceiverStatus {
            BluetoothReceiverStatus {
                supported: false,
                platform: std::env::consts::OS.into(),
                status_message: "Bluetooth audio receiver is not supported on this platform".into(),
                ..Default::default()
            }
        }

        pub async fn set_pairing(
            &self,
            _enabled: bool,
            _timeout_seconds: u32,
        ) -> Result<BluetoothReceiverStatus> {
            bail!("Bluetooth audio receiver is not supported on this platform")
        }

        pub async fn connect(&self, _device_id: &str) -> Result<BluetoothReceiverStatus> {
            bail!("Bluetooth audio receiver is not supported on this platform")
        }

        pub async fn disconnect(&self, _device_id: &str) -> Result<BluetoothReceiverStatus> {
            bail!("Bluetooth audio receiver is not supported on this platform")
        }

        pub async fn forget(&self, _device_id: &str) -> Result<BluetoothReceiverStatus> {
            bail!("Bluetooth audio receiver is not supported on this platform")
        }
    }
}
