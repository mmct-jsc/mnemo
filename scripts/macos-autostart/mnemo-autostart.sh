#!/usr/bin/env bash
# mnemo-autostart.sh - production wrapper for the mnemo daemon autostart (macOS).
#
# v5.10.0 mirrors the v5.8.1 Windows Task Scheduler wrapper. launchd
# treats exit 0 as success and exit non-zero as failure (and KeepAlive
# will respawn the agent automatically). The wrapper:
#
#   1. Spawns ``mnemo daemon start`` via the editable-install ``mnemo``
#      CLI script.
#   2. Polls ``/v1/health`` for up to 60 s, exiting 0 only when the
#      daemon is provably listening and answering.
#   3. Appends a timestamped line to
#      ``~/Library/Logs/mnemo/autostart.log`` so future debugging has
#      evidence the autostart fired.
#
# Intentionally a separate script (not inlined into the plist's
# ProgramArguments) so it's editable + version-controlled + testable
# without touching launchd.
#
# Override the CLI path or health URL via env vars:
#   MNEMO_BIN=/path/to/mnemo bash mnemo-autostart.sh
#   MNEMO_HEALTH_URL=http://127.0.0.1:7373/v1/health
#   MNEMO_AUTOSTART_TIMEOUT=60

set -u

MNEMO_BIN="${MNEMO_BIN:-mnemo}"
MNEMO_HEALTH_URL="${MNEMO_HEALTH_URL:-http://127.0.0.1:7373/v1/health}"
MNEMO_AUTOSTART_TIMEOUT="${MNEMO_AUTOSTART_TIMEOUT:-60}"

LOG_DIR="${HOME}/Library/Logs/mnemo"
LOG_FILE="${LOG_DIR}/autostart.log"
mkdir -p "${LOG_DIR}"

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "${LOG_FILE}"
}

log "autostart fired (pid $$)"

if ! command -v "${MNEMO_BIN}" >/dev/null 2>&1; then
    if [[ ! -x "${MNEMO_BIN}" ]]; then
        log "FATAL: mnemo CLI not found at '${MNEMO_BIN}' (set MNEMO_BIN env var to override)."
        exit 1
    fi
fi

# Fire the daemon start. mnemo forks a detached --foreground subprocess
# and returns; we don't wait on the launch.
if ! "${MNEMO_BIN}" daemon start >> "${LOG_FILE}" 2>&1; then
    log "FATAL: 'mnemo daemon start' returned non-zero exit"
    exit 1
fi
log "spawned ${MNEMO_BIN} daemon start"

# Poll /v1/health until the daemon answers or we hit the timeout.
start_ts=$(date +%s)
deadline=$(( start_ts + MNEMO_AUTOSTART_TIMEOUT ))
last_err=""
while [[ "$(date +%s)" -lt "${deadline}" ]]; do
    if curl -fsS --max-time 3 -o /dev/null "${MNEMO_HEALTH_URL}" 2>/tmp/mnemo-autostart-curl-err; then
        elapsed=$(( $(date +%s) - start_ts ))
        log "daemon healthy at ${MNEMO_HEALTH_URL} after ${elapsed}s"
        exit 0
    fi
    last_err="$(cat /tmp/mnemo-autostart-curl-err 2>/dev/null || true)"
    sleep 1
done

log "FATAL: daemon did not answer ${MNEMO_HEALTH_URL} within ${MNEMO_AUTOSTART_TIMEOUT}s. Last error: ${last_err}"
exit 1
