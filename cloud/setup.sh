#!/usr/bin/env bash
# Join the tailnet from a cloud container and set up SSH + dashboard access to the Pi.
#
# Why userspace networking: cloud containers usually can't create a TUN
# device, so tailscaled runs as a user-level proxy instead. SSH goes through
# `tailscale nc`; HTTP to the Pi goes through a local SOCKS5 proxy on :1055.
#
# Needs these environment variables (set them as secrets in the cloud env):
#   TS_AUTHKEY   Tailscale auth key (ephemeral + reusable recommended)
#   PI_SSH_KEY_B64  private SSH key, base64 on ONE line (env var fields often
#                   reject multi-line values):  base64 -w0 ~/.ssh/pi_cloud
#   (or PI_SSH_KEY  the raw multi-line key, if your environment allows it)
# Required: PI_HOST (the Pi Tailscale IP, e.g. from `tailscale ip -4` on the Pi)
# Optional:
#   PI_USER (default username)
#
# Safe to re-run: it skips whatever is already set up.
set -euo pipefail

: "${TS_AUTHKEY:?set TS_AUTHKEY (Tailscale admin > Settings > Keys)}"
: "${PI_SSH_KEY_B64:=}" "${PI_SSH_KEY:=}"
[ -n "$PI_SSH_KEY_B64$PI_SSH_KEY" ] || { echo "set PI_SSH_KEY_B64 (base64 -w0 ~/.ssh/pi_cloud)" >&2; exit 1; }
PI_HOST="${PI_HOST:?set PI_HOST to the Pi Tailscale IP or MagicDNS name}"
PI_USER="${PI_USER:-username}"
SOCK="/tmp/tailscaled.sock"
SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null && SUDO="sudo"

# 0. ssh + rsync (slim cloud images sometimes lack them)
need=""; command -v ssh >/dev/null || need="$need openssh-client"; command -v rsync >/dev/null || need="$need rsync"
if [ -n "$need" ]; then echo "installing:$need"; $SUDO apt-get update -qq && $SUDO apt-get install -y -qq $need; fi

# 1. tailscale binaries (static build: works without systemd or apt repos)
if ! command -v tailscaled >/dev/null; then
  echo "installing tailscale..."
  arch=$(uname -m); case "$arch" in x86_64) arch=amd64;; aarch64|arm64) arch=arm64;; esac
  ver=$(curl -fsSL "https://pkgs.tailscale.com/stable/?mode=json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["TarballsVersion"])')
  curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${ver}_${arch}.tgz" | tar xz -C /tmp
  $SUDO install -m 755 /tmp/tailscale_${ver}_${arch}/tailscale /tmp/tailscale_${ver}_${arch}/tailscaled /usr/local/bin/
fi

# 2. tailscaled in userspace mode (state in memory: the node vanishes when the container stops)
if ! tailscale --socket="$SOCK" status >/dev/null 2>&1; then
  if [ ! -S "$SOCK" ] || ! tailscale --socket="$SOCK" version >/dev/null 2>&1; then
    rm -f "$SOCK"
    nohup tailscaled --tun=userspace-networking --state=mem: --socket="$SOCK" \
      --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1055 \
      >/tmp/tailscaled.log 2>&1 &
    sleep 2
  fi
  tailscale --socket="$SOCK" up --authkey="$TS_AUTHKEY" --hostname="cloud-$(hostname | cut -c1-12)" --accept-dns=false
fi
tailscale --socket="$SOCK" status | head -8

# 3. SSH key + "ssh pi" shortcut
mkdir -p ~/.ssh && chmod 700 ~/.ssh
if [ -n "$PI_SSH_KEY_B64" ]; then
  printf '%s' "$PI_SSH_KEY_B64" | tr -d ' \n\r' | base64 -d > ~/.ssh/pi_cloud
else
  printf '%b\n' "$PI_SSH_KEY" | sed '/^$/d' > ~/.ssh/pi_cloud
fi
chmod 600 ~/.ssh/pi_cloud
ssh-keygen -y -f ~/.ssh/pi_cloud >/dev/null || { echo "!! the SSH key secret is not a valid private key" >&2; exit 1; }
if ! grep -q '^Host pi$' ~/.ssh/config 2>/dev/null; then
  cat >> ~/.ssh/config <<EOF
Host pi
  HostName $PI_HOST
  User $PI_USER
  IdentityFile ~/.ssh/pi_cloud
  IdentitiesOnly yes
  ProxyCommand tailscale --socket=$SOCK nc %h %p
  StrictHostKeyChecking accept-new
  ServerAliveInterval 20
EOF
  chmod 600 ~/.ssh/config
fi

# 4. helpers on PATH
$SUDO install -m 755 "$(dirname "$0")/pi-dash" /usr/local/bin/pi-dash

echo
echo "== checks =="
tailscale --socket="$SOCK" ping -c 3 "$PI_HOST" || true
ssh -o BatchMode=yes -o ConnectTimeout=15 pi 'echo "ssh ok: $(hostname), up $(uptime -p)"' || echo "!! ssh failed: is the key's .pub in the Pi's authorized_keys?"
pi-dash /api/status >/dev/null && echo "dashboard ok" || echo "!! dashboard not reachable on :8081"
echo
echo "Ready. Try:  ssh pi   |   pi-dash /api/status   |   pi-dash /api/camera"
