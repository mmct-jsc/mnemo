"""v5.25.0 step 7: the Embedder must try the LOCAL model cache first.

Live diagnosis: each fresh-process model load contacted the HuggingFace
Hub (unauthenticated, rate-limited) and took ~50s inside the per-prompt
hook. With ``local_files_only=True`` a cached model loads with zero
network round-trips; the network path remains as the first-run fallback.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


def _fake_st_module(calls: list[dict], *, fail_local: bool) -> types.SimpleNamespace:
    class FakeST:
        def __init__(self, name: str, **kwargs: object) -> None:
            calls.append(dict(kwargs))
            if fail_local and kwargs.get("local_files_only"):
                raise OSError("model not in local cache")

    return types.SimpleNamespace(SentenceTransformer=FakeST)


def test_embedder_loads_local_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict] = []
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", _fake_st_module(calls, fail_local=False)
    )
    from mnemo.embed import Embedder

    Embedder(cache_dir=tmp_path)._load()
    assert calls, "model constructor never called"
    assert calls[0].get("local_files_only") is True, "first attempt must be cache-only"
    assert len(calls) == 1, "cached load must not retry over the network"


def test_embedder_default_cache_honors_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """MNEMO_MODEL_CACHE_DIR overrides the DEFAULT cache dir (explicit arg
    still wins). This is what lets CI cache the model once per runner and
    reuse it across jobs instead of re-downloading from HuggingFace -- the
    download path stalls under Hub throttling (a CI integration job hung
    for 1h38m on exactly this)."""
    from mnemo.embed import Embedder

    monkeypatch.setenv("MNEMO_MODEL_CACHE_DIR", str(tmp_path / "modelcache"))
    assert Embedder()._cache_dir == tmp_path / "modelcache"
    assert Embedder(cache_dir=tmp_path / "explicit")._cache_dir == tmp_path / "explicit"


def test_embedder_falls_back_to_network_when_uncached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict] = []
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", _fake_st_module(calls, fail_local=True)
    )
    from mnemo.embed import Embedder

    Embedder(cache_dir=tmp_path)._load()
    assert len(calls) == 2, "uncached load must fall back to the network path"
    assert not calls[1].get("local_files_only"), "fallback must allow the download"
