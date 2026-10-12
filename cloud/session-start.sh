#!/usr/bin/env bash
# Claude Code SessionStart hook: make sure this cloud session can reach the Pi.
# Does nothing on machines without TS_AUTHKEY (e.g. the laptop, which is
# already on the tailnet). Output goes into the session's context, so keep it short.
[ -n "${TS_AUTHKEY:-}" ] || exit 0
cd "$(dirname "$0")/.." || exit 0
if bash cloud/setup.sh >/tmp/pi-setup.log 2>&1; then
  echo "Pi access ready: 'ssh pi' works, 'pi-dash /api/status' for the dashboard. Setup log: /tmp/pi-setup.log"
else
  echo "Pi access setup FAILED (see /tmp/pi-setup.log, then run: bash cloud/setup.sh). Last lines:"
  tail -5 /tmp/pi-setup.log
fi
grep -E '^!!' /tmp/pi-setup.log || true
exit 0
