"""Integration tests for /v1/sources/* endpoints (phase 3).

Covers:
- POST creates a source with optional include / exclude patterns
- GET lists sources with their patterns
- PATCH updates a subset of fields; omitted fields untouched; null clears
- DELETE removes
- ingest.scan_source honors include AND exclude patterns
- Default include set (markdown only) when patterns are unset
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.embed import Embedder
from mnemo.ingest import scan_source
from mnemo.server import create_app
from mnemo.store import Source, Store


class _FakeEmbedder(Embedder):
    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        self.model_name = "fake"
        self._cache_dir = Path("/tmp/mnemo-fake-cache")
        self._model = object()

    @property
    def dim(self) -> int:
        return 384

    def embed_text(self, text: str) -> list[float]:
        return [float(len(text) % 7) for _ in range(384)]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    db = tmp_path / "test.db"
    store = Store(db)
    embedder = _FakeEmbedder()
    app = create_app(store=store, embedder=embedder)
    with TestClient(app) as c:
        yield c


# --- HTTP layer -----------------------------------------------------------


def test_create_source_with_patterns(client: TestClient, tmp_path: Path) -> None:
    src_dir = tmp_path / "memory"
    src_dir.mkdir()
    r = client.post(
        "/v1/sources",
        json={
            "path": str(src_dir),
            "kind": "memory_dir",
            "include": "*.md, *.txt",
            "exclude": "**/draft-*.md",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == str(src_dir)
    assert body["include"] == "*.md, *.txt"
    assert body["exclude"] == "**/draft-*.md"


def test_patch_source_partial_update(client: TestClient, tmp_path: Path) -> None:
    src_dir = tmp_path / "memory"
    src_dir.mkdir()
    client.post(
        "/v1/sources",
        json={
            "path": str(src_dir),
            "kind": "memory_dir",
            "project_key": "test-key",
            "include": "*.md",
        },
    )
    # PATCH only the exclude. Other fields should remain.
    r = client.patch(
        "/v1/sources",
        json={"path": str(src_dir), "exclude": "**/tmp/**"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["project_key"] == "test-key"  # untouched
    assert body["include"] == "*.md"  # untouched
    assert body["exclude"] == "**/tmp/**"  # patched


def test_patch_source_clear_field_with_null(client: TestClient, tmp_path: Path) -> None:
    src_dir = tmp_path / "memory"
    src_dir.mkdir()
    client.post(
        "/v1/sources",
        json={
            "path": str(src_dir),
            "kind": "memory_dir",
            "include": "*.md",
        },
    )
    # Explicitly clear include via null.
    r = client.patch(
        "/v1/sources",
        json={"path": str(src_dir), "include": None},
    )
    assert r.status_code == 200
    assert r.json()["include"] is None


def test_patch_unknown_source_404(client: TestClient) -> None:
    r = client.patch(
        "/v1/sources",
        json={"path": "/does/not/exist", "enabled": False},
    )
    assert r.status_code == 404


def test_delete_source(client: TestClient, tmp_path: Path) -> None:
    src_dir = tmp_path / "memory"
    src_dir.mkdir()
    client.post("/v1/sources", json={"path": str(src_dir), "kind": "memory_dir"})
    r = client.delete(f"/v1/sources?path={src_dir}")
    assert r.status_code == 200
    # List should now be empty.
    r2 = client.get("/v1/sources")
    assert all(s["path"] != str(src_dir) for s in r2.json())


# --- ingest layer (filter behavior) --------------------------------------


def _make_tree(root: Path, files: list[str]) -> None:
    for rel in files:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "---\nname: t\ndescription: t\ntype: project\n---\nbody " + rel,
            encoding="utf-8",
        )


def test_default_include_matches_md_only(tmp_path: Path) -> None:
    """A source with NO include set falls back to '**/*.md' (phase 3 default).
    .txt files should NOT be picked up yet -- phase 4 widens this."""
    root = tmp_path / "store"
    _make_tree(root, ["a.md", "sub/b.md", "c.txt", "ignored/d.md"])
    src = Source(
        path=str(root),
        kind="memory_dir",
        project_key=None,
        last_indexed_at=None,
        enabled=True,
        include=None,
        exclude=None,
    )
    paths = sorted(p.path.relative_to(root).as_posix() for p in scan_source(src))
    assert paths == ["a.md", "ignored/d.md", "sub/b.md"]
    assert "c.txt" not in paths  # default include is markdown-only


def test_explicit_include_widens_to_txt(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _make_tree(root, ["a.md", "c.txt"])
    src = Source(
        path=str(root),
        kind="memory_dir",
        project_key=None,
        last_indexed_at=None,
        enabled=True,
        include="*.md, *.txt",
        exclude=None,
    )
    paths = sorted(p.path.relative_to(root).as_posix() for p in scan_source(src))
    # Note: phase 3's parse_file still only handles .md, but the filter
    # WOULD let .txt through. Phase 4 ships the .txt parser.
    # For now we assert markdown is included; .txt being parsed cleanly
    # is verified in phase 4's tests.
    assert "a.md" in paths


def test_exclude_pattern_filters(tmp_path: Path) -> None:
    root = tmp_path / "store"
    _make_tree(root, ["keep.md", "drafts/skip-1.md", "drafts/skip-2.md", "good/yes.md"])
    src = Source(
        path=str(root),
        kind="memory_dir",
        project_key=None,
        last_indexed_at=None,
        enabled=True,
        include=None,  # default: **/*.md
        exclude="drafts/**",
    )
    paths = sorted(p.path.relative_to(root).as_posix() for p in scan_source(src))
    assert paths == ["good/yes.md", "keep.md"]


def test_memory_md_index_files_always_skipped(tmp_path: Path) -> None:
    """MEMORY.md index files are skipped regardless of include patterns
    -- they're derivable and would duplicate body content."""
    root = tmp_path / "store"
    _make_tree(root, ["MEMORY.md", "real.md"])
    src = Source(
        path=str(root),
        kind="memory_dir",
        project_key=None,
        last_indexed_at=None,
        enabled=True,
        include="*.md",
        exclude=None,
    )
    paths = sorted(p.path.relative_to(root).as_posix() for p in scan_source(src))
    assert paths == ["real.md"]
