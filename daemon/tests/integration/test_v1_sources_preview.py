"""v2.0 phase 2 integration: POST /v1/sources/preview.

The preview endpoint is the auto-router's HTTP surface: clients
(CLI, UI) call it with a path before deciding whether to commit a
``POST /v1/sources`` write. No DB write happens here -- preview is
side-effect-free.

Response shape mirrors :class:`mnemo.auto_router.PreviewResult` so
the UI can render the proposed-kind suggestion plus the file
breakdown directly without remapping fields.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.embed import Embedder
from mnemo.server import create_app
from mnemo.store import Store


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


def _write(path: Path, body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_preview_classifies_code_repo(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    _write(tmp_path / "main.py", "x = 1\n")
    _write(tmp_path / "lib.py", "y = 2\n")
    r = client.post("/v1/sources/preview", json={"path": str(tmp_path)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proposed_kind"] == "code_repo"
    assert body["confidence"] == "high"
    assert body["exceeds_safety_ceiling"] is False
    assert body["breakdown"]["has_git"] is True
    assert body["breakdown"]["by_ext"][".py"] == 2


def test_preview_classifies_memory_dir(client: TestClient, tmp_path: Path) -> None:
    _write(
        tmp_path / "feedback_x.md",
        "---\nname: feedback_x\ntype: feedback\n---\nbody\n",
    )
    r = client.post("/v1/sources/preview", json={"path": str(tmp_path)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proposed_kind"] == "memory_dir"
    assert body["confidence"] == "high"
    assert body["breakdown"]["md_with_frontmatter"] == 1


def test_preview_classifies_docs_dir(client: TestClient, tmp_path: Path) -> None:
    _write(tmp_path / "intro.md", "# Intro\n")
    _write(tmp_path / "guide.md", "# Guide\n")
    r = client.post("/v1/sources/preview", json={"path": str(tmp_path)})
    body = r.json()
    assert body["proposed_kind"] == "docs_dir"
    assert body["confidence"] == "medium"


def test_preview_returns_low_for_empty_directory(client: TestClient, tmp_path: Path) -> None:
    r = client.post("/v1/sources/preview", json={"path": str(tmp_path)})
    assert r.status_code == 200
    body = r.json()
    assert body["proposed_kind"] is None
    assert body["confidence"] == "low"


def test_preview_404_on_nonexistent_path(client: TestClient, tmp_path: Path) -> None:
    bogus = tmp_path / "definitely-not-here"
    r = client.post("/v1/sources/preview", json={"path": str(bogus)})
    assert r.status_code == 404
    body = r.json()
    assert "path" in body.get("detail", "").lower() or "exist" in body.get("detail", "").lower()


def test_preview_400_on_missing_path_field(client: TestClient) -> None:
    r = client.post("/v1/sources/preview", json={})
    # FastAPI validates Pydantic models and returns 422 by default.
    assert r.status_code in (400, 422)


def test_preview_respects_force_flag(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Lower the ceiling so we can trip it cheaply.
    from mnemo import auto_router

    monkeypatch.setattr(auto_router, "SAFETY_CEILING", 3)
    (tmp_path / ".git").mkdir()
    for i in range(5):
        _write(tmp_path / f"f{i}.py", "x = 1\n")
    r1 = client.post("/v1/sources/preview", json={"path": str(tmp_path)})
    assert r1.json()["exceeds_safety_ceiling"] is True
    r2 = client.post("/v1/sources/preview", json={"path": str(tmp_path), "force": True})
    assert r2.json()["exceeds_safety_ceiling"] is False


def test_preview_does_not_create_a_source_row(client: TestClient, tmp_path: Path) -> None:
    """The preview endpoint MUST be side-effect-free.

    Regression guard: if a future change accidentally calls
    ``register_source`` inside the preview handler, the sources list
    would grow even though we only called ``/preview``.
    """
    _write(tmp_path / "feedback.md", "---\nname: f\ntype: feedback\n---\nbody\n")
    client.post("/v1/sources/preview", json={"path": str(tmp_path)})
    r = client.get("/v1/sources")
    assert r.json() == []
