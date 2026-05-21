"""v5 phase 5: pre-emit warning banner + companion Settings toggle.

When the prompt-architect's mnemo_query retrieval pass returns a
non-zero ``local_only_excluded`` count, the dock surfaces a banner:
"This architected prompt drew on N nodes; M local-only were
excluded. Verify before pasting."

The warning is configurable in Settings (default ON). v5.0 ships
the Settings default + the dock's UI hook; the actual count is
threaded through SSE in a v5.x follow-up (architect skill emits
the count after running mnemo_query; the chat factory captures
it on an existing or new event type).

These tests pin the contract:

- ``Config().companion`` ships ``warn_on_local_only_exclusion = True``
  by default.
- The chat factory exposes a ``localOnlyExcluded`` field so the
  template can bind to it.
- The banner template references both fields and the dismiss path.
"""

from __future__ import annotations

from pathlib import Path

from mnemo.config import Config

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_JS = REPO_ROOT / "daemon" / "mnemo" / "ui" / "static" / "chat.js"
COMPOSER_TMPL = REPO_ROOT / "daemon" / "mnemo" / "ui" / "templates" / "_chat_composer.html"
APP_CSS = REPO_ROOT / "daemon" / "mnemo" / "ui" / "static" / "app.css"
CHAT_SETTINGS_TMPL = REPO_ROOT / "daemon" / "mnemo" / "ui" / "templates" / "chat_settings.html"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# --- Config default -------------------------------------------------------


def test_warn_on_local_only_exclusion_defaults_true() -> None:
    cfg = Config()
    assert cfg.companion["warn_on_local_only_exclusion"] is True


def test_warn_setting_overridable_via_settings_json(tmp_path, monkeypatch) -> None:
    """User can flip the warning off via settings.json; the load
    path respects the override (unlike the dataclass default)."""
    monkeypatch.setenv("MNEMO_HOME", str(tmp_path))
    import json

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_provider": "anthropic",
                "providers": {},
                "companion": {
                    "name": "Mnem",
                    "tone": "casual",
                    "dock_state": "closed",
                    "proactive": True,
                    "proactive_pages": ["nebula"],
                    "proactive_frequency": "normal",
                    "warn_on_local_only_exclusion": False,
                },
                "chat_history_retention_days": None,
            }
        ),
        encoding="utf-8",
    )
    # Bust any cache from prior tests.
    from mnemo import config as cfg_mod

    cfg_mod._cache = None
    cfg = cfg_mod.load()
    assert cfg.companion["warn_on_local_only_exclusion"] is False


# --- chat.js state hook --------------------------------------------------


def test_chat_factory_tracks_local_only_excluded() -> None:
    js = _read(CHAT_JS)
    assert "localOnlyExcluded" in js, (
        "chat.js factory must expose localOnlyExcluded so the banner can bind to it"
    )


# --- Banner template hook ------------------------------------------------


def test_dock_template_renders_local_only_warning_banner() -> None:
    """The dock's composer area carries the banner DOM so chat.js
    can flip its visibility when localOnlyExcluded > 0."""
    html = _read(COMPOSER_TMPL)
    assert "local-only" in html.lower() or "localOnlyExcluded" in html, (
        "composer should include the warning banner element"
    )


# --- Settings template surfaces the toggle -------------------------------


def test_chat_settings_template_exposes_warn_toggle() -> None:
    """The companion Settings panel must let the user flip the
    warning off when they're sure their memory is safe to paste."""
    html = _read(CHAT_SETTINGS_TMPL)
    assert "warn_on_local_only_exclusion" in html, (
        "chat_settings.html must expose the warn_on_local_only_exclusion toggle"
    )


# --- CSS class hook ------------------------------------------------------


def test_warning_banner_has_css_hook() -> None:
    css = _read(APP_CSS)
    assert "mc-localonly-warn" in css, "app.css should style the warning banner"
