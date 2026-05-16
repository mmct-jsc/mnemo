"""v3.2 phase 5: the companion as a Settings retune assistant.

The settings page_context already carries weights / k / recent_feedback
(P2). The companion reads memory (mnemo_query), proposes scoring-weight
deltas + explains the tradeoffs, and -- confirm-risk -- applies them via
``mnemo_apply_retune``; the Settings page validates by re-pulling
``/v1/config`` and showing the before/after on the live inputs (design
v3.2 S3.5). The tool is scoped to ONLY the 6 scoring weights (a bounded,
recoverable confirm surface -- NOT the danger mnemo_change_settings
catch-all).

Alpine can't run under pytest, so the page-validate half is asserted by
a settings.html surface grep.
"""

from __future__ import annotations

from pathlib import Path

from mnemo import config
from mnemo.agent_tools import TOOLS, ToolContext
from mnemo.store import Store

_UI = Path(__file__).resolve().parents[2] / "mnemo" / "ui"
SETTINGS_HTML = (_UI / "templates" / "settings.html").read_text(encoding="utf-8")


# --- mnemo_apply_retune (confirm) --------------------------------------


def test_mnemo_apply_retune_registered_confirm() -> None:
    assert "mnemo_apply_retune" in TOOLS
    spec = TOOLS["mnemo_apply_retune"]
    assert spec.risk == "confirm"  # bounded + recoverable, NOT danger
    assert spec.parameters.get("type") == "object"
    assert "weights" in spec.parameters.get("properties", {})
    assert spec.description.strip()


def test_mnemo_apply_retune_applies_and_returns_before_after(
    isolated_mnemo_home, store: Store
) -> None:
    config.reset()
    before_alpha = config.load().scoring.alpha
    out = TOOLS["mnemo_apply_retune"].fn(
        ToolContext(store=store), weights={"alpha": 0.55, "zeta": 0.05}
    )
    assert out["ok"] is True
    assert out["before"]["alpha"] == before_alpha
    assert out["after"]["alpha"] == 0.55
    assert out["after"]["zeta"] == 0.05
    assert set(out["applied"]) == {"alpha", "zeta"}
    # persisted to the on-disk config
    assert config.load().scoring.alpha == 0.55
    assert config.load().scoring.zeta == 0.05


def test_mnemo_apply_retune_rejects_unknown_keys(isolated_mnemo_home, store: Store) -> None:
    config.reset()
    out = TOOLS["mnemo_apply_retune"].fn(
        ToolContext(store=store), weights={"alpha": 0.3, "bogus": 9}
    )
    assert "alpha" in out["applied"]
    assert "bogus" in out["ignored"]
    assert config.load().scoring.alpha == 0.3
    assert not hasattr(config.load().scoring, "bogus")


def test_mnemo_apply_retune_nonnumeric_is_safe(isolated_mnemo_home, store: Store) -> None:
    config.reset()
    out = TOOLS["mnemo_apply_retune"].fn(ToolContext(store=store), weights={"alpha": "high"})
    assert out["applied"] == []
    assert "alpha" in out["ignored"]
    assert "error" not in out  # the tool never raises on bad input
    assert config.load().scoring.alpha == 0.40  # unchanged default


def test_mnemo_apply_retune_empty_weights_is_a_noop(isolated_mnemo_home, store: Store) -> None:
    config.reset()
    out = TOOLS["mnemo_apply_retune"].fn(ToolContext(store=store), weights={})
    assert out["applied"] == []
    assert out["before"] == out["after"]
    assert "error" not in out


# --- client surface: the page validates before/after -------------------


def test_settings_validates_retune_before_after() -> None:
    # the companion signal (reusing the existing ui_action infra) makes
    # the page re-pull /v1/config and show the new weights
    assert "validateRetune" in SETTINGS_HTML
    assert "/v1/config" in SETTINGS_HTML
    assert "mnemo-retune" in SETTINGS_HTML
    # it surfaces the before -> after delta (not a silent swap)
    assert "retune-validated" in SETTINGS_HTML
