#!/usr/bin/env bash
# install-launchd.sh - register the mnemo daemon autostart with launchd (macOS).
#
# v5.10.0: parallel of v5.8.1's install-task.ps1 for the macOS surface.
# Run this once after installing mnemo (or after `git pull` if the
# template / wrapper changed):
#
#   bash scripts/macos-autostart/install-launchd.sh
#
# Idempotent - re-running unloads the existing agent first, then
# rewrites + reloads.
#
# Why launchd over a login item or .desktop:
#  - Fires earlier on logon (login items wait for shell init).
#  - KeepAlive=true respawns the agent on crash / transient failure.
#  - Visible via 'launchctl list' for diagnosis.
#  - Survives Finder + Dock crash + restart.
#  - Decouples from the per-user login-item registry.

set -euo pipefail

LABEL="${MNEMO_LAUNCHD_LABEL:-com.mnemo.daemon}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER="${SCRIPT_DIR}/mnemo-autostart.sh"
TEMPLATE="${SCRIPT_DIR}/com.mnemo.daemon.plist.template"

PLIST_DIR="${HOME}/Library/LaunchAgents"
PLIST_FILE="${PLIST_DIR}/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/mnemo"

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
    echo "ERROR: missing plist template at ${TEMPLATE}" >&2
    exit 1
fi
if [[ ! -f "${WRAPPER}" ]]; then
    echo "ERROR: missing wrapper script at ${WRAPPER}" >&2
    exit 1
fi

mkdir -p "${PLIST_DIR}" "${LOG_DIR}"
chmod +x "${WRAPPER}"

# Idempotent: unload any existing agent before rewriting (launchctl will
# silently no-op if the agent isn't loaded; ignore failure).
launchctl unload "${PLIST_FILE}" 2>/dev/null || true

# Render the template. Use a portable sed in-place that works on both
# macOS BSD sed and GNU sed (write to a temp + mv).
TMP_PLIST="$(mktemp -t mnemo-plist)"
sed \
    -e "s|@WRAPPER@|${WRAPPER}|g" \
    -e "s|@MNEMO_BIN@|${MNEMO_BIN_RESOLVED}|g" \
    -e "s|@LOG_DIR@|${LOG_DIR}|g" \
    "${TEMPLATE}" > "${TMP_PLIST}"
mv "${TMP_PLIST}" "${PLIST_FILE}"

# Load the agent (this also triggers RunAtLoad).
launchctl load "${PLIST_FILE}"

echo "Registered launchd user agent '${LABEL}' (RunAtLoad + KeepAlive)."
echo ""
echo "Test now (without waiting for next logon):"
echo "  launchctl kickstart -k gui/$(id -u)/${LABEL}"
echo ""
echo "View status:"
echo "  launchctl list | grep ${LABEL}"
echo ""
echo "Logs at: ${LOG_DIR}/autostart.log"
echo "  (also launchd-stdout.log / launchd-stderr.log for raw process output)"
