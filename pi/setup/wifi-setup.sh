#!/bin/bash
# Pi Wi-Fi order: 1) preferred Wi-Fi  2) home Wi-Fi  3) phone hotspot. Then move to home Wi-Fi now.
# Run ON THE PI (secrets come from the environment, never from this repo):
#   sudo HOME_SSID='<home wifi name>' HOME_PSK='<home wifi password>' bash wifi-setup.sh
# Optional: PREFERRED_SSID (another saved network to prefer; default is the
# placeholder YOUR_HOME_SSID, which does nothing) and HOTSPOT_CON (name of the saved phone-hotspot
# connection, default YOUR_PHONE_HOTSPOT).
# Why the background switch: changing networks drops this SSH session, so the
# switch runs as its own systemd unit and falls back to the hotspot if the home
# Wi-Fi doesn't connect or can't reach Tailscale within ~30 s.
# Log: /var/log/pi-wifi-switch.log
set -u
HOME_SSID="${HOME_SSID:?set HOME_SSID (home Wi-Fi name)}"
HOME_PSK="${HOME_PSK:?set HOME_PSK (home Wi-Fi password)}"
PREFERRED="${PREFERRED_SSID:-YOUR_HOME_SSID}"      # network to prefer when in range
HOT="${HOTSPOT_CON:-YOUR_PHONE_HOTSPOT}"           # NetworkManager connection name of your phone hotspot

# 1. save (or update) the home network
if nmcli -t -f NAME con show | grep -qx "$HOME_SSID"; then
  nmcli con modify "$HOME_SSID" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$HOME_PSK" connection.autoconnect yes
else
  nmcli con add type wifi ifname wlan0 con-name "$HOME_SSID" ssid "$HOME_SSID" \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$HOME_PSK" connection.autoconnect yes
fi

# 2. priorities: higher number wins when several are in range
nmcli con modify "$PREFERRED" connection.autoconnect-priority 30 2>/dev/null || true
nmcli con modify "$HOME_SSID" connection.autoconnect-priority 20
nmcli con modify "$HOT" connection.autoconnect-priority 10
echo "== saved networks (higher priority wins) =="
nmcli -f NAME,AUTOCONNECT,AUTOCONNECT-PRIORITY con show | grep -E "NAME|$PREFERRED|$HOME_SSID|$HOT"

# 3. only switch if home Wi-Fi is actually in range
nmcli dev wifi rescan 2>/dev/null; sleep 5
echo "== networks the Pi can see =="
nmcli -f SSID,SIGNAL dev wifi list | head -12
if ! nmcli -t -f SSID dev wifi list | grep -qx "$HOME_SSID"; then
  echo "!! $HOME_SSID is not in range. Saved it + priorities; staying on the hotspot."
  echo "   The Pi will join it automatically when it's in range."
  exit 0
fi

# 4. switch in the background (SSH will drop), with fallback to the hotspot
systemd-run --unit=pi-wifi-switch --collect bash -c '
  exec >>/var/log/pi-wifi-switch.log 2>&1
  echo "--- $(date) switching to '"$HOME_SSID"'"
  sleep 2
  ok=0
  if nmcli --wait 30 con up "'"$HOME_SSID"'"; then
    for i in $(seq 15); do
      if ping -c1 -W2 1.1.1.1 >/dev/null && tailscale status >/dev/null 2>&1; then ok=1; break; fi
      sleep 2
    done
  fi
  if [ $ok = 1 ]; then echo "on '"$HOME_SSID"', internet + tailscale OK"
  else echo "home Wi-Fi failed; back to hotspot"; nmcli con up "'"$HOT"'"; fi'
echo "== switching to $HOME_SSID in 2 s. This SSH session will disconnect; that's expected."
echo "   The Pi keeps its Tailscale address (see `tailscale ip -4`)."
