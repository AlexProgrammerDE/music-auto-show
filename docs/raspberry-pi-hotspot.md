# Host the Music Auto Show Wi-Fi network

This guide configures an optional Wi-Fi hotspot on Raspberry Pi OS. NetworkManager owns the connection profile, DHCP, DNS, address sharing, and boot-time activation. Music Auto Show continues to run as an ordinary unprivileged process.

Nothing changes unless you run one of the `hotspot` commands. Starting Music Auto Show normally never creates or modifies a network connection.

## Before you begin

Use Raspberry Pi OS Bookworm or newer, where NetworkManager is the default networking service. Set the correct WLAN country in `raspi-config` before enabling a hotspot.

Changing a Wi-Fi adapter from client mode to hotspot mode can interrupt an SSH session that uses the same adapter. Run the first setup with local console access or an Ethernet connection when possible. A single Wi-Fi adapter normally cannot stay connected to another Wi-Fi network while hosting this hotspot. Use Ethernet or a second Wi-Fi adapter if the Pi also needs an internet uplink.

Build Music Auto Show before provisioning the hotspot:

```bash
./compile.sh
```

## Enable the hotspot

Run the provisioning command as root on a headless Raspberry Pi:

```bash
sudo ./target/release/music-auto-show hotspot enable
```

The first run creates a NetworkManager profile named `music-auto-show-hotspot`, broadcasts the SSID `Music Auto Show`, generates a strong Wi-Fi password, and prints that password once. Store it before closing the terminal. The profile starts automatically on later boots.

To choose the network name, password, or Wi-Fi adapter yourself:

```bash
sudo ./target/release/music-auto-show hotspot enable \
  --ssid "Stage Lights" \
  --password "replace-with-a-strong-password" \
  --interface wlan0
```

The SSID must be at most 32 bytes. WPA passwords must contain 8 to 63 ASCII characters, or exactly 64 hexadecimal characters. A password passed on the command line can remain in shell history, so prefer the generated password unless you need a fixed credential.

Running `enable` again updates the existing Music Auto Show profile. Omitted values remain unchanged for an existing profile.

## Start Music Auto Show

Start the application normally, without root privileges:

```bash
./target/release/music-auto-show
```

The server listens on `0.0.0.0:3000` by default. Connect a phone or laptop to the hotspot, then open the Web UI address printed by `hotspot enable`. NetworkManager normally assigns an address in the `10.42.x.1` range, but use the printed address instead of assuming a specific subnet.

Music Auto Show does not authenticate its Web UI or API. Treat the Wi-Fi password as the access boundary and share it only with people who should control the show.

## Check hotspot status

The status command does not show the Wi-Fi password:

```bash
./target/release/music-auto-show hotspot status
```

It reports whether the profile exists, whether it is active, whether it starts at boot, and the current Web UI address when available.

## Disable or remove the hotspot

Disable the hotspot without deleting its settings:

```bash
sudo ./target/release/music-auto-show hotspot disable
```

This disconnects the hotspot and turns off autoconnect. Re-enable it later with `hotspot enable`.

Delete the dedicated NetworkManager profile completely:

```bash
sudo ./target/release/music-auto-show hotspot remove
```

These commands only modify the `music-auto-show-hotspot` profile. They do not delete other Wi-Fi profiles.

## Troubleshooting

- If `nmcli` is missing, install and start NetworkManager before retrying.
- If NetworkManager reports insufficient permissions, rerun the command with `sudo`.
- If no suitable Wi-Fi device is found, verify that Wi-Fi is enabled and that the adapter supports access point mode. Pass `--interface wlan0` when NetworkManager chooses the wrong adapter.
- If hotspot activation disconnects SSH, reconnect through Ethernet or join the newly created hotspot.
- If clients connect but cannot reach the internet, provide an Ethernet or second Wi-Fi uplink. The Web UI on the Pi remains available without internet access.
