---
description: Show the full body of a mnemo node by its ID
argument-hint: <node-id>
---

Look up a single mnemo node by its ID (the value inside `[mnemo:<id>]`
citations) and print its frontmatter + body.

```bash
mnemo node show "$ARGUMENTS"
```

Or, for JSON output:

```bash
mnemo node show "$ARGUMENTS" --json
```
