---
description: Capture the current insight as a new mnemo memory node
---

Walk the user through capturing an insight as a new memory entry.

1. Ask the user for the **type** (one of: `user`, `feedback`, `project`, `reference`).
2. Ask for a short **name** (slug-style; the filename stem).
3. Ask for a one-line **description** (this is what shows up in retrieval citations).
4. Ask for the **body** (the actual content; can be multi-paragraph).
5. Ask which project it belongs to (or `(global)`).

Before writing, check for novelty:

```bash
mnemo query "<description>" --json --k 5
```

If a hit comes back with high similarity (`score > 0.7`) and the same type,
ask the user whether to **supersede** the existing entry or **cancel**.

When confirmed, write the file to:

- Project memory: `~/.claude/projects/<project-key>/memory/<type>_<name>.md`
- Global: `~/.claude/CLAUDE.md` (append a section, not a new file)

Use the standard frontmatter format:

```markdown
---
name: <name>
description: <description>
type: <type>
---

<body>
```

The `PostToolUse` hook will pick up the new file automatically and trigger a
reindex. No manual `mnemo reindex` needed.
