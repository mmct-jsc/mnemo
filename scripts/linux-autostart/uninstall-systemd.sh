#!/usr/bin/env bash
# uninstall-systemd.sh - remove the mnemo daemon systemd-user unit (Linux).
#
# Run this if you want to disable autostart or revert to manual start.
# Does NOT touch the daemon itself - just the systemd wiring.

set -u

UNIT_NAME="${MNEMO_SYSTEMD_UNIT:-mnemo-daemon}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_FILE="${UNIT_DIR}/${UNIT_NAME}.service"

if [[ ! -f "${UNIT_FILE}" ]]; then
    echo "No systemd user unit at ${UNIT_FILE} - nothing to remove."
    exit 0
fi

# Stop + disable (in that order: stop terminates the currently-running
# daemon, disable prevents next-logon spawn). systemctl no-ops cleanly
# if the unit isn't loaded / enabled.
systemctl --user stop "${UNIT_NAME}.service" 2>/dev/null || true
systemctl --user disable "${UNIT_NAME}.service" 2>/dev/null || true
rm -f "${UNIT_FILE}"
systemctl --user daemon-reload

echo "Unregistered systemd user unit '${UNIT_NAME}.service'."
echo ""
echo "The mnemo daemon will NOT autostart on next logon."
echo "Start it manually with: mnemo daemon start"
