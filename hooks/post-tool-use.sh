#!/usr/bin/env bash
# mnemo PostToolUse hook - if the tool just edited a memory-shaped file,
# trigger an async reindex (data-only, no embedding) so subsequent retrievals
# pick up the change. Hash-gated, so reindex is a no-op when nothing changed.

set -uo pipefail

if ! command -v mnemo >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
    exit 0
fi

input=$(cat)

file_path=$(printf '%s' "$input" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    ti = data.get('tool_input') or {}
    print(ti.get('file_path') or '')
except Exception:
    pass
" 2>/dev/null)

if [ -z "$file_path" ]; then
    exit 0
fi

case "$file_path" in
    */memory/*.md|*/CLAUDE.md|*/docs/plans/*.md)
        # Background, detached, no waiting.
        ( mnemo reindex --no-embed >/dev/null 2>&1 & disown ) >/dev/null 2>&1
        ;;
esac

exit 0
