"""v5.28.0 step 4: GET /v1/nodes/<id>/full_source for line-stable keys.

Code nodes now carry a ``<file>::<qualified_name>`` key with the line
range in frontmatter. full_source must read the range from frontmatter
(not the key) and still re-read the right slice from disk. This endpoint
had NO test before v5.28.0 -- the key change is what exposed the gap, so
the regression test lands with the fix.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo import server
from mnemo.store import Node, Store


class _FakeEmbedder:
    dim = 384

    def embed_text(self, text: str) -> list[float]:
        return [0.0] * 384

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 384 for _ in texts]


@pytest.fixture
def app_store(tmp_path: Path):
    store = Store(tmp_path / "mnemo.db")
    app = server.create_app(store=store, embedder=_FakeEmbedder())
    yield TestClient(app), store
    store.close()


def _seed(store: Store, *, source_path: str, frontmatter_json: str | None) -> str:
    now = int(time.time())
    store.upsert_node(
        Node(
            id="n1",
            type="code_function",
            name="login",
            description=None,
            body="def login(): ...",
            source_path=source_path,
            source_kind="code_repo",
            project_key=None,
            frontmatter_json=frontmatter_json,
            hash="h1",
            created_at=now,
            updated_at=now,
        )
    )
    return "n1"


def test_full_source_reads_range_from_frontmatter_for_stable_key(
    app_store: tuple[TestClient, Store], tmp_path: Path
) -> None:
    client, store = app_store
    src_file = tmp_path / "auth.py"
    src_file.write_text(
        "def login():\n    return True\n\ndef other():\n    return 1\n", encoding="utf-8"
    )
    nid = _seed(
        store,
        source_path=f"{src_file.as_posix()}::login",
        frontmatter_json=json.dumps({"code_unit": {"line_start": 1, "line_end": 2}}),
    )

    resp = client.get(f"/v1/nodes/{nid}/full_source")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["lines"] == [1, 2]
    assert "def login():" in data["body"]
    # The range slice must stop before the second function.
    assert "def other()" not in data["body"]
    assert data["source_path"].endswith("/auth.py")


def test_full_source_still_handles_legacy_keyed_node(
    app_store: tuple[TestClient, Store], tmp_path: Path
) -> None:
    """A pre-migration node (line range in the key, no frontmatter) must
    still resolve -- the helper falls back to the legacy suffix."""
    client, store = app_store
    src_file = tmp_path / "auth.py"
    src_file.write_text("def login():\n    return True\n", encoding="utf-8")
    nid = _seed(store, source_path=f"{src_file.as_posix()}:1-2", frontmatter_json=None)

    resp = client.get(f"/v1/nodes/{nid}/full_source")
    assert resp.status_code == 200, resp.text
    assert resp.json()["lines"] == [1, 2]
