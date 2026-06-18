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


def _default_providers() -> dict:
    """C2 (v4.1): single-sourced from the provider registry (was a
    hand-maintained literal). Lazy import: runs at Config()
    instantiation, never at config.py import time."""
    from mnemo.providers import PROVIDERS

    return {n: {"model": d.default_model} for n, d in PROVIDERS.items()}


@dataclass
class Config:
    scoring: ScoringWeights = field(default_factory=ScoringWeights)
    defaults: Defaults = field(default_factory=Defaults)
    recency_half_life_days: float = 90.0
    # v1.1: project isolation behavior. 'strict' (default) hard-filters
    # query results to the active project's nodes plus any BASE-flagged
    # nodes. 'boost' falls back to the v1.0 behavior (no filter, just
    # scoring boost via epsilon). Useful when the user wants more
    # cross-project surfacing.
    project_isolation_mode: str = "strict"
    # v4.3.2: in 'strict' mode, a cross/inactive-project non-BASE
    # candidate is no longer HARD-dropped (that hid a dramatically
    # stronger exact match behind a weaker BASE node -- a silent-zero;
    # the user's "result seems wrong"). It is kept but its final score
    # is multiplied by this penalty, so BASE + in-project still win for
    # comparable relevance while a dominant cross-project match still
    # surfaces. 1.0 = no isolation; lower = stronger isolation. 0.85
    # tuned so a *comparable* cross-project node still loses to BASE/
    # in-project, but a *dramatically* stronger exact match (e.g. the
    # v4 handover, ~0.71 vs a 0.53 BASE node) still wins -- honoring
    # the principle, not over-penalizing.
    project_isolation_penalty: float = 0.85
    # v1.2 phase 2: inferred-re-query detector. When a new prompt is
    # cosine-similar to a query within the look-back window, the daemon
    # writes a `signal=-0.5, reason='inferred_requery'` row against the
    # older query's top-N hits. Tuned conservatively -- 0.85 cosine + 5
    # minute window matches the design doc's heuristic. Disable by
    # setting threshold > 1.0.
    requery_window_seconds: int = 300
    requery_cosine_threshold: float = 0.85
    requery_top_n_hits: int = 3
    # v1.2 phase 4: MMR re-rank lambda. 0.7 leans toward relevance
    # with enough diversity penalty to nuke near-duplicates. 1.0
    # bypasses MMR (pre-v1.2 behavior; saves ~0.5ms/query). 0.0 is
    # pure diversity, mostly a diagnostic.
    mmr_lambda: float = 0.7
    # v1.2 phase 5: auto-tuner minimum labeled-query threshold. Below
    # this count, ``mnemo retune`` refuses to run because MRR estimates
    # are too noisy. 30 is the design-doc default; users can lower it
    # for tighter feedback loops at the cost of overfit risk.
    retune_min_queries: int = 30
    # v6.1.0 governance: how hard the PreToolUse/Stop gates enforce a
    # `block` rule. 'off' = never gate; 'warn' (default) = surface the
    # would-block reason but allow; 'block' = actually deny/ask. Default
    # 'warn' so governance is non-aggressive until rules prove precise on
    # real sessions; flip per-deployment to 'block' for hard enforcement.
    # The env var MNEMO_GOVERNANCE_MODE overrides this; MNEMO_GOVERNANCE_BYPASS=1
    # downgrades any block to a warn (the escape hatch).
    governance_enforce_mode: str = "warn"
    # v3 phase 7: chat companion settings (design S7). Secrets are NOT
    # here -- API keys live in the OS keychain (mnemo.keys). Only
    # non-secret provider prefs (default + per-provider model) +
    # Mnem personality + history retention persist in settings.json.
    default_provider: str = "anthropic"
    providers: dict = field(default_factory=_default_providers)
    companion: dict = field(
        default_factory=lambda: {
            "name": "Mnem",
            "tone": "casual",
            "dock_state": "closed",
            "proactive": True,
            "proactive_pages": ["nebula", "node"],
            "proactive_frequency": "normal",
            # v5 phase 5: when the prompt-architect skill's retrieval
            # excludes any local_only nodes, the dock surfaces a banner
            # ("N local-only excluded; verify before pasting"). Default
            # ON because the prompt-architect output is paste-bound to
            # foreign LLMs -- safer to remind by default, let power
            # users opt out via Settings if their memory has no
            # local-only nodes worth warning about.
            "warn_on_local_only_exclusion": True,
        }
    )
    chat_history_retention_days: int | None = None
    # Phase 3b / Task 2.3: optional API-key auth on /v1/query.
    # **False by default** so self-host installs are entirely
    # unaffected. When True, requests from non-loopback clients
    # must present a valid `Authorization: Bearer <key>` header;
    # loopback (127.0.0.1 / ::1 / localhost) stays exempt so the
    # local UI + CLI + plugin keep working without keys. The
    # hosted-deployment doc (`docs/hosted/deploying.md`) is the
    # canonical operator reference for flipping this on.
    hosted_auth_enabled: bool = False


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
        "requery_window_seconds": cfg.requery_window_seconds,
        "requery_cosine_threshold": cfg.requery_cosine_threshold,
        "requery_top_n_hits": cfg.requery_top_n_hits,
        "mmr_lambda": cfg.mmr_lambda,
        "retune_min_queries": cfg.retune_min_queries,
        "project_isolation_mode": cfg.project_isolation_mode,
        "project_isolation_penalty": cfg.project_isolation_penalty,
        "default_provider": cfg.default_provider,
        "providers": cfg.providers,
        "companion": cfg.companion,
        "chat_history_retention_days": cfg.chat_history_retention_days,
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
    if isinstance(raw.get("project_isolation_mode"), str):
        mode = raw["project_isolation_mode"].strip().lower()
        if mode in ("strict", "boost"):
            cfg.project_isolation_mode = mode
    if isinstance(raw.get("project_isolation_penalty"), int | float):
        cfg.project_isolation_penalty = max(0.0, min(1.0, float(raw["project_isolation_penalty"])))
    if isinstance(raw.get("requery_window_seconds"), int):
        cfg.requery_window_seconds = max(0, int(raw["requery_window_seconds"]))
    if isinstance(raw.get("requery_cosine_threshold"), int | float):
        cfg.requery_cosine_threshold = max(0.0, min(2.0, float(raw["requery_cosine_threshold"])))
    if isinstance(raw.get("requery_top_n_hits"), int):
        cfg.requery_top_n_hits = max(0, min(100, int(raw["requery_top_n_hits"])))
    if isinstance(raw.get("mmr_lambda"), int | float):
        cfg.mmr_lambda = max(0.0, min(1.0, float(raw["mmr_lambda"])))
    if isinstance(raw.get("retune_min_queries"), int):
        cfg.retune_min_queries = max(1, int(raw["retune_min_queries"]))
    if isinstance(raw.get("governance_enforce_mode"), str):
        gm = raw["governance_enforce_mode"].strip().lower()
        if gm in ("off", "warn", "block"):
            cfg.governance_enforce_mode = gm
    # v3 phase 7 sections.
    if isinstance(raw.get("default_provider"), str):
        cfg.default_provider = raw["default_provider"].strip().lower()
    if isinstance(raw.get("providers"), dict):
        for name, pcfg in raw["providers"].items():
            if isinstance(pcfg, dict):
                slot = cfg.providers.setdefault(name, {})
                # Only the non-secret 'model' is persisted; a stray
                # 'key' (from the providers POST body) is dropped here.
                if isinstance(pcfg.get("model"), str):
                    slot["model"] = pcfg["model"]
    if isinstance(raw.get("companion"), dict):
        comp = raw["companion"]
        if isinstance(comp.get("name"), str):
            cfg.companion["name"] = comp["name"]
        if comp.get("tone") in ("formal", "casual", "quirky"):
            cfg.companion["tone"] = comp["tone"]
        if comp.get("dock_state") in ("closed", "docked-open", "pinned"):
            cfg.companion["dock_state"] = comp["dock_state"]
        if isinstance(comp.get("proactive"), bool):
            cfg.companion["proactive"] = comp["proactive"]
        if isinstance(comp.get("proactive_pages"), list):
            cfg.companion["proactive_pages"] = [str(x) for x in comp["proactive_pages"]]
        if comp.get("proactive_frequency") in ("minimal", "normal", "chatty"):
            cfg.companion["proactive_frequency"] = comp["proactive_frequency"]
        # v5 phase 5: pre-emit local-only warning toggle. Default True
        # (set by the dataclass factory); persisted by the Settings UI.
        if isinstance(comp.get("warn_on_local_only_exclusion"), bool):
            cfg.companion["warn_on_local_only_exclusion"] = comp["warn_on_local_only_exclusion"]
    if "chat_history_retention_days" in raw:
        rd = raw["chat_history_retention_days"]
        if rd is None or isinstance(rd, int):
            cfg.chat_history_retention_days = rd
