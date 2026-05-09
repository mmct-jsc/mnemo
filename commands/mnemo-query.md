---
description: Search mnemo memory for relevant context (typed, cited, budget-capped)
argument-hint: <query text>
---

Run hybrid Graph-RAG retrieval against the mnemo store and present the hits.

```bash
mnemo query "$ARGUMENTS" --json --budget 800 --k 5
```

Format the JSON response as a list:

```
[mnemo:<id>] [<type>] <name>: <description>
  <body snippet, if present>
```

Always include the `[mnemo:<id>]` citations so future tools can resolve them
back to the source file via `mnemo node show <id>` or the UI at
http://127.0.0.1:7373/node/<id>.
