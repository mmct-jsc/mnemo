#!/usr/bin/env bash
# uninstall-launchd.sh - remove the mnemo daemon launchd agent (macOS).
#
# Run this if you want to disable autostart or revert to manual start.
# Does NOT touch the daemon itself - just the launchd wiring.

set -u

LABEL="${MNEMO_LAUNCHD_LABEL:-com.mnemo.daemon}"
PLIST_FILE="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if [[ ! -f "${PLIST_FILE}" ]]; then
    echo "No launchd plist at ${PLIST_FILE} - nothing to remove."
    exit 0
fi

# Unload the agent (launchctl no-ops silently if the agent isn't loaded).
launchctl unload "${PLIST_FILE}" 2>/dev/null || true
rm -f "${PLIST_FILE}"

echo "Unregistered launchd user agent '${LABEL}'."
echo ""
echo "The mnemo daemon will NOT autostart on next logon."
echo "Start it manually with: mnemo daemon start"
