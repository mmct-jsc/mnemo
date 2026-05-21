# Pull request

## Summary

<!-- 1-3 bullets describing what this PR does and why. Lead with the why. -->

## Test plan

- [ ] `cd daemon && uv run ruff check .` clean
- [ ] `cd daemon && uv run ruff format --check .` clean
- [ ] `cd daemon && uv run pytest tests/unit -q` green
- [ ] `cd daemon && uv run pytest tests/integration -q` green (if touched)
- [ ] Manual smoke (describe what you exercised by hand)

## Hard rules (CONTRIBUTING.md)

- [ ] No emojis in code, docs, or commit messages (unless explicitly requested)
- [ ] Conventional commit prefix: `feat:` / `fix:` / `chore:` / `docs:` / `test:` / `refactor:` / `perf:`
- [ ] Multi-line commit messages used HEREDOC (no `git commit -m "line1\nline2"`)
- [ ] No `0.0.0.0` bind anywhere; `127.0.0.1` only

## Out-of-scope check

- [ ] Does not introduce new runtime deps without prior discussion
- [ ] Does not touch the scoring weights or retrieval pipeline without
      benchmark numbers (see `docs/benchmarks.md`)
- [ ] Does not add cloud-only or multi-user behavior (see roadmap non-goals)

## Related

<!-- Links to issues / docs / earlier discussion -->
