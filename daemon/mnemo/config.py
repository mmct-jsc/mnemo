"""Runtime-editable configuration for mnemo.

Settings live in ``~/.claude/mnemo/settings.json`` and are read on every
retrieval. Edit via the UI (`/settings`), the API (`PUT /config`), or by
hand. Defaults below are used when the file is missing or partial.

Keeping this in its own module avoids circular imports between
``retrieve`` and ``server`` once the API exposes write endpoints.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mnemo import paths

log = logging.getLogger(__name__)


# --- Defaults -------------------------------------------------------------


@dataclass
class ScoringWeights:
    alpha: float = 0.40  # vector cosine
    beta: float = 0.15  # graph proximity
    gamma: float = 0.10  # recency
    delta: float = 0.10  # type priority
    epsilon: float = 0.05  # project scope
    zeta: float = 0.20  # lexical overlap (name+description match)


@dataclass
class Defaults:
    k: int = 5
    budget_tokens: int = 800


@dataclass
class Config:
    scoring: ScoringWeights = field(default_factory=ScoringWeights)
    defaults: Defaults = field(default_factory=Defaults)
    recency_half_life_days: float = 90.0


# --- Load / save ----------------------------------------------------------


_lock = threading.RLock()
_cache: tuple[Config, float] | None = None  # (config, mtime)


def _path() -> Path:
    paths.ensure_runtime_dirs()
    return paths.mnemo_home() / "settings.json"


def load() -> Config:
    """Read the on-disk settings, falling back to defaults for missing fields.

    Cached by mtime so a long-running daemon picks up edits without reloading
    on every query.
    """
    global _cache
    p = _path()
    mtime = p.stat().st_mtime if p.exists() else 0.0

    with _lock:
        if _cache is not None and _cache[1] == mtime:
            return _cache[0]

        cfg = Config()
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                _apply(cfg, raw)
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("could not read %s: %s; using defaults", p, exc)

        _cache = (cfg, mtime)
        return cfg


def save(cfg: Config) -> None:
    """Persist a Config and invalidate the cache."""
    p = _path()
    payload = {
        "scoring": asdict(cfg.scoring),
        "defaults": asdict(cfg.defaults),
        "recency_half_life_days": cfg.recency_half_life_days,
    }
    with _lock:
        p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        global _cache
        _cache = None  # force reload next time


def update(patch: dict) -> Config:
    """Patch the on-disk config (only fields present in ``patch`` change)."""
    cfg = load()
    _apply(cfg, patch)
    save(cfg)
    return cfg


def reset() -> Config:
    """Erase the settings file; defaults take over on next load."""
    p = _path()
    with _lock:
        if p.exists():
            p.unlink()
        global _cache
        _cache = None
    return load()


def _apply(cfg: Config, raw: dict) -> None:
    """Populate ``cfg`` in place from a possibly-partial dict."""
    if not isinstance(raw, dict):
        return
    if isinstance(raw.get("scoring"), dict):
        for k, v in raw["scoring"].items():
            if hasattr(cfg.scoring, k) and isinstance(v, int | float):
                setattr(cfg.scoring, k, float(v))
    if isinstance(raw.get("defaults"), dict):
        d = raw["defaults"]
        if isinstance(d.get("k"), int):
            cfg.defaults.k = max(1, min(200, d["k"]))
        if isinstance(d.get("budget_tokens"), int):
            cfg.defaults.budget_tokens = max(1, min(10_000, d["budget_tokens"]))
    if isinstance(raw.get("recency_half_life_days"), int | float):
        cfg.recency_half_life_days = max(1.0, float(raw["recency_half_life_days"]))
