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
- ``mnemo daemon {start,stop,status}``
"""

from __future__ import annotations

import json
import logging

import typer

from mnemo import __version__, daemon, ingest, paths, retrieve
from mnemo.embed import Embedder
from mnemo.store import Store

log = logging.getLogger(__name__)

app = typer.Typer(
    name="mnemo",
    help="mnemo - local-first knowledge memory for Claude Code",
    no_args_is_help=True,
)
source_app = typer.Typer(help="Manage ingestion sources", no_args_is_help=True)
node_app = typer.Typer(help="Inspect and edit nodes", no_args_is_help=True)
daemon_app = typer.Typer(help="Daemon process lifecycle", no_args_is_help=True)
app.add_typer(source_app, name="source")
app.add_typer(node_app, name="node")
app.add_typer(daemon_app, name="daemon")


def _open_store() -> Store:
    paths.ensure_runtime_dirs()
    return Store(paths.db_path())


# --- Top-level commands ----------------------------------------------------


@app.command()
def init() -> None:
    """Create runtime dirs and register the default Scope B sources."""
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
                    "errors": [list(e) for e in report.errors],
                },
                indent=2,
            )
        )
    finally:
        store.close()


@app.command()
def query(
    prompt: str = typer.Argument(..., help="Query text"),
    k: int = typer.Option(5, "--k", help="Number of hits to return"),
    budget: int = typer.Option(800, "--budget", help="Token budget"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of human text"),
    project: str | None = typer.Option(None, "--project", help="Active project key"),
) -> None:
    """One-shot retrieval. Hooks call this to fetch context."""
    store = _open_store()
    try:
        embedder = Embedder()
        result = retrieve.query(
            store, embedder, prompt, k=k, budget_tokens=budget, active_project=project
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


# --- source subcommands ----------------------------------------------------


@source_app.command("add")
def source_add(
    path: str,
    kind: str = typer.Option(..., "--kind", help="memory_dir | claude_md | plan_dir"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    store = _open_store()
    try:
        store.register_source(path, kind, project_key=project_key)
        typer.echo(f"registered: {path}")
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
def daemon_stop() -> None:
    if daemon.stop():
        typer.echo("daemon stopped")
    else:
        typer.echo("daemon not running")


@daemon_app.command("status")
def daemon_status() -> None:
    s = daemon.status()
    if s.running:
        typer.echo(f"running (pid {s.pid})")
    elif s.stale:
        typer.echo(f"stale pid file (pid {s.pid} not alive)")
    else:
        typer.echo("not running")


def _run_foreground(*, host: str, port: int) -> None:
    """Foreground entry: write PID, run uvicorn, clean up PID on exit."""
    import uvicorn

    from mnemo.server import create_app

    daemon.write_pid_file()
    try:
        uvicorn.run(create_app(), host=host, port=port, log_level="info")
    finally:
        daemon.remove_pid_file()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
