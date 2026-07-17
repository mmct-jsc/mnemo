"""Reindex must leave NO node unembedded (silent-zero fix).

THE BUG (found by dogfooding v6.2.0): 4,106/18,661 live nodes (22%) had zero
chunk_meta rows -- invisible to vector search while BM25/FTS5 indexed them
fine, so nothing ever looked wrong. Casualties included 100% of `commit` and
`code_endpoint` nodes and the two most recent session handovers, meaning the
canonical entry-point memory could not be recalled semantically.

TWO INDEPENDENT CAUSES, one shared invariant:

1. The file-walk path. The PostToolUse hook nudges
   `POST /v1/reindex?embed=false` for speed, so `ingest.reindex_events` runs
   with `embedder=None` and upserts the node WITHOUT embedding it. A later
   reindex that DOES have an embedder sees `existing.hash == parsed.hash` and
   takes the `unchanged` branch -- so the embed is never retried. Permanent.
2. The git-log path. `_ingest_git_log_for_source` upserts commit nodes and
   never embeds them at all, embedder or not.

Patching each creation site is whack-a-mole (there are at least three, and the
next one added would silently regress). The invariant these tests pin is
global and creation-path agnostic:

    after a reindex WITH an embedder, no node in the store lacks embeddings.

`embed.embed_all_unembedded` already implemented exactly this repair; it was
just never wired into the daemon or the CLI (only `scripts/smoke_ingest.py`
called it).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mnemo import ingest
from mnemo.store import Node, Store


def _write_memory(d: Path, name: str) -> Path:
    p = d / f"{name}.md"
    p.write_text(
        f"---\nname: {name}\ntype: project\ndescription: {name} description\n---\n\n"
        f"Body of {name} with enough words to chunk into something embeddable.\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_HOME", str(tmp_path / "home"))
    src_dir = tmp_path / "mem"
    src_dir.mkdir()
    _write_memory(src_dir, "alpha_note")
    store = Store(tmp_path / "t.db")
    store.register_source(str(src_dir), "memory_dir")
    yield store, src_dir
    store.close()


def _unembedded_ids(store: Store) -> set[str]:
    embedded = store.list_embedded_node_ids()
    return {n.id for n in store.list_nodes(limit=10**6)} - embedded


# --- the regression: cheap hook ingest, then a real reindex ---------------


def test_node_ingested_without_embedder_is_embedded_on_next_reindex(seeded) -> None:
    """THE bug. The hook's embed=false pass creates the node unembedded; the
    next reindex WITH an embedder must repair it even though the file's hash
    has not changed."""
    from tests.conftest import FakeEmbedder

    store, _ = seeded
    ingest.reindex(store, embedder=None)  # the cheap hook path
    assert _unembedded_ids(store), "precondition: embed=false leaves it unembedded"

    ingest.reindex(store, embedder=FakeEmbedder())  # a real reindex, file UNCHANGED
    assert not _unembedded_ids(store), (
        "a hash-unchanged node with no embeddings must still get embedded -- "
        "the skip condition is 'unchanged AND already embedded', not 'unchanged'"
    )


def test_reindex_with_embedder_leaves_nothing_unembedded_whatever_created_it(
    seeded,
) -> None:
    """Creation-path agnostic: a node inserted directly (as the git-log commit
    path does -- upsert with no embed) must also be repaired."""
    from tests.conftest import FakeEmbedder

    store, _ = seeded
    store.upsert_node(
        Node.new(
            type="commit",
            name="abc123",
            description="a commit that no ingest path ever embedded",
            body="commit body text that should be searchable by meaning",
            source_path="/repo@abc123",
            source_kind="code_repo",  # the kind git_log.commit_to_node actually uses
        )
    )
    assert _unembedded_ids(store), "precondition: the commit node has no chunks"

    ingest.reindex(store, embedder=FakeEmbedder())
    assert not _unembedded_ids(store), "reindex must not care which path created a node"


# --- the cheap path must stay cheap ---------------------------------------


def test_reindex_without_embedder_does_not_embed(seeded) -> None:
    """embed=false is the hook's speed contract: it must NOT start embedding."""
    store, _ = seeded
    ingest.reindex(store, embedder=None)
    assert _unembedded_ids(store), "embed=false must remain a no-embed fast path"


# --- visibility: the repair must be reported, never silent ----------------


def test_report_exposes_the_backfilled_count(seeded) -> None:
    """A silent repair is how this hid for so long. The count must surface."""
    from tests.conftest import FakeEmbedder

    store, _ = seeded
    ingest.reindex(store, embedder=None)
    report = ingest.reindex(store, embedder=FakeEmbedder())
    assert getattr(report, "embedded_backfilled", 0) >= 1


def test_backfill_is_zero_when_corpus_is_already_whole(seeded) -> None:
    from tests.conftest import FakeEmbedder

    store, _ = seeded
    ingest.reindex(store, embedder=FakeEmbedder())
    report = ingest.reindex(store, embedder=FakeEmbedder())
    assert report.embedded_backfilled == 0, "a healthy corpus must not re-embed"


def test_done_event_carries_the_backfilled_count(seeded) -> None:
    from tests.conftest import FakeEmbedder

    store, _ = seeded
    ingest.reindex(store, embedder=None)
    done = None
    for name, payload in ingest.reindex_events(store, embedder=FakeEmbedder()):
        if name == "done":
            done = payload
    assert done is not None
    assert done.get("embedded_backfilled", 0) >= 1
