#!/usr/bin/env bash
# install-systemd.sh - register the mnemo daemon autostart with systemd-user (Linux).
#
# v5.10.0: parallel of v5.8.1's install-task.ps1 for the Linux surface.
# Run this once after installing mnemo (or after `git pull` if the
# template / wrapper changed):
#
#   bash scripts/linux-autostart/install-systemd.sh
#
# Idempotent - re-running just overwrites the unit file and reloads.
#
# Why systemd-user over a .desktop autostart entry:
#  - Fires earlier in the user session.
#  - Restart=on-failure: auto-retry on transient failures.
#  - Visible via 'systemctl --user status' for diagnosis.
#  - Survives desktop-session crash + restart.
#  - Decouples from per-DE autostart specs (GNOME / KDE / XFCE / Sway).

set -euo pipefail

UNIT_NAME="${MNEMO_SYSTEMD_UNIT:-mnemo-daemon}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER="${SCRIPT_DIR}/mnemo-autostart.sh"
TEMPLATE="${SCRIPT_DIR}/mnemo-daemon.service.template"

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_FILE="${UNIT_DIR}/${UNIT_NAME}.service"

# Resolve the mnemo CLI path. Caller can override via MNEMO_BIN; otherwise
# fall back to 'command -v mnemo' (whichever 'mnemo' is on the PATH at
# install time).
MNEMO_BIN_RESOLVED="${MNEMO_BIN:-$(command -v mnemo 2>/dev/null || echo '')}"
if [[ -z "${MNEMO_BIN_RESOLVED}" ]]; then
    echo "ERROR: 'mnemo' not on PATH and MNEMO_BIN not set." >&2
    echo "Hint: 'pip install mnemo' or set MNEMO_BIN=/abs/path/to/mnemo before re-running." >&2
    exit 1
fi

if [[ ! -f "${TEMPLATE}" ]]; then
    echo "ERROR: missing systemd unit template at ${TEMPLATE}" >&2
    exit 1
fi
if [[ ! -f "${WRAPPER}" ]]; then
    echo "ERROR: missing wrapper script at ${WRAPPER}" >&2
    exit 1
fi

mkdir -p "${UNIT_DIR}"
chmod +x "${WRAPPER}"

# Render the template (sed in-place via temp file so it works on
# every Linux distro's sed variant).
TMP_UNIT="$(mktemp)"
sed \
    -e "s|@WRAPPER@|${WRAPPER}|g" \
    -e "s|@MNEMO_BIN@|${MNEMO_BIN_RESOLVED}|g" \
    "${TEMPLATE}" > "${TMP_UNIT}"
mv "${TMP_UNIT}" "${UNIT_FILE}"

# Reload + enable + start. daemon-reload is REQUIRED for systemd to
# pick up the new unit file; without it 'enable' fails with
# "Unit ${UNIT_NAME}.service not found."
systemctl --user daemon-reload
systemctl --user enable "${UNIT_NAME}.service"
systemctl --user start "${UNIT_NAME}.service" || {
    echo "WARNING: 'systemctl --user start' returned non-zero." >&2
    echo "Check: systemctl --user status ${UNIT_NAME}.service" >&2
}

echo "Registered systemd user unit '${UNIT_NAME}.service' (default.target + Restart=on-failure)."
echo ""
echo "View status:"
echo "  systemctl --user status ${UNIT_NAME}.service"
echo ""
echo "Manual restart:"
echo "  systemctl --user restart ${UNIT_NAME}.service"
echo ""
echo "Logs:"
echo "  journalctl --user -u ${UNIT_NAME}.service -f"
echo "  (wrapper log also at \$XDG_STATE_HOME/mnemo/logs/autostart.log)"
echo ""
echo "Hint: for autostart to fire on logon when no graphical session is"
echo "      active, run 'loginctl enable-linger \$USER' (one-time)."
