---
description: Manually rescan registered sources and update the mnemo store
---

Run a full mnemo reindex. Idempotent and hash-gated, so unchanged files are
skipped.

```bash
mnemo reindex
```

Or, to skip embedding (data-only update, much faster):

```bash
mnemo reindex --no-embed
```

Or, to reindex a single registered source:

```bash
mnemo reindex --source <path>
```

Report the JSON output to the user (added / updated / unchanged / removed).
