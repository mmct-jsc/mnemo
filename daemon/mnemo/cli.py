"""mnemo command-line interface (Typer).

Entry point exposed by ``[project.scripts]`` in ``pyproject.toml``::

    [project.scripts]
    mnemo = "mnemo.cli:app"

Subcommands group naturally:

- ``mnemo init``                   one-time setup
- ``mnemo reindex``                ingest + (re-)embed
- ``mnemo query <text>``           one-shot retrieval
- ``mnemo status``                 quick health summary
- ``mnemo source {add,list,remove}``
- ``mnemo node {show}``
- ``mnemo daemon {start,stop,restart,status}``
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import typer

from mnemo import __version__, daemon, paths

if TYPE_CHECKING:
    from mnemo.embed import Embedder as _EmbedderT
    from mnemo.store import Store

# v5.25.0: the heavy modules (ingest -> store -> sqlite_vec -> numpy, the
# embedder stack, auto_router) are imported FUNCTION-LOCALLY in the commands
# that need them. Every Claude Code hook fire and statusline refresh spawns a
# fresh python that imports this module; live profiling showed the old
# module-top imports costing ~1.1s of a 1.6s cold start that most spawns
# never used. Guarded by tests/unit/test_cli_light_import.py.

log = logging.getLogger(__name__)

app = typer.Typer(
    name="mnemo",
    help="mnemo - local-first knowledge memory for Claude Code",
    no_args_is_help=True,
)
source_app = typer.Typer(help="Manage ingestion sources", no_args_is_help=True)
node_app = typer.Typer(help="Inspect and edit nodes", no_args_is_help=True)
daemon_app = typer.Typer(help="Daemon process lifecycle", no_args_is_help=True)
key_app = typer.Typer(
    help="Manage hosted API keys (Phase 3). The self-host loopback "
    "is NOT affected -- keys only matter when the hosted-tier "
    "auth flag is on.",
    no_args_is_help=True,
)
billing_app = typer.Typer(
    help="Hosted-tier billing reports. CSV-out so it pipes to your "
    "spreadsheet / billing system without translation.",
    no_args_is_help=True,
)
app.add_typer(source_app, name="source")
app.add_typer(node_app, name="node")
app.add_typer(daemon_app, name="daemon")
app.add_typer(key_app, name="key")
app.add_typer(billing_app, name="billing")

# v5.24.0: hook entrypoints invoked by hooks/hooks.json. A subcommand group
# so the SAME `mnemo` binary serves all three Claude Code hook events
# cross-platform -- no .sh/.ps1 split, no `python3`-on-PATH dependency, and
# the logic is unit-testable Python instead of dual shell scripts.
hook_app = typer.Typer(
    help="Internal entrypoints invoked by hooks/hooks.json. Not for direct use.",
    no_args_is_help=True,
)
app.add_typer(hook_app, name="hook")


def _open_store() -> Store:
    from mnemo.store import Store

    paths.ensure_runtime_dirs()
    return Store(paths.db_path())


def Embedder(*args: object, **kwargs: object) -> _EmbedderT:  # noqa: N802
    """Lazy class-shaped factory for :class:`mnemo.embed.Embedder`.

    A function, not a module-top import, so ``import mnemo.cli`` -- paid by
    every hook fire and statusline refresh in a fresh python -- does not pull
    the embedder stack. Tests monkeypatch THIS name (``mnemo.cli.Embedder``),
    which keeps working unchanged."""
    from mnemo.embed import Embedder as _Embedder

    return _Embedder(*args, **kwargs)


def _is_memory_shaped(file_path: str) -> bool:
    """True if an edited path is one mnemo indexes (so a reindex is worth it).

    Mirrors the PostToolUse glob the old hook script used:
    ``*/memory/*.md`` | ``*/CLAUDE.md`` | ``*/docs/plans/*.md``.
    """
    p = (file_path or "").replace("\\", "/")
    if not p:
        return False
    return (
        ("/memory/" in p and p.endswith(".md"))
        or p.endswith("/CLAUDE.md")
        or p == "CLAUDE.md"
        or ("/docs/plans/" in p and p.endswith(".md"))
    )


def _spawn_background_reindex() -> None:
    """Fire-and-forget a data-only reindex so later retrievals see the edit.

    Detached + best-effort: never waits, swallows failures. Uses the current
    interpreter (`-m mnemo.cli`) so it works regardless of how `mnemo` is
    shimmed onto PATH. Monkeypatched out in tests.
    """
    import subprocess
    import sys
    import time

    # v5.25.0: debounce. Bursts of memory edits while the daemon is down
    # (daemon-up edits nudge /v1/reindex instead, which has server-side
    # single-flight) must not pile up concurrent full-corpus subprocesses --
    # 4 overlapping reindex pythons were observed live.
    try:
        stamp = paths.mnemo_home() / "reindex-nudge.stamp"
        if stamp.exists() and (time.time() - stamp.stat().st_mtime) < 60:
            return
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(int(time.time())), encoding="utf-8")
    except Exception:
        pass

    try:
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "mnemo.cli", "reindex", "--no-embed"],
            **kwargs,
        )
    except Exception:
        # A reindex nudge is best-effort; never let it surface to the hook.
        pass


def _read_stdin_json() -> dict | None:
    """Parse a hook payload from stdin; None on any failure (hooks fail open).

    Parses from the first ``{``: Windows shells prepend a UTF-8 BOM to every
    pipe (PowerShell 5.1 always does), and depending on the console codepage
    it reaches python as U+FEFF or as the cp1252 mojibake ``\\xef\\xbb\\xbf``
    -- either way ``json.loads`` rejects it, which made the hook silently
    fail open instead of doing its job. Hook payloads are always JSON
    objects, so brace-seeking is safe."""
    import sys

    raw = sys.stdin.read()
    start = raw.find("{")
    if start == -1:
        return None
    try:
        data = json.loads(raw[start:])
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _daemon_query(
    prompt: str, *, k: int = 5, budget_tokens: int = 800, cwd: str | None = None
) -> dict | None:
    """POST /v1/query to the local daemon (warm model, loopback-exempt auth).

    Returns the parsed QueryOut dict, or None on ANY failure so the caller
    falls back to the in-process path. The daemon answers in ~100ms; the
    in-process fallback loads the embedder per spawn, which live profiling
    showed costing up to ~50s under HuggingFace rate limits. ``cwd`` lets
    the server auto-scope the query to the caller's project (v5.26.0)."""
    import urllib.request

    payload: dict[str, object] = {"prompt": prompt, "k": k, "budget_tokens": budget_tokens}
    if cwd:
        payload["cwd"] = cwd
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{daemon.DEFAULT_PORT}/v1/query",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15.0) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("hits"), list) else None


def _nudge_daemon_reindex() -> bool:
    """Ask the daemon to reindex (data-only). True = nudge satisfied.

    POST /v1/reindex has SERVER-side single-flight (HTTP 409 + reindex_lock
    when one is already running -- that 409 counts as satisfied). The
    endpoint is a sync handler in the daemon's threadpool, so a client
    read-timeout abandons the RESPONSE, not the reindex: timeout also
    counts as satisfied. Only a connection failure (daemon down) returns
    False, sending the caller to the subprocess fallback."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{daemon.DEFAULT_PORT}/v1/reindex?embed=false",
        data=b"",
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5):  # noqa: S310
            return True
    except urllib.error.HTTPError as exc:
        return exc.code == 409
    except urllib.error.URLError as exc:
        return isinstance(exc.reason, TimeoutError)
    except TimeoutError:
        return True
    except Exception:
        return False


# --- Top-level commands ----------------------------------------------------


@app.command()
def init() -> None:
    """Create runtime dirs and register the default Scope B sources."""
    from mnemo import ingest

    store = _open_store()
    try:
        n = ingest.register_default_sources(store, paths.claude_home())
        typer.echo(f"Registered {n} new default sources")
        typer.echo(f"Total: {len(store.list_sources())} source(s)")
    finally:
        store.close()


@app.command()
def reindex(
    source: str | None = typer.Option(
        None, "--source", help="Reindex only this single source path"
    ),
    no_embed: bool = typer.Option(False, "--no-embed", help="Skip embedding (data-only reindex)"),
) -> None:
    """Scan registered sources and update the store. Embeds new/changed nodes."""
    from mnemo import ingest

    store = _open_store()
    try:
        sources = None
        if source is not None:
            matches = [s for s in store.list_sources() if s.path == source]
            if not matches:
                typer.echo(f"source not found: {source}", err=True)
                raise typer.Exit(code=1)
            sources = matches
        embedder = None if no_embed else Embedder()
        report = ingest.reindex(store, sources=sources, embedder=embedder)
        typer.echo(
            json.dumps(
                {
                    "added": report.added,
                    "updated": report.updated,
                    "unchanged": report.unchanged,
                    "removed": report.removed,
                    # Nonzero means these nodes had been invisible to semantic
                    # search until this run repaired them. Steady state is 0.
                    "embedded_backfilled": report.embedded_backfilled,
                    "errors": [list(e) for e in report.errors],
                },
                indent=2,
            )
        )
    finally:
        store.close()


@app.command("migrate-code-identity")
def migrate_code_identity(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Run against the LIVE store and commit (default: dry-run on a throwaway copy)",
    ),
) -> None:
    """v5.28.0: migrate code nodes to line-stable <file>::<qualified_name>
    keys and report the impact.

    Default is a DRY RUN against a COPY of the live DB -- it touches
    nothing live and prints how many legacy nodes would be re-keyed in
    place (id preserved) vs orphaned. Review those numbers before
    --apply (the same migration also happens lazily on any reindex).
    """
    from mnemo import migrate_identity

    if apply:
        store = _open_store()
        try:
            report = migrate_identity.run_code_identity_migration(store, embedder=Embedder())
        finally:
            store.close()
    else:
        report = migrate_identity.dry_run_from_db(paths.db_path())
    typer.echo(json.dumps(report, indent=2))


@app.command()
def query(
    prompt: str = typer.Argument(..., help="Query text"),
    k: int = typer.Option(5, "--k", help="Number of hits to return"),
    budget: int = typer.Option(800, "--budget", help="Token budget"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of human text"),
    project: str | None = typer.Option(None, "--project", help="Active project key"),
    auto_scope: bool = typer.Option(
        True,
        "--auto-scope/--no-auto-scope",
        help=(
            "Scope the query to the current directory's project when it is "
            "indexed (v5.26.0 default). --project always wins."
        ),
    ),
    exclude_local_only: bool = typer.Option(
        False,
        "--exclude-local-only",
        help=(
            "Filter out local_only-flagged nodes (frontmatter local_only=true, "
            "any _private path segment, or [LOCAL ONLY] body marker). Use "
            "this when the retrieval result will be pasted into a foreign LLM "
            "(Cursor / Claude Code / Continue / Copilot). The v5.8.0 "
            "/mnemo-prompt slash command always passes this."
        ),
    ),
) -> None:
    """One-shot retrieval. Hooks call this to fetch context."""
    import os

    from mnemo import retrieve

    store = _open_store()
    try:
        # v5.26.0: auto-scope to the cwd's project keys unless an explicit
        # --project was given or --no-auto-scope opts out. The has-nodes
        # guard inside resolve_auto_scope keeps unindexed dirs unscoped.
        scope_kwargs: dict = {"active_project": project}
        if project is None and auto_scope:
            keys, _indexed = retrieve.resolve_auto_scope(store, os.getcwd())
            scope_kwargs = {"active_projects": keys or None}
        embedder = Embedder()
        result = retrieve.query(
            store,
            embedder,
            prompt,
            k=k,
            budget_tokens=budget,
            exclude_local_only=exclude_local_only,
            **scope_kwargs,
        )
        if json_out:
            typer.echo(
                json.dumps(
                    {
                        "intent_tags": result.intent_tags,
                        "tokens_used": result.tokens_used,
                        "query_id": result.query_id,
                        "hits": [
                            {
                                "node_id": h.node_id,
                                "type": h.type,
                                "name": h.name,
                                "description": h.description,
                                "body": h.body,
                                "score": h.score,
                                "chunk_idx": h.chunk_idx,
                                "citation": h.citation,
                            }
                            for h in result.hits
                        ],
                    },
                    indent=2,
                )
            )
        else:
            typer.echo(f"intent: {result.intent_tags}  tokens_used: {result.tokens_used}")
            for h in result.hits:
                desc = (h.description or "")[:80]
                typer.echo(f"  {h.citation} [{h.type}] {h.name}: {desc}")
    finally:
        store.close()


@app.command()
def ui(
    host: str = typer.Option(daemon.DEFAULT_HOST, "--host"),
    port: int = typer.Option(daemon.DEFAULT_PORT, "--port"),
    no_start: bool = typer.Option(False, "--no-start", help="Don't auto-start the daemon"),
) -> None:
    """Open the mnemo web UI in the default browser. Auto-starts the daemon."""
    import webbrowser

    url = f"http://{host}:{port}/"
    d = daemon.status()
    if not d.running and not no_start:
        typer.echo("daemon not running; starting...")
        try:
            pid = daemon.start(host=host, port=port)
            typer.echo(f"daemon started (pid {pid})")
        except RuntimeError as exc:
            typer.echo(f"failed to start daemon: {exc}", err=True)
            typer.echo(f"open {url} manually after starting it.", err=True)
            raise typer.Exit(code=1) from exc
    elif d.running:
        typer.echo(f"daemon already running (pid {d.pid})")

    typer.echo(f"opening {url}")
    webbrowser.open(url)


@app.command()
def retune(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Skip the y/N prompt and persist the proposed weights immediately.",
    ),
    min_queries: int | None = typer.Option(
        None,
        "--min-queries",
        help="Override the default labeled-query threshold (config.retune_min_queries).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
) -> None:
    """Auto-tune the 6 scoring weights from accumulated feedback.

    Runs coordinate descent over the alpha..zeta weights against the
    audit-log + feedback_event signal. Prints a diff + before/after
    MRR; persists only after the user confirms (or ``--apply``).

    Below the minimum-labeled-queries threshold the command exits with
    a friendly message so you can come back later.
    """
    from mnemo import config as cfg_mod
    from mnemo.retune import retune as do_retune

    cfg = cfg_mod.load()
    threshold = min_queries if min_queries is not None else cfg.retune_min_queries

    store = _open_store()
    try:
        report = do_retune(store, min_queries=threshold)
    finally:
        store.close()

    if json_out:
        import dataclasses

        typer.echo(json.dumps(dataclasses.asdict(report), indent=2))
        return

    if report.train_size == 0 and report.val_size == 0:
        # Below threshold or no labeled data -- the report's log line
        # carries the human-readable reason.
        for line in report.log:
            typer.echo(line)
        return

    typer.echo("--- proposed weight changes ---")
    moved = False
    for k in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta"):
        cur = report.current[k]
        new = report.proposed[k]
        diff = report.diff[k]
        if abs(diff) < 1e-9:
            typer.echo(f"  {k:<8} {cur:.4f}  (unchanged)")
        else:
            moved = True
            sign = "+" if diff > 0 else ""
            typer.echo(f"  {k:<8} {cur:.4f} -> {new:.4f}  ({sign}{diff:.4f})")
    typer.echo("")
    typer.echo(
        f"val_mrr:   {report.val_mrr_before:.4f} -> {report.val_mrr_after:.4f}"
        f"  ({'+' if report.val_mrr_after >= report.val_mrr_before else ''}"
        f"{report.val_mrr_after - report.val_mrr_before:.4f})"
    )
    typer.echo(f"train_mrr: {report.train_mrr_before:.4f} -> {report.train_mrr_after:.4f}")
    typer.echo(f"samples:   train={report.train_size}, val={report.val_size}")
    typer.echo(f"iterations: {report.iterations} in {report.elapsed_seconds:.2f}s")
    typer.echo("--- log ---")
    for line in report.log:
        typer.echo(f"  {line}")

    if not moved:
        typer.echo("\nNothing to apply -- weights unchanged.")
        return

    if not apply:
        confirm = typer.confirm("\nApply these weights to ~/.claude/mnemo/settings.json?")
        if not confirm:
            typer.echo("Discarded; no changes written.")
            return

    cfg_mod.update({"scoring": report.proposed})
    typer.echo("Weights applied.")


@app.command()
def status() -> None:
    """Show node/source counts and daemon status."""
    store = _open_store()
    try:
        counts = store.count_nodes()
        d = daemon.status()
        typer.echo(f"version: {__version__}")
        typer.echo(f"db:      {paths.db_path()}")
        typer.echo(f"nodes:   {sum(counts.values())} total")
        for t, n in sorted(counts.items()):
            typer.echo(f"           {t}: {n}")
        typer.echo(f"sources: {len(store.list_sources())}")
        typer.echo(
            "daemon:  "
            + ("running" if d.running else "stale" if d.stale else "stopped")
            + (f" (pid {d.pid})" if d.pid else "")
        )
    finally:
        store.close()


@app.command()
def doctor() -> None:
    """End-to-end install check: PATH, index, plugin registration, daemon, MCP.

    Prints a [ok]/[FAIL]/[?] checklist with a concrete fix for each problem
    and exits nonzero if a REQUIRED link is broken -- replacing the old
    silent fail-open where a half-wired install looked identical to a working
    one. Run it after installing, or any time mnemo "isn't doing anything".
    """
    from mnemo import doctor as doctor_mod

    text, code = doctor_mod.render(doctor_mod.gather())
    typer.echo(text)
    raise typer.Exit(code=code)


# --- source subcommands ----------------------------------------------------


@source_app.command("add")
def source_add(
    path: str,
    kind: str | None = typer.Option(
        None,
        "--kind",
        help=(
            "memory_dir | claude_md | plan_dir | transcripts | code_repo | docs_dir. "
            "Omit to let the auto-router propose a kind based on the path's contents."
        ),
    ),
    project_key: str | None = typer.Option(None, "--project-key"),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the auto-router confirmation prompt.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Bypass the 50k-file safety ceiling. Only relevant when the "
            "auto-router scans a large code_repo."
        ),
    ),
) -> None:
    """Register a source. v2.0 phase 2: omit ``--kind`` to use the auto-router.

    The auto-router scans the path on disk, proposes a kind
    (``code_repo`` / ``memory_dir`` / ``docs_dir``) plus a confidence,
    and prints a per-extension file breakdown. It never writes the row
    without explicit confirmation -- pass ``--yes`` to skip the
    interactive prompt in scripts.

    Explicit ``--kind`` skips the auto-router entirely; the user has
    committed to a classification and the source row is written
    immediately. The safety ceiling does not apply on this path.
    """
    if kind is None:
        # Auto-router path.
        from mnemo import auto_router

        try:
            result = auto_router.preview(path, force=force)
        except FileNotFoundError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from None

        # Print the breakdown so the user can sanity-check before writing.
        typer.echo(f"path: {result.path}")
        typer.echo(
            f"proposed kind: {result.proposed_kind or '(none)'}  (confidence: {result.confidence})"
        )
        typer.echo(f"total files (after skip-dirs): {result.breakdown.total_files}")
        if result.breakdown.by_ext:
            typer.echo("by extension:")
            for ext, count in sorted(result.breakdown.by_ext.items(), key=lambda kv: -kv[1]):
                typer.echo(f"  {ext or '(none)':<12} {count}")

        if result.exceeds_safety_ceiling:
            typer.echo(
                "refusing: this path exceeds the "
                f"{auto_router.SAFETY_CEILING}-file safety ceiling. "
                "Re-run with --force to override, or narrow the source "
                "with `--include` / `--exclude` after registration.",
                err=True,
            )
            raise typer.Exit(code=1)

        if result.proposed_kind is None:
            typer.echo(
                "auto-router couldn't propose a kind. Re-run with "
                "--kind <memory_dir|claude_md|plan_dir|code_repo|docs_dir> "
                "to register explicitly."
            )
            raise typer.Exit(code=1)

        if not yes:
            confirmed = typer.confirm(f"Register as {result.proposed_kind}?", default=False)
            if not confirmed:
                typer.echo("cancelled")
                raise typer.Exit(code=0)
        kind = result.proposed_kind

    store = _open_store()
    try:
        store.register_source(path, kind, project_key=project_key)
        typer.echo(f"registered: {path}  [{kind}]")
    finally:
        store.close()


@source_app.command("list")
def source_list(
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    store = _open_store()
    try:
        sources = store.list_sources()
        if json_out:
            typer.echo(
                json.dumps(
                    [
                        {
                            "path": s.path,
                            "kind": s.kind,
                            "project_key": s.project_key,
                            "last_indexed_at": s.last_indexed_at,
                            "enabled": s.enabled,
                        }
                        for s in sources
                    ],
                    indent=2,
                )
            )
        else:
            for s in sources:
                indexed = "never" if s.last_indexed_at is None else f"@{s.last_indexed_at}"
                typer.echo(f"  [{s.kind:12}] {s.path}  pk={s.project_key}  {indexed}")
    finally:
        store.close()


@source_app.command("remove")
def source_remove(path: str) -> None:
    """Unregister a source and cascade-delete every node ingested from it.

    v1.1.1: cascade is automatic. The command prints the number of nodes
    cleaned up alongside the source path.
    """
    store = _open_store()
    try:
        removed = store.remove_source(path)
        if removed:
            noun = "node" if removed == 1 else "nodes"
            typer.echo(f"removed: {path}  ({removed} {noun} cleaned up)")
        else:
            typer.echo(f"removed: {path}")
    finally:
        store.close()


@source_app.command("orphans")
def source_orphans(
    prune: bool = typer.Option(False, "--prune", help="Delete the orphan nodes."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
) -> None:
    """List (or prune) nodes whose source_path matches no registered source.

    Pre-1.1.1 ``DELETE /v1/sources`` (or ``mnemo source remove``) didn't
    cascade -- removing a source left every node from it orphaned in the
    graph because the reindex orphan-sweep only walks nodes still under a
    registered source.

    Run this once after upgrading to v1.1.1 to surface and clean up the
    leftovers. Without ``--prune`` it just lists them.

    Examples::

        mnemo source orphans
        mnemo source orphans --prune
        mnemo source orphans --json
    """
    store = _open_store()
    try:
        orphans = store.find_orphan_nodes()
        if json_out:
            typer.echo(
                json.dumps(
                    [
                        {
                            "id": n.id,
                            "type": n.type,
                            "name": n.name,
                            "source_path": n.source_path,
                            "project_key": n.project_key,
                            "updated_at": n.updated_at,
                        }
                        for n in orphans
                    ],
                    indent=2,
                )
            )
        else:
            if not orphans:
                typer.echo("No orphan nodes.")
            else:
                typer.echo(f"Found {len(orphans)} orphan node(s):")
                # Cap the listing at 50; bigger lists are noise on a terminal.
                for n in orphans[:50]:
                    short_id = n.id[:8]
                    typer.echo(f"  {short_id}  [{n.type:18}]  {n.source_path}")
                if len(orphans) > 50:
                    typer.echo(f"  ... and {len(orphans) - 50} more")
        if prune and orphans:
            for n in orphans:
                store.delete_node(n.id)
            noun = "node" if len(orphans) == 1 else "nodes"
            typer.echo(f"Pruned {len(orphans)} orphan {noun}.")
    finally:
        store.close()


# --- node subcommands ------------------------------------------------------


@node_app.command("show")
def node_show(
    node_id: str,
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    store = _open_store()
    try:
        n = store.get_node(node_id)
        if n is None:
            typer.echo("not found", err=True)
            raise typer.Exit(code=1)
        if json_out:
            typer.echo(
                json.dumps(
                    {
                        "id": n.id,
                        "type": n.type,
                        "name": n.name,
                        "description": n.description,
                        "body": n.body,
                        "source_path": n.source_path,
                        "source_kind": n.source_kind,
                        "project_key": n.project_key,
                        "hash": n.hash,
                        "created_at": n.created_at,
                        "updated_at": n.updated_at,
                    },
                    indent=2,
                )
            )
        else:
            typer.echo(f"id:          {n.id}")
            typer.echo(f"type:        {n.type}")
            typer.echo(f"name:        {n.name}")
            typer.echo(f"description: {n.description or ''}")
            typer.echo(f"source:      {n.source_path}")
            typer.echo(f"updated_at:  {n.updated_at}")
            typer.echo("---")
            typer.echo(n.body)
    finally:
        store.close()


# --- daemon subcommands ----------------------------------------------------


@daemon_app.command("start")
def daemon_start(
    foreground: bool = typer.Option(False, "--foreground", help="Run in this process; don't fork."),
    host: str = typer.Option(daemon.DEFAULT_HOST, "--host"),
    port: int = typer.Option(daemon.DEFAULT_PORT, "--port"),
) -> None:
    if foreground:
        _run_foreground(host=host, port=port)
        return
    try:
        pid = daemon.start(host=host, port=port)
        typer.echo(f"daemon started (pid {pid}) on http://{host}:{port}")
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@daemon_app.command("stop")
def daemon_stop(port: int = typer.Option(daemon.DEFAULT_PORT, "--port")) -> None:
    if daemon.stop(port=port):
        typer.echo("daemon stopped")
    else:
        typer.echo("daemon not running")


@daemon_app.command("status")
def daemon_status(port: int = typer.Option(daemon.DEFAULT_PORT, "--port")) -> None:
    s = daemon.status(port=port)
    if s.running:
        typer.echo(f"running (pid {s.pid})")
    elif s.stale:
        typer.echo(f"stale pid file (pid {s.pid} not alive)")
    else:
        typer.echo("not running")


@daemon_app.command("restart")
def daemon_restart(
    host: str = typer.Option(daemon.DEFAULT_HOST, "--host"),
    port: int = typer.Option(daemon.DEFAULT_PORT, "--port"),
) -> None:
    """Stop the daemon (if running) then start it fresh.

    One command instead of ``mnemo daemon stop && mnemo daemon start``
    (chaining ``&& start`` runs the *Windows* ``start`` program -- a
    blank console -- not the daemon). ``daemon.stop()`` blocks until
    the old process has actually exited, so the start is race-safe.
    """
    typer.echo("daemon stopped" if daemon.stop(port=port) else "daemon was not running")
    try:
        pid = daemon.start(host=host, port=port)
    except RuntimeError as exc:
        typer.echo(f"restart failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"daemon started (pid {pid}) on http://{host}:{port}")


def _run_foreground(*, host: str, port: int) -> None:
    """Foreground entry: write PID, run uvicorn, clean up PID on exit."""
    import uvicorn

    from mnemo.server import create_app

    daemon.write_pid_file(port=port)
    try:
        uvicorn.run(create_app(), host=host, port=port, log_level="info")
    finally:
        daemon.remove_pid_file(port=port)


@app.command()
def mcp() -> None:
    """Run the mnemo MCP server over stdio.

    Point an external MCP client (Cursor / Claude Desktop / Codex /
    Windsurf) at ``mnemo mcp`` to get mnemo's full tool surface --
    retrieval + the write/danger tools, risk-tagged (v3 phase 6)."""
    from mnemo import mcp_server

    mcp_server.serve_stdio()


# --- mnemo hook {session-start,user-prompt-submit,post-tool-use} ----------
#
# v5.24.0: the plugin's hooks/hooks.json invokes these. All three follow the
# CC plugin hook contract verified against the plugin-dev hook-development
# reference: emit context via raw stdout + exit 0, and FAIL OPEN (exit 0, no
# output) on any error so a down daemon / missing index never blocks a
# session. Replaces the 6 hooks/*.sh + *.ps1 scripts.


@hook_app.command("session-start")
def hook_session_start() -> None:
    """SessionStart: emit a JSON object -- a one-line user-visible banner
    (top-level ``systemMessage``, the "notify" channel) plus the memory map
    as model context (``hookSpecificOutput.additionalContext``). v5.25.0.

    Always ``json.dumps`` a dict (never hand-build) so the output is valid
    JSON, and FAIL OPEN (emit nothing, exit 0) on any error -- malformed
    JSON has no documented CC fallback, so a half-built object is worse
    than silence."""
    import os
    from pathlib import Path

    data = _read_stdin_json() or {}
    cwd = str(data.get("cwd") or os.getcwd())

    try:
        store = _open_store()
    except Exception:
        return
    unindexed_project = False
    try:
        total = sum(store.count_nodes().values())
        sources = len(store.list_sources())
        d = daemon.status()
        # v5.26.0 (user spec): detect the IDE's project; when it looks like
        # a real project (.git) but has zero indexed nodes, surface the
        # offer to index it. NEVER auto-index -- it is a user decision.
        try:
            if (Path(cwd) / ".git").exists():
                from mnemo import retrieve

                _keys, indexed = retrieve.resolve_auto_scope(store, cwd)
                unindexed_project = not indexed
        except Exception:
            unindexed_project = False
    except Exception:
        return
    finally:
        store.close()

    daemon_state = "running" if d.running else "stale" if d.stale else "stopped"
    context = "\n".join(
        [
            "## mnemo memory map",
            "",
            f"- version: {__version__}",
            f"- nodes: {total} across {sources} source(s)",
            f"- daemon: {daemon_state}",
            "",
            "Use `/mnemo-query <text>` for ad-hoc recall, or call the "
            "`mnemo_query` tool. Auto-injection adds cited memory to each "
            "prompt -- prefer it over grep for 'how/where/why' questions.",
        ]
    )
    if unindexed_project:
        context += (
            "\n\nNOTE: the current project is NOT indexed in mnemo. If the "
            "user wants project-scoped recall here, you may offer to run "
            f"`mnemo source add {cwd}` followed by `mnemo reindex` -- ask "
            "first; indexing is the user's decision."
        )
    payload: dict[str, object] = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }
    # The user-visible one-line banner. Opt-out via MNEMO_NO_SESSION_BANNER=1
    # (the model context stays either way; presence is never spammy).
    if not os.environ.get("MNEMO_NO_SESSION_BANNER"):
        banner = f"mnemo: {total:,} memories across {sources} source(s) -- /mnemo-query to recall"
        if unindexed_project:
            banner += f" | this project is not indexed -- run: mnemo source add {cwd}"
        payload["systemMessage"] = banner
    typer.echo(json.dumps(payload))


@hook_app.command("user-prompt-submit")
def hook_user_prompt_submit() -> None:
    """UserPromptSubmit: inject cited memory for the prompt (read from stdin).

    v5.25.0: DAEMON-FIRST. The running daemon answers /v1/query with its
    warm model in ~100ms. The in-process fallback (daemon down) loads the
    embedder inside this throwaway spawn, which live profiling showed
    costing up to ~50s under HuggingFace rate limits -- acceptable only as
    a fallback, never as the steady state (this hook fires on EVERY prompt
    in EVERY session)."""
    data = _read_stdin_json()
    if data is None:
        return
    prompt = (data.get("prompt") or data.get("user_prompt") or "").strip()
    if not prompt:
        return

    # v5.26.0: auto-scope to the caller's project. CC sends cwd in the hook
    # payload (and spawns hooks in the project dir, so os.getcwd() is the
    # faithful fallback). The daemon (or the in-process fallback below)
    # applies the has-nodes guard.
    import os

    cwd = str(data.get("cwd") or os.getcwd())

    # v6.1.0 governance: binding rules surface in their own section, separate
    # from (and above) the ranked memory pointer.
    gov_rules: list[dict] = []
    payload = _daemon_query(prompt, cwd=cwd)
    if payload is not None:
        rows = [
            {
                "citation": str(h.get("citation", "")),
                "type": str(h.get("type", "")),
                "name": str(h.get("name", "")),
                "description": str(h.get("description") or ""),
            }
            for h in payload.get("hits", [])
        ]
        intent_tags = [str(t) for t in payload.get("intent_tags") or []]
        gov_rules = [
            {
                "citation": str(gr.get("citation", "")),
                "modality": str(gr.get("modality", "")),
                "text": str(gr.get("text") or ""),
            }
            for gr in payload.get("rules") or []
        ]
    else:
        # Fallback: daemon down -- query in-process (the slow path).
        try:
            store = _open_store()
        except Exception:
            return
        try:
            from mnemo import governance, retrieve

            scope_keys, _indexed = retrieve.resolve_auto_scope(store, cwd)
            result = retrieve.query(
                store,
                Embedder(),
                prompt,
                k=5,
                budget_tokens=800,
                active_projects=scope_keys or None,
            )
            gov_rules = [
                {"citation": f"[mnemo:{r.node_id}]", "modality": r.modality, "text": r.text}
                for r in governance.active_rules(
                    store,
                    scope=set(scope_keys) if scope_keys else None,
                    intent_tags=set(result.intent_tags),
                )
            ]
        except Exception:
            return
        finally:
            store.close()
        rows = [
            {
                "citation": h.citation,
                "type": h.type,
                "name": h.name,
                "description": h.description or "",
            }
            for h in result.hits
        ]
        intent_tags = list(result.intent_tags)

    # record the injection size for `mnemo statusline` (best-effort; records
    # 0 too, so the bar drops `up{N}` when nothing was injected).
    try:
        from mnemo import statusline as statusline_mod

        statusline_mod.write_inject_count(data.get("session_id"), len(rows))
    except Exception:
        pass

    if not rows and not gov_rules:
        return
    out: list[str] = []
    if gov_rules:
        out.append("## Active rules (mnemo) -- binding")
        out.append("")
        out.extend(f"- {gr['citation']} {gr['modality']}: {gr['text']}" for gr in gov_rules)
        out.append("")
    if rows:
        out.append("## Relevant memory (mnemo)")
        out.append("")
        for r in rows:
            desc = r["description"].replace("\n", " ")
            out.append(f"- {r['citation']} [{r['type']}] {r['name']}: {desc}")
        out.append("")
        out.append(f"intent: {', '.join(intent_tags) or 'none'} | k: {len(rows)}")
    typer.echo("\n".join(out).rstrip())


_EXIT_CODE_KEYS = ("exit_code", "exitCode", "returnCode", "return_code", "code")
_EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def _bash_exit_code(data: dict) -> int | None:
    """Pull a Bash command's exit code out of the PostToolUse payload, across
    the shape variants Claude Code builds use. None when not surfaced."""
    tr = data.get("tool_response")
    if isinstance(tr, dict):
        for key in _EXIT_CODE_KEYS:
            v = tr.get(key)
            if isinstance(v, bool):  # guard: bools are ints in Python
                continue
            if isinstance(v, int):
                return v
    return None


def _response_has_error(data: dict) -> bool:
    """Best-effort failure signal when no explicit exit code is present."""
    tr = data.get("tool_response")
    if isinstance(tr, dict):
        return bool(tr.get("is_error") or tr.get("interrupted") or tr.get("error"))
    return False


def _governance_capture(data: dict) -> None:
    """v6.1.0 G3: capture EVIDENCE from the agent's real tool result -- record
    edited files, and stamp a rule's verify step satisfied when a matching
    command exits as expected. The agent cannot fake this; mnemo reads the
    actual result. Fully fail-open (no session, daemon contention, etc. -> no-op)."""
    session_id = str(data.get("session_id") or "")
    if not session_id:
        return
    tool_name = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input") or {}
    import os as _os

    cwd = str(data.get("cwd") or _os.getcwd())
    try:
        store = _open_store()
    except Exception:
        return
    try:
        fp = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if fp and tool_name in _EDIT_TOOLS:
            store.record_touched_file(session_id, fp)
        if tool_name == "Bash":
            command = str(tool_input.get("command") or "")
            if command:
                from mnemo import governance, retrieve

                scope_keys, _idx = retrieve.resolve_auto_scope(store, cwd)
                scope = set(scope_keys) if scope_keys else None
                exit_code = _bash_exit_code(data)
                for rule in governance.rules_with_verify(store, scope=scope):
                    if not (
                        rule.verify_command
                        and governance.command_satisfies_verify(rule.verify_command, command)
                    ):
                        continue
                    if exit_code is None:
                        # No exit code surfaced by this CC build: we cannot
                        # PROVE the command passed, so we do NOT stamp the gate
                        # satisfied (evidence-based -> never falsely open).
                        continue
                    ok = exit_code == rule.verify_expect_exit
                    store.record_governance_evidence(
                        session_id=session_id,
                        rule_id=rule.id,
                        step="verify",
                        status="satisfied" if ok else "failed",
                        evidence=f"{command} -> exit {exit_code}",
                    )
    except Exception:
        pass
    finally:
        store.close()


@hook_app.command("post-tool-use")
def hook_post_tool_use() -> None:
    """PostToolUse: reindex (data-only, detached) when a memory file is edited,
    and capture governance evidence (touched files + verify-command results)."""
    data = _read_stdin_json()
    if data is None:
        return
    file_path = (data.get("tool_input") or {}).get("file_path") or ""
    # v5.25.0: daemon-first -- /v1/reindex has server-side single-flight
    # (409 while one runs). Spawn a subprocess only when the daemon is
    # down; the spawn itself is debounced.
    if _is_memory_shaped(file_path) and not _nudge_daemon_reindex():
        _spawn_background_reindex()
    # v6.1.0: evidence capture is independent of (and after) the reindex nudge.
    import contextlib

    with contextlib.suppress(Exception):
        _governance_capture(data)


def _governance_mode() -> str:
    """Resolve the enforcement mode: env MNEMO_GOVERNANCE_MODE > config >
    'warn'. 'warn' is the safe default (surface, never block)."""
    import os as _os

    env = (_os.environ.get("MNEMO_GOVERNANCE_MODE") or "").strip().lower()
    if env in ("off", "warn", "block"):
        return env
    try:
        from mnemo.config import load as _load_config

        m = (_load_config().governance_enforce_mode or "warn").strip().lower()
        return m if m in ("off", "warn", "block") else "warn"
    except Exception:
        return "warn"


def _gov_tool_context(data: dict) -> tuple[str, str, list[str] | None]:
    tool_name = str(data.get("tool_name") or "")
    ti = data.get("tool_input") or {}
    fp = str(ti.get("file_path") or "")
    cmd = str(ti.get("command") or "")
    tool_arg = cmd if tool_name == "Bash" else fp
    return tool_name, tool_arg, ([fp] if fp else None)


def _gov_decision(data: dict, *, stop: bool):
    """Open the store, resolve scope, and return a governance GateDecision for
    a PreToolUse (stop=False) or Stop (stop=True) event. Fail-open -> None."""
    import os as _os

    session_id = str(data.get("session_id") or "")
    if stop and not session_id:
        return None
    cwd = str(data.get("cwd") or _os.getcwd())
    try:
        store = _open_store()
    except Exception:
        return None
    try:
        from mnemo import governance, retrieve

        scope_keys, _idx = retrieve.resolve_auto_scope(store, cwd)
        scope = set(scope_keys) if scope_keys else None
        if stop:
            return governance.evaluate_stop(store, session_id=session_id, scope=scope)
        tool_name, tool_arg, file_paths = _gov_tool_context(data)
        if not tool_name:
            return None
        return governance.evaluate_gate(
            store,
            session_id=session_id,
            tool_name=tool_name,
            tool_arg=tool_arg,
            file_paths=file_paths,
            scope=scope,
        )
    except Exception:
        return None
    finally:
        store.close()


@hook_app.command("pre-tool-use")
def hook_pre_tool_use() -> None:
    """PreToolUse: gate a tool call against governance ``block`` rules. Default
    mode 'warn' (surface the reason, allow); 'block' denies/asks. Fail-open --
    any error or daemon-down lets the tool through (a governance layer must
    never brick a session)."""
    import json as _json
    import os as _os

    data = _read_stdin_json()
    if data is None:
        return
    decision = _gov_decision(data, stop=False)
    if decision is None or not decision.blocked:
        return
    mode = "warn" if _os.environ.get("MNEMO_GOVERNANCE_BYPASS") == "1" else _governance_mode()
    if mode == "off":
        return
    if mode == "warn":
        typer.echo(
            _json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": "[mnemo governance] WARNING (not blocking):\n"
                        + decision.reason,
                    }
                }
            )
        )
        return
    typer.echo(
        _json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision.permission,
                    "permissionDecisionReason": decision.reason,
                }
            }
        )
    )


@hook_app.command("stop")
def hook_stop() -> None:
    """Stop: block session end while a file edited this session is still
    covered by an unsatisfied mandatory gate. Default 'warn' (allow); 'block'
    blocks. Fail-open."""
    import json as _json
    import os as _os

    data = _read_stdin_json()
    if data is None:
        return
    decision = _gov_decision(data, stop=True)
    if decision is None or not decision.blocked:
        return
    mode = "warn" if _os.environ.get("MNEMO_GOVERNANCE_BYPASS") == "1" else _governance_mode()
    if mode in ("off", "warn"):
        return  # Stop has no context channel; warn = allow
    typer.echo(_json.dumps({"decision": "block", "reason": decision.reason}))


# --- mnemo statusline -----------------------------------------------------
#
# v5.25.0 (workstream B): a one-line presence cue for the Claude Code status
# bar, wired into the user's settings.json by the installer. Reads CC's
# status JSON from stdin and prints `mnemo <count>` / `mnemo offline`. Never
# opens the store; short daemon probe that returns as soon as the daemon
# answers, and the bar reruns per-message, so it never blocks input.


@app.command()
def statusline() -> None:
    """Print a one-line mnemo status for the Claude Code status bar."""
    import sys

    from mnemo import statusline as statusline_mod

    typer.echo(statusline_mod.render(sys.stdin.read()))


@app.command("statusline-setup")
def statusline_setup(
    settings: str = typer.Option(
        "", "--settings", help="settings.json to wire (default: ~/.claude/settings.json)."
    ),
) -> None:
    """Wire mnemo's statusline into Claude Code settings.json.

    Non-clobbering + idempotent: adds a ``statusLine`` entry only when none
    exists; NEVER overwrites a user's existing status line. Invoked by the
    installers; safe to run by hand."""
    from pathlib import Path

    from mnemo import paths
    from mnemo import statusline as statusline_mod

    path = Path(settings) if settings else (paths.claude_home() / "settings.json")
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(current, dict):
            current = {}
    except Exception:
        current = {}
    new, action = statusline_mod.ensure_statusline(current)
    if action == "added":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
        typer.echo(f"[ok] added mnemo statusline to {path}")
    elif action == "exists_mnemo":
        typer.echo("[ok] mnemo statusline already configured")
    else:  # exists_other
        typer.echo(
            f"[warn] a different statusLine is set in {path}; leaving it. To use "
            f'mnemo, set statusLine.command to "{statusline_mod.STATUSLINE_COMMAND}".'
        )


# --- mnemo eval (v5.26.0): the retrieval precision instrument --------------


@app.command("eval")
def eval_cmd(
    set_path: str = typer.Option(
        "", "--set", help="Path to a labelled eval-set JSON (defaults to the shipped SELF set)."
    ),
    k: int = typer.Option(5, "--k", help="Rank cutoff for hit@k."),
) -> None:
    """Run the retrieval precision eval (hit@k / MRR) against the live store.

    A report instrument, not a gate: prints per-query hits/misses + the
    aggregate baseline that v5.27.0's exactness work must beat. Queries go
    daemon-first (warm model) with the in-process fallback, auto-scoped to
    the current directory exactly like the production hook path."""
    import os
    from pathlib import Path

    from mnemo import eval_retrieval as ev

    fixture = (
        Path(set_path)
        if set_path
        else Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "retrieval_eval.json"
    )
    entries = ev.load_eval_set(fixture)
    cwd = os.getcwd()

    def _query(e: ev.EvalEntry) -> list[str]:
        payload = _daemon_query(e.prompt, k=k, cwd=(e.project_key or cwd))
        if payload is not None:
            return [str(h.get("source_path") or "") for h in payload.get("hits", [])]
        # Daemon down: in-process fallback (slow path; loads the embedder).
        from mnemo import retrieve

        store = _open_store()
        try:
            if e.project_key is not None:
                kwargs: dict = {"active_project": e.project_key}
            else:
                keys, _idx = retrieve.resolve_auto_scope(store, cwd)
                kwargs = {"active_projects": keys or None}
            res = retrieve.query(store, Embedder(), e.prompt, k=k, **kwargs)
            return [h.source_path or "" for h in res.hits]
        finally:
            store.close()

    rows = ev.run_entries(entries, query_fn=_query, k=k)
    agg = ev.aggregate(rows)
    # v5.28.0: pin a corpus snapshot in the header so two reports are
    # comparable (the set is noisy when the corpus drifts between runs).
    snap_store = _open_store()
    try:
        corpus = ev.corpus_snapshot(snap_store)
    finally:
        snap_store.close()
    typer.echo(ev.format_report(rows, agg, corpus=corpus))


# --- mnemo eval-tasks (v6.0.0): the moat task-success instrument ----------


@app.command("eval-tasks")
def eval_tasks_cmd(
    set_path: str = typer.Option(
        "", "--set", help="Path to a task-set JSON (defaults to the shipped SELF set)."
    ),
) -> None:
    """Run the agentic task-success eval -- the moat-reliability instrument.

    Reliability here is NOT hit@k (snippet retrieval). It is: can mnemo's
    tools answer a structural / provenance / memory-recall question in <= the
    task's call budget? Graph classes walk the in-process store via the
    deterministic ORACLE path (one get_edges / traverse); memory_recall goes
    daemon-first (warm model) with an in-process fallback. A report
    instrument, not a gate -- the report LEADS with per-class moat success;
    hit@k is deliberately absent."""
    import os
    from pathlib import Path

    from mnemo import eval_retrieval as ev
    from mnemo import eval_tasks as et

    fixture = (
        Path(set_path)
        if set_path
        else Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tasks_eval.json"
    )
    tasks = et.load_task_set(fixture)
    cwd = os.getcwd()
    store = _open_store()
    try:

        def _solve(task: et.Task) -> tuple[list[str], int]:
            if task.cls == "memory_recall":
                # Daemon-first (warm model), auto-scoped exactly like the hook.
                payload = _daemon_query(task.prompt, k=5, cwd=cwd)
                if payload is not None:
                    return ([str(h.get("source_path") or "") for h in payload.get("hits", [])], 1)
                return et.oracle_solve(store, task, k=5)  # in-process fallback (loads embedder)
            # structural / provenance: pure graph walk, no model needed.
            return et.oracle_solve(store, task)

        results = et.run_tasks(tasks, solve_fn=_solve)
        agg = et.aggregate_tasks(results)
        corpus = ev.corpus_snapshot(store)
    finally:
        store.close()
    typer.echo(et.format_task_report(results, agg, corpus=corpus))


# --- mnemo key {create,list,revoke} (Phase 3 / Task 2.2) ------------------
#
# Issuance + lifecycle of hosted-tier API keys. The hosted tier itself
# (auth on /v1/query, metering, quota enforcement) ships in Tasks 2.3 /
# 2.4 / 2.5. Self-host installs that never enable hosted mode can
# ignore this surface entirely -- the schema migration is harmless.


@key_app.command("create")
def key_create(name: str) -> None:
    """Mint a new API key. The raw key is printed ONCE; copy it now."""
    store = _open_store()
    try:
        raw_key, key_id = store.create_api_key(name)
        typer.echo("RAW KEY:")
        typer.echo(f"  {raw_key}")
        typer.echo(
            "\n*** IMPORTANT *** copy the raw key NOW. mnemo stores only "
            "the salted hash; it will NOT be shown again."
        )
        typer.echo(f"\nid:   {key_id}")
        typer.echo(f"name: {name}")
    finally:
        store.close()


@key_app.command("list")
def key_list(
    include_revoked: bool = typer.Option(False, "--include-revoked", help="Show revoked keys too."),
) -> None:
    """List API keys. By default excludes revoked keys."""
    store = _open_store()
    try:
        keys = store.list_api_keys(include_revoked=include_revoked)
        if not keys:
            typer.echo("(no API keys)")
            return
        for k in keys:
            status = "REVOKED" if k["revoked_at"] else "active"
            typer.echo(f"{k['id']}  {k['name']}  {status}")
    finally:
        store.close()


@key_app.command("revoke")
def key_revoke(key_id: str) -> None:
    """Revoke an active API key. Idempotent for the not-found /
    already-revoked case (exits 1 with a hint)."""
    store = _open_store()
    try:
        ok = store.revoke_api_key(key_id)
        if ok:
            typer.echo(f"Revoked: {key_id}")
        else:
            typer.echo(
                f"No active key with id {key_id!r} (not found or already revoked).",
                err=True,
            )
            raise typer.Exit(code=1)
    finally:
        store.close()


@key_app.command("set-quota")
def key_set_quota(
    key_id: str,
    max_queries: int = typer.Option(
        ...,
        "--max-queries",
        help="Maximum queries per period (>=0).",
    ),
    max_tokens: int = typer.Option(
        ...,
        "--max-tokens",
        help="Maximum tokens per period (>=0).",
    ),
    period: str = typer.Option(
        "monthly",
        "--period",
        help="Quota granularity (v0.1: 'monthly' only).",
    ),
) -> None:
    """Set or update the quota for an API key.

    Wraps the SQLite step that docs/hosted/deploying.md used to
    require for Phase 3a. Idempotent: re-running with new limits
    updates them in place. The Phase 3b enforcement on /v1/query
    reads this row to decide whether to 429.
    """
    import sqlite3

    store = _open_store()
    try:
        try:
            store.set_quota(
                key_id,
                max_queries=max_queries,
                max_tokens=max_tokens,
                period=period,
            )
        except sqlite3.IntegrityError as exc:
            typer.echo(
                f"No key with id {key_id!r} (cascade FK refused).",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        except ValueError as exc:
            typer.echo(f"Invalid quota: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        typer.echo(
            f"Quota set for {key_id}: period={period}, "
            f"max_queries={max_queries}, max_tokens={max_tokens}"
        )
    finally:
        store.close()


# --- mnemo billing report (Phase 3 / Task 2.6) ----------------------------
#
# CSV-out per-key billing report. Columns are stable + documented in
# docs/hosted/deploying.md so downstream systems can rely on them.


@billing_app.command("report")
def billing_report_cmd(
    period: str = typer.Option(
        ...,
        "--period",
        help="Billing period in YYYY-MM (e.g. 2026-05). Monthly granularity.",
    ),
) -> None:
    """Emit a CSV billing report for the given period.

    Columns: ``key_name,queries,tokens,quota_queries,quota_tokens,over_quota``.
    Pipes directly into the billing spreadsheet / CSV-aware tool.
    Keys with zero usage in the period are included (zero rows).
    Keys without a quota set show ``0`` for the quota fields +
    ``over_quota=false``.
    """
    import csv
    import sys

    store = _open_store()
    try:
        rows = store.billing_report(period)
    finally:
        store.close()

    writer = csv.writer(sys.stdout)
    writer.writerow(
        ["key_name", "queries", "tokens", "quota_queries", "quota_tokens", "over_quota"]
    )
    for r in rows:
        writer.writerow(
            [
                r["key_name"],
                r["queries"],
                r["tokens"],
                r["quota_queries"],
                r["quota_tokens"],
                "true" if r["over_quota"] else "false",
            ]
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
