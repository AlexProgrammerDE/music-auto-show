#!/usr/bin/env bash

set -euo pipefail

purge=false

usage() {
  cat <<'EOF'
Remove the Music Auto Show Raspberry Pi service installation.

Usage:
  ./scripts/uninstall-rpi.sh [--purge]

Options:
  --purge       Also remove the deployed configuration and BeatNet+ checkpoint.
  -h, --help    Show this help.

Runtime packages, dialout membership, and systemd lingering are left unchanged
because other applications and user services may depend on them.
EOF
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

while (($# > 0)); do
  case "$1" in
    --purge)
      purge=true
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

((EUID != 0)) || die "run this script as the user that owns Music Auto Show, not as root"
command -v sudo >/dev/null 2>&1 || die "sudo is required"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"

unit_path="$HOME/.config/systemd/user/music-auto-show.service"
wireplumber_05_path="$HOME/.config/wireplumber/wireplumber.conf.d/80-music-auto-show-headless.conf"
wireplumber_04_path="$HOME/.config/wireplumber/bluetooth.lua.d/80-music-auto-show-headless.lua"
config_path="$HOME/.config/music-auto-show/config.json"
model_path="$HOME/.local/share/music-auto-show/models/beatnet-plus.pt"

systemctl --user disable --now music-auto-show.service 2>/dev/null || true
rm -f -- "$unit_path"
systemctl --user daemon-reload
systemctl --user reset-failed music-auto-show.service 2>/dev/null || true

wireplumber_config_removed=false
if [[ -e "$wireplumber_05_path" || -e "$wireplumber_04_path" ]]; then
  rm -f -- "$wireplumber_05_path" "$wireplumber_04_path"
  wireplumber_config_removed=true
fi
if $wireplumber_config_removed; then
  systemctl --user try-restart wireplumber.service || true
fi

sudo rm -f -- /usr/local/bin/music-auto-show

if $purge; then
  rm -f -- "$config_path" "$model_path"
  rmdir -- "$HOME/.local/share/music-auto-show/models" 2>/dev/null || true
  rmdir -- "$HOME/.local/share/music-auto-show" 2>/dev/null || true
  rmdir -- "$HOME/.config/music-auto-show" 2>/dev/null || true
  printf 'Removed the deployed configuration and BeatNet+ checkpoint.\n'
else
  printf 'Preserved configuration and model data for a future reinstall.\n'
fi

printf 'Music Auto Show was uninstalled. Runtime packages, lingering, and group membership were not changed.\n'
