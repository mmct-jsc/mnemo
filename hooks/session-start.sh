#!/usr/bin/env bash
# mnemo SessionStart hook - inject a small "memory map" so Claude knows what's
# available in mnemo this session. Fails open: if mnemo isn't installed or
# the daemon is down, prints nothing rather than blocking the conversation.

set -uo pipefail

if ! command -v mnemo >/dev/null 2>&1; then
    exit 0
fi

status=$(mnemo status 2>/dev/null) || exit 0

cat <<EOF
## mnemo memory map

\`\`\`
${status}
\`\`\`

Use \`/mnemo-query <text>\` for ad-hoc memory recall, or let auto-injection do it for you.
EOF
