"""Integration test for the real MiniLM embedder.

Runs the actual sentence-transformers model. First-time setup downloads ~22 MB
to ``~/.claude/mnemo/cache/``. Subsequent runs hit the cache in ~2s.

To skip these in fast iterations: ``uv run pytest --ignore=tests/integration``.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

from mnemo.embed import DEFAULT_DIM, Embedder, embed_node
from mnemo.store import Node, Store


@pytest.fixture(scope="module")
def embedder(tmp_path_factory: pytest.TempPathFactory) -> Embedder:
    # Prefer the shared model cache when configured (CI caches it per
    # runner; a warm cache loads offline via the local_files_only-first
    # attempt). A fresh tmp dir would re-download from the Hub every run,
    # which stalls under throttling.
    env = os.environ.get("MNEMO_MODEL_CACHE_DIR")
    cache = Path(env) if env else tmp_path_factory.mktemp("model-cache")
    return Embedder(cache_dir=cache)


def test_dim_is_384(embedder: Embedder) -> None:
    assert embedder.dim == DEFAULT_DIM


def test_embed_text_returns_float_vector(embedder: Embedder) -> None:
    v = embedder.embed_text("Hard rule: no emojis in code or commit messages.")
    assert isinstance(v, list)
    assert len(v) == DEFAULT_DIM
    assert all(isinstance(x, float) for x in v)


def test_embedding_is_normalized(embedder: Embedder) -> None:
    v = embedder.embed_text("any short text")
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-3


def test_embedding_is_deterministic(embedder: Embedder) -> None:
    v1 = embedder.embed_text("the same exact string")
    v2 = embedder.embed_text("the same exact string")
    assert v1 == v2


def test_similar_texts_have_smaller_distance(embedder: Embedder) -> None:
    v_a = embedder.embed_text("Use IoU dedup with threshold zero for time-based cooldown.")
    v_b = embedder.embed_text("Time-based cooldown via degenerated IoU dedup.")
    v_c = embedder.embed_text("Frontend Vite build cache invalidation rules.")
    # Cosine similarity ~ 1 - 0.5 * L2² for unit vectors.
    cos_ab = sum(a * b for a, b in zip(v_a, v_b, strict=True))
    cos_ac = sum(a * c for a, c in zip(v_a, v_c, strict=True))
    assert cos_ab > cos_ac


def test_embed_node_writes_chunks(embedder: Embedder, store: Store, tmp_path: Path) -> None:
    n = Node.new(
        type="memory_feedback",
        name="t",
        body="## A\nfirst section\n\n## B\nsecond section",
        source_path=str(tmp_path / "t.md"),
        source_kind="memory_dir",
    )
    store.upsert_node(n)
    count = embed_node(store, n, embedder)
    assert count >= 2
    assert n.id in store.list_embedded_node_ids()
