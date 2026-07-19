#!/usr/bin/env bash

set -euo pipefail

project_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
binary_path="$project_root/target/release/music-auto-show"
config_source="$project_root/config.json"
install_packages=true
headless=true
start_service=true

usage() {
  cat <<'EOF'
Install Music Auto Show as a systemd user service on Raspberry Pi OS.

Usage:
  ./scripts/install-rpi.sh [options]

Options:
  --binary PATH       Install this release binary.
  --config PATH       Seed the deployed config from this file when none exists.
  --desktop           Keep WirePlumber's active-seat Bluetooth policy.
  --skip-packages     Do not install Raspberry Pi OS runtime packages.
  --no-start          Enable the service without starting it now.
  -h, --help          Show this help.

Run this script as the unprivileged user that will own the audio session.
Existing deployed configuration and BeatNet+ checkpoint files are preserved.
EOF
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '%s\n' "$*"
}

while (($# > 0)); do
  case "$1" in
    --binary)
      (($# >= 2)) || die "--binary requires a path"
      binary_path="$2"
      shift 2
      ;;
    --config)
      (($# >= 2)) || die "--config requires a path"
      config_source="$2"
      shift 2
      ;;
    --desktop)
      headless=false
      shift
      ;;
    --skip-packages)
      install_packages=false
      shift
      ;;
    --no-start)
      start_service=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

((EUID != 0)) || die "run this script as the user that will own Music Auto Show, not as root"
[[ -x "$binary_path" ]] || die "release binary not found or not executable: $binary_path"
[[ -f "$project_root/packaging/systemd/music-auto-show.service" ]] || die "systemd unit asset is missing"
command -v sudo >/dev/null 2>&1 || die "sudo is required"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
command -v loginctl >/dev/null 2>&1 || die "systemd-logind is required"

if command -v file >/dev/null 2>&1; then
  binary_description="$(file -Lb -- "$binary_path")"
  case "$(uname -m)" in
    aarch64|arm64)
      [[ "$binary_description" == *"ARM aarch64"* ]] || die "the binary is not built for ARM64: $binary_description"
      ;;
    armv7l|armv6l)
      [[ "$binary_description" == *"ARM"* && "$binary_description" != *"aarch64"* ]] || die "the binary does not match the 32-bit ARM host: $binary_description"
      ;;
    x86_64)
      [[ "$binary_description" == *"x86-64"* ]] || die "the binary does not match the x86-64 host: $binary_description"
      ;;
  esac
fi

if $install_packages; then
  command -v apt-get >/dev/null 2>&1 || die "automatic package installation requires Raspberry Pi OS or another Debian-based system"
  runtime_packages=(
    bluez
    libasound2
    libdbus-1-3
    libpipewire-0.3-0
    libspa-0.2-bluetooth
    libsystemd0
    libudev1
    pipewire
    wireplumber
  )
  if apt-cache show pipewire-audio >/dev/null 2>&1; then
    runtime_packages+=(pipewire-audio)
  else
    runtime_packages+=(pipewire-pulse)
  fi
  note "Installing Raspberry Pi OS runtime packages..."
  sudo apt-get update
  sudo apt-get install -y "${runtime_packages[@]}"
fi

current_user="$(id -un)"
group_refresh_required=false
if ! id -Gn | tr ' ' '\n' | grep -Fxq dialout; then
  note "Adding $current_user to the dialout group for Open DMX access..."
  sudo usermod -a -G dialout "$current_user"
  group_refresh_required=true
fi

config_dir="$HOME/.config/music-auto-show"
state_dir="$HOME/.local/share/music-auto-show"
model_dir="$state_dir/models"
unit_dir="$HOME/.config/systemd/user"
unit_path="$unit_dir/music-auto-show.service"
deployed_config="$config_dir/config.json"
deployed_model="$model_dir/beatnet-plus.pt"

install -d -m 0700 -- "$config_dir" "$state_dir" "$model_dir"
install -d -m 0755 -- "$unit_dir"

if [[ ! -e "$deployed_config" ]]; then
  if [[ -f "$config_source" ]]; then
    install -m 0600 -- "$config_source" "$deployed_config"
    note "Installed initial configuration at $deployed_config"
  else
    note "No seed configuration found; the application will use its defaults."
  fi
else
  note "Preserved existing configuration at $deployed_config"
fi

checkpoint_source="$project_root/models/beatnet-plus.pt"
if [[ ! -e "$deployed_model" && -f "$checkpoint_source" ]]; then
  install -m 0600 -- "$checkpoint_source" "$deployed_model"
  note "Installed the existing BeatNet+ checkpoint at $deployed_model"
elif [[ -e "$deployed_model" ]]; then
  note "Preserved existing BeatNet+ checkpoint at $deployed_model"
fi

if systemctl --user is-active --quiet music-auto-show.service; then
  systemctl --user stop music-auto-show.service
fi

sudo install -m 0755 -- "$binary_path" /usr/local/bin/music-auto-show
install -m 0644 -- "$project_root/packaging/systemd/music-auto-show.service" "$unit_path"

wireplumber_config_changed=false
wireplumber_05_path="$HOME/.config/wireplumber/wireplumber.conf.d/80-music-auto-show-headless.conf"
wireplumber_04_path="$HOME/.config/wireplumber/bluetooth.lua.d/80-music-auto-show-headless.lua"
if $headless; then
  wireplumber_version="$(wireplumber --version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
  case "$wireplumber_version" in
    0.4.*)
      install -d -m 0755 -- "$(dirname -- "$wireplumber_04_path")"
      install -m 0644 -- "$project_root/packaging/wireplumber/0.4/80-music-auto-show-headless.lua" "$wireplumber_04_path"
      rm -f -- "$wireplumber_05_path"
      ;;
    *)
      install -d -m 0755 -- "$(dirname -- "$wireplumber_05_path")"
      install -m 0644 -- "$project_root/packaging/wireplumber/0.5/80-music-auto-show-headless.conf" "$wireplumber_05_path"
      rm -f -- "$wireplumber_04_path"
      ;;
  esac
  wireplumber_config_changed=true
else
  if [[ -e "$wireplumber_05_path" || -e "$wireplumber_04_path" ]]; then
    rm -f -- "$wireplumber_05_path" "$wireplumber_04_path"
    wireplumber_config_changed=true
  fi
fi

sudo systemctl enable --now bluetooth.service
sudo loginctl enable-linger "$current_user"
systemctl --user daemon-reload
systemctl --user enable --now pipewire.socket wireplumber.service

if $wireplumber_config_changed; then
  systemctl --user restart wireplumber.service
fi

systemctl --user enable music-auto-show.service
if $start_service; then
  systemctl --user restart music-auto-show.service
  if ! systemctl --user is-active --quiet music-auto-show.service; then
    systemctl --user status music-auto-show.service --no-pager -l || true
    die "Music Auto Show did not stay running"
  fi
fi

note ""
note "Music Auto Show installation is complete."
note "Service logs: journalctl --user -u music-auto-show.service -f"
if $start_service; then
  note "Web UI: http://<raspberry-pi-address>:3000"
else
  note "Start it with: systemctl --user start music-auto-show.service"
fi
if $group_refresh_required; then
  note "Reboot once so the user service receives its new dialout group membership."
fi
