# Working on the Pi from a cloud environment

A cloud container (Claude Code on the web, GitHub Codespaces, any Linux
container) can join your tailnet and reach the Pi like the laptop does:
`ssh pi`, the dashboard API, deploys, stream tests.

## What to add to the cloud environment

### Secrets / environment variables

| Name | Value | Where it comes from |
|---|---|---|
| `TS_AUTHKEY` | `tskey-auth-…` | Tailscale admin console → **Settings → Keys → Generate auth key**. Turn on **Reusable** and **Ephemeral** (the container disappears from your tailnet when it stops). Turn on **Pre-approved** if device approval is on. Pick an expiry (e.g. 90 days). |
| `PI_SSH_KEY_B64` | one long line from `base64 -w0 ~/.ssh/pi_cloud` | The private key, base64-encoded so it fits in a one-line env var field. Its `.pub` goes on the Pi. |
| `PI_HOST` | `<pi-tailscale-ip>` | required: the Pi's Tailscale IP (`tailscale ip -4` on the Pi) |
| `PI_USER` | `username` | optional, this is the default |

### Setup script

```bash
bash cloud/setup.sh
```

`cloud/session-start.sh` can be wired as a session-start hook (it does nothing
without `TS_AUTHKEY`), or you can set `cloud/setup.sh` as the environment's
setup script.

It installs Tailscale (static binaries, no systemd needed), starts it in
userspace mode, joins with `TS_AUTHKEY`, writes the SSH key, adds a `Host pi`
entry, puts `pi-dash` on the PATH, and checks all three connections.

### Network access

The container must reach these over HTTPS (443):
`pkgs.tailscale.com`, `controlplane.tailscale.com`, `login.tailscale.com`,
`*.tailscale.com` (DERP relays, e.g. `derp1.tailscale.com`), plus `github.com`.
If the environment limits outbound traffic, allow those domains (or use full
network access). If UDP is blocked, Tailscale still works through the relays,
just with more latency.

## One-time steps on your side

1. **Make a dedicated SSH key** (laptop):
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/pi_cloud -N "" -C "cloud-to-pi"
   ```
2. **Put its public half on the Pi** (asks for the Pi password once):
   ```bash
   ssh-copy-id -i ~/.ssh/pi_cloud.pub username@<pi-tailscale-ip>
   ```
3. **Copy the private half, as one line,** into the `PI_SSH_KEY_B64` secret:
   ```bash
   base64 -w0 ~/.ssh/pi_cloud; echo
   ```
4. **Make the auth key** in the Tailscale admin console and paste it into `TS_AUTHKEY`.
5. **Set your own password** on the Pi (`ssh pi passwd`). Key login
   keeps working.

## Check it works (in the cloud container)

```bash
ssh pi 'hostname; uptime'
pi-dash /api/status
pi-dash /api/camera
python3 tools/stream_test.py --socks localhost:1055 --seconds 10
ssh pi 'sudo -n true && echo "passwordless sudo: yes"'   # needed for deploys
```

Raspberry Pi OS usually gives the first user passwordless sudo. If the last
line fails, deploys will stop at the sudo prompt; restarts then need you.

## Optional: limit what the cloud can reach

By default every device on your tailnet can reach every other. To let cloud
containers reach only the Pi's SSH and web ports, tag the auth key
`tag:cloud` and add rules like this to your tailnet policy (keep your existing
rules; this is an addition):

```json
"tagOwners": { "tag:cloud": ["autogroup:admin"] },
"acls": [
  { "action": "accept", "src": ["autogroup:member"], "dst": ["*:*"] },
  { "action": "accept", "src": ["tag:cloud"], "dst": ["<pi-tailscale-ip>:22,8080,8081"] }
]
```

## Revoking access

- Remove the `cloud-to-pi` line from `~/.ssh/authorized_keys` on the Pi.
- Revoke the auth key in the Tailscale admin console.
