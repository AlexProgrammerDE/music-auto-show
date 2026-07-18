use std::{io::Write, thread, time::Duration};

use anyhow::{Context, Result, bail};
use serialport::{DataBits, FlowControl, Parity, SerialPort, SerialPortType, StopBits};

use crate::proto::v1::{DmxConfig, DmxRuntimeStatus};

pub enum DmxOutput {
    Simulated {
        status: DmxRuntimeStatus,
    },
    Serial {
        port: Box<dyn SerialPort>,
        status: DmxRuntimeStatus,
    },
}

impl DmxOutput {
    pub fn open(config: &DmxConfig) -> Result<Self> {
        if config.simulate {
            return Ok(Self::Simulated {
                status: DmxRuntimeStatus {
                    configured_port: config.port.clone(),
                    port: "Simulated DMX".into(),
                    device_info: "No hardware output".into(),
                    interface_type: "simulated".into(),
                    break_method: "simulated".into(),
                    running: true,
                    is_open: true,
                    simulated: true,
                    ..Default::default()
                },
            });
        }

        let port_name = if config.port.trim().is_empty() || config.port.eq_ignore_ascii_case("auto")
        {
            detect_port().context("no serial DMX interface was found")?
        } else {
            config.port.clone()
        };
        let device_info = serialport::available_ports()
            .ok()
            .and_then(|ports| ports.into_iter().find(|port| port.port_name == port_name))
            .map(|port| format_port(&port))
            .unwrap_or_else(|| "Unknown serial device".into());
        let mut port = serialport::new(&port_name, 250_000)
            .data_bits(DataBits::Eight)
            .stop_bits(StopBits::Two)
            .parity(Parity::None)
            .flow_control(FlowControl::None)
            .timeout(Duration::from_millis(100))
            .exclusive(true)
            .open()
            .with_context(|| format!("failed to open DMX interface {port_name}"))?;
        let _ = port.write_request_to_send(false);
        let _ = port.write_data_terminal_ready(false);
        port.flush()?;
        Ok(Self::Serial {
            port,
            status: DmxRuntimeStatus {
                configured_port: config.port.clone(),
                port: port_name,
                device_info,
                interface_type: "Open DMX USB".into(),
                break_method: "baudrate_switch".into(),
                running: true,
                is_open: true,
                simulated: false,
                ..Default::default()
            },
        })
    }

    pub fn send(&mut self, universe: &[u8]) -> Result<()> {
        match self {
            Self::Simulated { .. } => Ok(()),
            Self::Serial { port, .. } => send_open_dmx(port.as_mut(), universe),
        }
    }

    pub fn status(&self) -> DmxRuntimeStatus {
        match self {
            Self::Simulated { status } | Self::Serial { status, .. } => status.clone(),
        }
    }
}

fn send_open_dmx(port: &mut dyn SerialPort, universe: &[u8]) -> Result<()> {
    // Open DMX adapters have no packet protocol. A zero byte at 50 kbaud
    // creates an approximately 180 microsecond break, followed by the DMX MAB
    // and a conventional 250 kbaud 8N2 frame.
    port.set_baud_rate(50_000)?;
    port.write_all(&[0])?;
    port.flush()?;
    port.set_baud_rate(250_000)?;
    thread::sleep(Duration::from_micros(12));
    port.write_all(&[0])?;
    port.write_all(universe)?;
    port.flush()?;
    Ok(())
}

fn detect_port() -> Option<String> {
    let ports = serialport::available_ports().ok()?;
    ports
        .iter()
        .find(|port| matches!(&port.port_type, SerialPortType::UsbPort(info) if info.vid == 0x0403 && info.pid == 0x6001))
        .or_else(|| ports.iter().find(|port| matches!(&port.port_type, SerialPortType::UsbPort(info) if info.vid == 0x0403)))
        .or_else(|| ports.iter().find(|port| format_port(port).to_uppercase().contains("DMX")))
        .or_else(|| ports.iter().find(|port| port.port_name.to_uppercase().contains("USB") || port.port_name.to_uppercase().contains("ACM")))
        .map(|port| port.port_name.clone())
}

fn format_port(port: &serialport::SerialPortInfo) -> String {
    match &port.port_type {
        SerialPortType::UsbPort(info) => format!(
            "{} (VID:0x{:04x} PID:0x{:04x})",
            info.product.as_deref().unwrap_or("USB serial device"),
            info.vid,
            info.pid
        ),
        SerialPortType::PciPort => "PCI serial device".into(),
        SerialPortType::BluetoothPort => "Bluetooth serial device".into(),
        SerialPortType::Unknown => "Unknown serial device".into(),
    }
}

pub fn validate_config(config: &DmxConfig) -> Result<()> {
    if !(1..=512).contains(&config.universe_size) {
        bail!("DMX universe size must be between 1 and 512");
    }
    if !(1..=44).contains(&config.fps) {
        bail!("DMX output rate must be between 1 and 44 FPS");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_rates_above_the_dmx_limit() {
        let config = DmxConfig {
            fps: 45,
            universe_size: 512,
            ..Default::default()
        };
        assert!(validate_config(&config).is_err());
    }
}
