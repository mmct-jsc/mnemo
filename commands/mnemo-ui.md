---
description: Open the mnemo web UI in the default browser
---

Make sure the daemon is running, then open the local UI at
http://127.0.0.1:7373/.

```bash
mnemo daemon status
```

If the daemon is not running, start it first:

```bash
mnemo daemon start
```

Then open the UI:

- macOS: `open http://127.0.0.1:7373/`
- Linux: `xdg-open http://127.0.0.1:7373/`
- Windows: `start http://127.0.0.1:7373/`

The UI exposes search, an interactive node-link graph, per-node edit forms,
the source registry, and the query audit log.
