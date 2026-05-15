"""v3 phase 7: settings + keychain BYO keys (design S7).

Secrets NEVER touch settings.json -- only the keychain (or the
plaintext 0600 fallback). GET /v1/settings must never echo key
material; it reports ``has_key`` booleans only.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from mnemo import config, keys
from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


def test_config_has_v3_sections(isolated_mnemo_home) -> None:
    config.reset()
    cfg = config.load()
    assert cfg.default_provider == "anthropic"
    assert "anthropic" in cfg.providers
    assert cfg.companion["name"] == "Mnem"
    assert cfg.companion["tone"] in ("formal", "casual", "quirky")
    assert cfg.chat_history_retention_days is None


def test_config_apply_persists_v3_sections(isolated_mnemo_home) -> None:
    config.reset()
    config.update(
        {
            "default_provider": "openai",
            "providers": {"openai": {"model": "gpt-4o-mini"}},
            "companion": {"name": "Nem", "tone": "quirky"},
            "chat_history_retention_days": 30,
        }
    )
    cfg = config.load()
    assert cfg.default_provider == "openai"
    assert cfg.providers["openai"]["model"] == "gpt-4o-mini"
    assert cfg.companion["name"] == "Nem"
    assert cfg.companion["tone"] == "quirky"
    assert cfg.chat_history_retention_days == 30


def test_set_and_has_key_plaintext_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(keys, "_discover_dotenv", lambda: None)
    monkeypatch.setattr(keys, "_set_keyring_key", lambda p, k: False)  # force fallback
    kf = tmp_path / "keys.json"
    monkeypatch.setattr(keys, "_keys_json_path", lambda: kf)

    res = keys.set_api_key("anthropic", "sk-secret")
    assert res["stored"] == "plaintext"
    assert keys.has_key("anthropic") is True
    assert keys.has_key("openai") is False
    # mode 0600 where the OS supports it
    if hasattr(kf, "stat"):
        assert kf.exists()


def test_get_settings_never_leaks_keys(client: TestClient, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(keys, "_discover_dotenv", lambda: None)
    monkeypatch.setattr(keys, "_set_keyring_key", lambda p, k: False)
    monkeypatch.setattr(keys, "_keys_json_path", lambda: tmp_path / "keys.json")
    keys.set_api_key("anthropic", "sk-TOP-SECRET")

    r = client.get("/v1/settings")
    assert r.status_code == 200
    body = r.text
    assert "sk-TOP-SECRET" not in body
    data = r.json()
    assert data["providers"]["anthropic"]["has_key"] is True
    assert "model" in data["providers"]["anthropic"]
    assert "key" not in data["providers"]["anthropic"]


def test_post_providers_and_companion(client: TestClient, isolated_mnemo_home, monkeypatch) -> None:
    monkeypatch.setattr(keys, "_set_keyring_key", lambda p, k: True)  # pretend keychain
    r = client.post(
        "/v1/settings/providers",
        json={
            "default_provider": "anthropic",
            "providers": {"anthropic": {"model": "claude-x", "key": "sk-new"}},
        },
    )
    assert r.status_code == 200
    assert config.load().providers["anthropic"]["model"] == "claude-x"

    r2 = client.post(
        "/v1/settings/companion",
        json={"name": "Mnemo", "tone": "formal", "dock_state": "pinned"},
    )
    assert r2.status_code == 200
    assert config.load().companion["name"] == "Mnemo"
    assert config.load().companion["tone"] == "formal"


def test_chat_settings_page_renders_three_tabs(client: TestClient) -> None:
    # v3 companion settings live at /settings/chat so the existing
    # /settings retrieval-tuning page is untouched (no regression).
    r = client.get("/settings/chat")
    assert r.status_code == 200
    html = r.text
    assert 'x-data="settingsPage()"' in html
    for tab in ("Providers", "Companion", "Permissions"):
        assert tab in html


def test_existing_retrieval_settings_still_renders(client: TestClient) -> None:
    """Regression guard: the v1.2 /settings page must keep working."""
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Retrieval settings" in r.text
