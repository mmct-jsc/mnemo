# Security policy

## Supported versions

Only the latest minor version of mnemo (`1.x`) receives security patches.
Earlier versions are not supported.

| Version | Supported |
|---------|-----------|
| 1.x     | yes       |
| 0.x     | no (pre-release; never published) |

## Reporting a vulnerability

**Do not open a public issue.** mnemo runs on localhost and never makes
outbound network calls except for the one-time HuggingFace model
download, but a vulnerability in the daemon could still expose the
user's memory to other processes on the same machine.

If you find a security issue, email **security@mnemo.dev** (or open a
[GitHub security advisory](https://github.com/mmct-jsc/mnemo/security/advisories/new)).
Include:

- A description of the issue
- Steps to reproduce
- Affected versions (`mnemo status`)
- Your assessment of impact

We aim to respond within 7 days and ship a fix within 30 days for
high-severity issues.

## Threat model

mnemo is **single-user, local-only by design**. The daemon binds to
`127.0.0.1` and never `0.0.0.0`. There is no auth layer because
listening on the loopback interface is the auth boundary.

**In scope:**

- SQL injection in any user-supplied input (prompts, paths, settings).
- Path traversal in source registration / `mnemo source add`.
- Arbitrary code execution via crafted memory frontmatter.
- Privilege escalation via the `mnemo` CLI shim.
- Information disclosure beyond what the user already has access to.

**Explicitly out of scope:**

- A second user on the same machine being able to read the SQLite
  database. This is a filesystem permissions question; mnemo doesn't
  encrypt at rest.
- Compromise of the HuggingFace model cache. We trust the
  `sentence-transformers/all-MiniLM-L6-v2` model and its hash.
- Compromise of pulled memory content. mnemo stores what's there; it
  does not validate that user-written memory entries are correct.
- Denial of service against the daemon by querying very fast. The
  daemon is single-process and not designed to handle adversarial load.

## Hardening tips

For users who want extra defense in depth:

- Run mnemo under a least-privileged user (no sudo).
- Restrict `~/.claude/mnemo/` to your user only: `chmod 700`.
- Disable the auto-injection hook (`/mnemo-hooks off`) when working in
  repos where you don't want global memory injected.
- Keep `mnemo` and its dependencies updated; Dependabot opens weekly
  PRs with security fixes.
