#!/usr/bin/env bash
# Configures the Pi to broadcast its own WiFi network (a NetworkManager
# hotspot) so a phone can reach the dashboard directly, with no external
# router needed — e.g. in the field during a training exercise. Reads
# network.ap_mode.* from config.yaml so the app and this script never
# disagree about SSID/password.
#
# Usage:
#   bash scripts/setup_wifi_ap.sh           # enable (reads config.yaml)
#   bash scripts/setup_wifi_ap.sh disable   # revert to normal WiFi client mode
#
# WARNING: a Raspberry Pi 4B has a single WiFi radio. Enabling this
# DISCONNECTS any existing WiFi client connection on that radio — including
# an SSH session over WiFi. Run this over Ethernet or a direct console, or
# be ready to reconnect by joining the new hotspot network yourself.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${CONFIG_FILE:-$REPO_ROOT/config.yaml}"
CON_NAME="VictimSimHotspot"
ACTION="${1:-enable}"

read_yaml() {
  "$REPO_ROOT/.venv/bin/python" -c "
import yaml
raw = yaml.safe_load(open('$CONFIG_FILE'))
ap = (raw.get('network') or {}).get('ap_mode') or {}
web = raw.get('web') or {}
print(ap.get('$1', web.get('$1', '')))
"
}

if [ "$ACTION" = "disable" ]; then
  echo "Disabling hotspot '$CON_NAME' and reverting to normal WiFi client mode..."
  sudo nmcli connection down "$CON_NAME" 2>/dev/null || true
  sudo nmcli connection modify "$CON_NAME" autoconnect no 2>/dev/null || true
  echo "Done. Reboot, or run 'nmcli connection up <your-wifi-profile-name>' to reconnect now."
  echo "(list profiles with: nmcli connection show)"
  exit 0
fi

ENABLED="$(read_yaml enabled)"
SSID="$(read_yaml ssid)"
PASSWORD="$(read_yaml password)"
INTERFACE="$(read_yaml interface)"
PORT="$(read_yaml port)"
INTERFACE="${INTERFACE:-wlan0}"
PORT="${PORT:-8080}"

if [ "$ENABLED" != "True" ]; then
  echo "network.ap_mode.enabled is false in $CONFIG_FILE — nothing to do."
  echo "Set it to true in config.yaml, then re-run this script."
  exit 0
fi

if [ -z "$SSID" ] || [ -z "$PASSWORD" ]; then
  echo "network.ap_mode.ssid and .password must be set in $CONFIG_FILE" >&2
  exit 1
fi
if [ "${#PASSWORD}" -lt 8 ]; then
  echo "network.ap_mode.password must be at least 8 characters (WPA2 requirement)" >&2
  exit 1
fi

echo "About to configure a WiFi hotspot:"
echo "  SSID:      $SSID"
echo "  Interface: $INTERFACE"
echo ""
echo "WARNING: this disconnects any existing WiFi client connection on"
echo "$INTERFACE (including an SSH session over WiFi). It also sets the"
echo "hotspot to autoconnect, so future reboots come up in AP mode too —"
echo "run 'bash scripts/setup_wifi_ap.sh disable' any time to revert."
read -r -p "Continue? [y/N] " REPLY
case "$REPLY" in
  [yY]) ;;
  *) echo "Aborted."; exit 1 ;;
esac

sudo nmcli connection delete "$CON_NAME" 2>/dev/null || true
sudo nmcli connection add type wifi ifname "$INTERFACE" con-name "$CON_NAME" autoconnect yes ssid "$SSID"
sudo nmcli connection modify "$CON_NAME" \
  802-11-wireless.mode ap \
  802-11-wireless.band bg \
  ipv4.method shared \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "$PASSWORD"
sudo nmcli connection up "$CON_NAME"

GATEWAY_IP="$(nmcli -g IP4.ADDRESS connection show "$CON_NAME" 2>/dev/null | cut -d/ -f1)"
GATEWAY_IP="${GATEWAY_IP:-10.42.0.1}"  # NetworkManager's default "shared" gateway

echo ""
echo "Hotspot '$SSID' is up."
echo "On your phone: connect to WiFi network '$SSID', then browse to:"
echo "  http://${GATEWAY_IP}:${PORT}"
