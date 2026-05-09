---
description: Toggle mnemo's automatic context injection (UserPromptSubmit hook)
argument-hint: <on|off>
---

mnemo's hook auto-injects budget-capped retrieval results on every prompt.
Use this command to turn that off (e.g., when working in an unrelated repo
where the global memory isn't useful) and back on later.

The toggle is a flag in `~/.claude/settings.json` under the mnemo plugin
config block. The hook itself reads the flag on each fire and skips
silently when off.

When `$ARGUMENTS` is `off`:

```bash
# Edit ~/.claude/settings.json so plugins.mnemo.config.auto_inject = false
```

When `$ARGUMENTS` is `on`:

```bash
# Edit ~/.claude/settings.json so plugins.mnemo.config.auto_inject = true
```

Confirm the new state by reading the file back. The setting takes effect on
the next prompt; no restart needed.
