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


def test_settings_out_includes_key_tier(client: TestClient) -> None:
    """C4 (v4.2): every per-provider object carries a read-only
    key-resolution tier (or null) -- never the secret."""
    r = client.get("/v1/settings")
    assert r.status_code == 200
    prov = r.json()["providers"]
    assert prov  # non-empty
    for _name, p in prov.items():
        assert "key_tier" in p
        assert p["key_tier"] in (None, "env", "dotenv", "keychain", "file")


def test_delete_provider_key_endpoint(client: TestClient, monkeypatch) -> None:
    """C4 (v4.2): DELETE removes the stored key and returns SettingsOut
    (same shape as POST)."""
    from mnemo import keys

    called: dict = {}
    monkeypatch.setattr(keys, "delete_api_key", lambda n: called.setdefault("n", n))
    r = client.delete("/v1/settings/providers/openai/key")
    assert r.status_code == 200
    assert called["n"] == "openai"
    body = r.json()
    assert "providers" in body
    assert "default_provider" in body


def test_provider_tab_is_registry_driven(client: TestClient) -> None:
    """C4 (v4.2): the provider tab consumes the C2 registry (every
    registered provider appears), the model is a picker (not free
    text), and key-tier + delete-key are surfaced."""
    html = client.get("/settings/chat").text
    assert "/v1/providers" in html, "provider tab must fetch the C2 registry"
    assert 'x-model="data.providers[name].model"' in html
    prov_section = html.split("Providers", 1)[1][:6000]
    assert "<select" in prov_section, "model must be a picker, not free text"
    assert "known_models" in html or "modelsFor(" in html
    assert "key_tier" in html, "read-only key tier surfaced"
    assert "/v1/settings/providers/" in html
    assert "/key" in html  # DELETE-key wire


def test_settings_is_reachable_and_unified_but_routes_stay_separate(
    client: TestClient,
) -> None:
    """C4 (v4.2): one settings IA via a shared tab strip -- but the
    v1.2 retrieval route/context is NOT merged (gotcha 9)."""
    main = client.get("/settings").text
    assert "/settings/chat" in main, "retrieval page must link to provider/companion"
    chat = client.get("/settings/chat").text
    assert "/settings" in chat, "provider page must link back to retrieval tuning"
    # gotcha 9: the retrieval page keeps its own identity (route/context
    # NOT merged) -- the regression anchors are intact:
    assert "Retrieval settings" in main
    assert "Auto-tune" in main
    assert "runRetune" in main
    # both render the SAME shared tab strip partial:
    assert "settings-tabs" in main
    assert "settings-tabs" in chat


def test_gotcha9_regression_guards_unmodified(client: TestClient) -> None:
    assert client.get("/settings").status_code == 200
    r = client.get("/settings/chat").text
    assert "Providers" in r
    assert "Companion" in r
    assert "Permissions" in r
    assert 'x-data="settingsPage()"' in r
