"""Tests for v2.6 phase 3: reindex_events emits classified + report events.

The generator grows two new event types:

- ``('classified', {idx, path, category, reason, override_applied})``
  emitted once per source-walked file. ``category`` is one of
  ``'indexed' | 'auto_skipped' | 'malformed' | 'suspicious'``.
- ``('report', {auto_skipped, malformed, suspicious, indexed_count, duration_ms})``
  emitted ONCE right before the final ``done`` event.

It also consults the ``source_overrides`` table before classifying:
``always_skip`` short-circuits to auto_skipped; ``always_keep``
bypasses the suspicious heuristics and forces an indexed verdict.
"""

from __future__ import annotations

from pathlib import Path

from mnemo import ingest, workspaces
from mnemo.store import Store
from tests.conftest import FakeEmbedder

# --- Helpers ----------------------------------------------------------------


def _seed_memory_file(tmp_path: Path, name: str = "good.md", body: str = "# good") -> Path:
    """Write a single markdown file under a memory_dir-shaped tree."""
    src_dir = tmp_path / "memory"
    src_dir.mkdir(exist_ok=True)
    p = src_dir / name
    p.write_text(body)
    return src_dir


def _events_by_name(events: list[tuple[str, dict]], name: str) -> list[dict]:
    return [payload for (n, payload) in events if n == name]


# --- classified event contract ----------------------------------------------


def test_emits_classified_for_normal_file(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    src = _seed_memory_file(tmp_path)
    store.register_source(path=str(src), kind="memory_dir")

    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    classified = _events_by_name(events, "classified")
    assert len(classified) == 1
    payload = classified[0]
    assert payload["category"] == "indexed"
    assert "path" in payload
    assert payload["override_applied"] is False
    assert "idx" in payload
    assert isinstance(payload["reason"], str)


def test_emits_classified_for_oversize_file(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    src = tmp_path / "memory"
    src.mkdir()
    big = src / "huge.md"
    big.write_bytes(b"x" * (6 * 1024 * 1024))  # 6 MiB
    store.register_source(path=str(src), kind="memory_dir")

    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    classified = _events_by_name(events, "classified")
    assert len(classified) == 1
    assert classified[0]["category"] == "auto_skipped"
    assert classified[0]["reason"].startswith("oversize")
    # Should NOT have emitted a file event for the skipped file
    file_events = _events_by_name(events, "file")
    assert all(p["status"] != "indexed" for p in file_events), (
        "auto-skipped files must not appear as indexed in file events"
    )


def test_emits_classified_for_suspicious_minified(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    """A .min.js inside a memory_dir would still be walked; safeguards flag it."""
    src = tmp_path / "memory"
    src.mkdir()
    # memory_dir's default include is markdown/txt/pdf; bypass via explicit include.
    sketchy = src / "app.min.js"
    sketchy.write_text("var x=1;")
    sketchy_md = src / "good.md"
    sketchy_md.write_text("# good")
    store.register_source(path=str(src), kind="memory_dir", include="*.md,*.min.js", exclude="")

    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    classified = _events_by_name(events, "classified")
    suspicious = [c for c in classified if c["category"] == "suspicious"]
    assert len(suspicious) == 1
    assert "minified" in suspicious[0]["reason"]


# --- report event -----------------------------------------------------------


def test_emits_report_event_before_done(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    src = tmp_path / "memory"
    src.mkdir()
    (src / "ok.md").write_text("# good content")
    (src / "huge.md").write_bytes(b"x" * (6 * 1024 * 1024))
    (src / "bundle.js").write_text("var x=1;")  # filename suspicious
    store.register_source(path=str(src), kind="memory_dir", include="*.md,*.js", exclude="")

    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    names = [n for (n, _) in events]
    # 'report' must appear exactly once, immediately before 'done'.
    assert names.count("report") == 1
    report_idx = names.index("report")
    assert names[report_idx + 1] == "done"


def test_report_event_has_three_sections(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    src = tmp_path / "memory"
    src.mkdir()
    (src / "ok.md").write_text("# good content")
    (src / "huge.md").write_bytes(b"x" * (6 * 1024 * 1024))
    (src / "bundle.js").write_text("var x=1;")
    store.register_source(path=str(src), kind="memory_dir", include="*.md,*.js", exclude="")

    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    reports = _events_by_name(events, "report")
    assert len(reports) == 1
    rep = reports[0]
    for field in ("auto_skipped", "malformed", "suspicious", "indexed_count", "duration_ms"):
        assert field in rep, f"report missing {field}"

    paths_auto = {item["path"] for item in rep["auto_skipped"]}
    paths_susp = {item["path"] for item in rep["suspicious"]}
    assert any("huge.md" in p for p in paths_auto)
    assert any("bundle.js" in p for p in paths_susp)
    assert rep["indexed_count"] >= 1
    assert rep["duration_ms"] >= 0


# --- source_overrides integration -------------------------------------------


def test_always_skip_override_short_circuits_classification(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    src = tmp_path / "memory"
    src.mkdir()
    skip_me = src / "skip.md"
    skip_me.write_text("# would normally index")
    (src / "good.md").write_text("# good")
    store.register_source(path=str(src), kind="memory_dir")
    workspaces.upsert_source_override(
        store,
        source_path=str(skip_me),
        decision="always_skip",
        reason="user-skipped",
    )

    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    classified = _events_by_name(events, "classified")
    skip_event = next(c for c in classified if "skip.md" in c["path"])
    assert skip_event["category"] == "auto_skipped"
    assert skip_event["override_applied"] is True
    assert "override" in skip_event["reason"]
    # skip.md must not be indexed.
    file_events = _events_by_name(events, "file")
    skip_in_files = [p for p in file_events if "skip.md" in p["path"]]
    assert skip_in_files == []


def test_always_keep_override_bypasses_suspicious_heuristics(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    """always_keep forces a 'normally suspicious' file through to indexing."""
    src = tmp_path / "memory"
    src.mkdir()
    suspect = src / "vendor.js"
    suspect.write_text("function f(){}")
    store.register_source(path=str(src), kind="memory_dir", include="*.js", exclude="")
    workspaces.upsert_source_override(
        store,
        source_path=str(suspect),
        decision="always_keep",
        reason="user-kept",
    )

    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    classified = _events_by_name(events, "classified")
    assert any(c["category"] == "indexed" and c["override_applied"] is True for c in classified)


def test_classified_idx_monotonic(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    src = tmp_path / "memory"
    src.mkdir()
    for i in range(5):
        (src / f"f{i}.md").write_text(f"# file {i}")
    store.register_source(path=str(src), kind="memory_dir")

    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    classified = _events_by_name(events, "classified")
    idxs = [c["idx"] for c in classified]
    assert idxs == sorted(idxs)
    assert idxs[0] >= 1


# --- legacy contract preserved ----------------------------------------------


def test_existing_done_event_still_fires_last(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    src = _seed_memory_file(tmp_path)
    store.register_source(path=str(src), kind="memory_dir")

    events = list(ingest.reindex_events(store, embedder=fake_embedder))
    assert events[-1][0] == "done"
    assert events[0][0] == "start"


def test_legacy_reindex_report_unchanged(
    store: Store, fake_embedder: FakeEmbedder, tmp_path: Path
) -> None:
    src = _seed_memory_file(tmp_path)
    store.register_source(path=str(src), kind="memory_dir")

    report = ingest.reindex(store, embedder=fake_embedder)
    assert report.added == 1
    assert report.updated == 0
    assert report.errors == []
