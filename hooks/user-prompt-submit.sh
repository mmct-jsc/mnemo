#!/usr/bin/env bash
# mnemo UserPromptSubmit hook - run hybrid Graph-RAG retrieval against the
# user's prompt and inject budget-capped citations as additional context.
# Fails open: silent if mnemo or python3 is missing, or if retrieval fails.

set -uo pipefail

if ! command -v mnemo >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
    exit 0
fi

input=$(cat)

prompt=$(printf '%s' "$input" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get('prompt', ''))
except Exception:
    pass
" 2>/dev/null)

if [ -z "$prompt" ]; then
    exit 0
fi

result=$(mnemo query "$prompt" --json --budget 800 --k 5 2>/dev/null) || exit 0

printf '%s' "$result" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
hits = data.get('hits', [])
if not hits:
    sys.exit(0)
print('## Relevant memory (mnemo)')
print()
for h in hits:
    desc = (h.get('description') or '').replace('\n', ' ')
    print(f\"- {h.get('citation','')} [{h.get('type','')}] {h.get('name','')}: {desc}\")
    body = h.get('body')
    if body:
        snippet = body if len(body) <= 400 else body[:400].rstrip() + '...'
        for line in snippet.splitlines():
            print(f'  {line}')
print()
tags = ', '.join(data.get('intent_tags', []))
print(f\"intent: {tags or 'none'} | tokens used: {data.get('tokens_used', 0)} | k: {len(hits)}\")
"
