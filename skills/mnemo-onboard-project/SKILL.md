---
name: mnemo-onboard-project
description: Use when first introducing a new repository to mnemo. Scans the repo, extracts conventions, builds an initial memory snapshot, and registers the relevant sources so future sessions in this repo benefit from typed retrieval.
---

# Onboard Project

**Type:** flexible. The 5 phases below are a starting recipe; adapt
depending on what the repo has (a polished CLAUDE.md vs. nothing at all).

## Phase 1 - Scan

Identify what's worth ingesting in this repo:

```bash
# repo root
ls
ls -la

# obvious memory shapes
find . -maxdepth 4 -name "CLAUDE.md" -not -path "*/node_modules/*"
find . -maxdepth 4 -path "*/docs/plans/*.md"
find . -maxdepth 2 -name "README.md"
```

Note also:
- Build manifests: `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`.
- CI config: `.github/workflows/`, `Jenkinsfile`, etc.
- Deploy hints: `Dockerfile`, `docker-compose.yml`, `Makefile`.

## Phase 2 - Extract conventions

Based on what you found, infer:

| Convention | Source |
|---|---|
| Test framework + command | manifest scripts, README, existing test dir |
| Lint / format command | manifest config, pre-commit hooks |
| Branch / commit style | `git log --oneline -50`, repo CLAUDE.md |
| Deploy target | Dockerfile, deploy/, infra/ |
| Module layout | top-level dirs (src/, app/, internal/, etc.) |
| External services | env example files, config |

For ambiguous calls, **ask the user** - it's faster than guessing wrong.

## Phase 3 - Build initial nodes

Write 3-7 starter memory entries via `mnemo-add-knowledge`:

1. **`project_overview.md`** (type=`project`)
   - One-line description of what the repo does.
   - Tech stack (languages, key frameworks).
   - Where the work happens (which dirs are load-bearing).

2. **`project_test_and_lint.md`** (type=`project`)
   - Exact commands to run tests and lint.
   - Any non-obvious test setup.

3. **`project_deploy.md`** (type=`project`)
   - How code reaches production (CI/CD pipeline, manual deploy, etc.).
   - Where logs and dashboards live.

4. **`reference_external.md`** (type=`reference`) - if the repo points
   to external systems (Linear board, monitoring dashboard, on-call
   runbook URL).

5. **`feedback_*`** - capture any explicit user preferences for this
   repo (commit style, comment style, branch naming) as `feedback` type.

## Phase 4 - Link to global patterns

Query mnemo for cross-project patterns that apply here:

```bash
mnemo query "<repo's domain>" --k 8
mnemo query "<tech stack> patterns" --k 5
```

For each highly relevant hit, add an `appliesTo` link from the relevant
new node back to the global pattern. The graph layer surfaces the
cross-project transfer on later queries.

## Phase 5 - User confirm + register sources

1. Show the user the 3-7 entries you just drafted. Get **explicit approval**
   before committing them to memory - first impressions persist for the life
   of the project.
2. Register the repo's `CLAUDE.md` and `docs/plans/` (if they exist) as
   mnemo sources:
   ```bash
   mnemo source add /path/to/repo/CLAUDE.md --kind claude_md --project-key <key>
   mnemo source add /path/to/repo/docs/plans --kind plan_dir --project-key <key>
   ```
3. Run a full reindex:
   ```bash
   mnemo reindex
   ```
4. Sanity-check by querying:
   ```bash
   mnemo query "test command for this repo" --project <key>
   ```

## Cross-cutting

- Don't dump everything from the README into mnemo. Memory is for the
  **non-obvious**: things that would be re-derived from scratch in a
  future session if not captured. Trivia goes in CLAUDE.md, not memory.
- Use absolute dates (YYYY-MM-DD) when capturing time-sensitive facts.
- Less is more: 5 high-quality entries beat 50 noisy ones. The retrieval
  scorer rewards distinctiveness.
