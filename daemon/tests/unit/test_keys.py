"""v3 phase 2: BYO API-key resolution.

Precedence (design S7): a real exported env var ALWAYS wins, then the
repo ``.env`` (the temp key the user dropped there), then the OS
keychain (phase 7), then a plaintext ``keys.json`` fallback. No global
``os.environ`` mutation -- resolution reads sources directly so tests
stay isolated and the daemon can re-resolve at runtime.
"""

from __future__ import annotations

import json

import pytest

from mnemo import keys
from mnemo.keys import KeyResolutionError, require_api_key, resolve_api_key


def test_real_env_var_wins_over_dotenv(tmp_path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-real-env")
    assert resolve_api_key("anthropic", dotenv_path=env) == "from-real-env"


def test_dotenv_used_when_env_unset(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        '# comment\nANTHROPIC_API_KEY="sk-ant-from-dotenv"\nOTHER=ignored\n',
        encoding="utf-8",
    )
    assert resolve_api_key("anthropic", dotenv_path=env) == "sk-ant-from-dotenv"


def test_missing_key_returns_none_and_require_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    empty = tmp_path / ".env"
    empty.write_text("UNRELATED=x\n", encoding="utf-8")
    monkeypatch.setattr(keys, "_discover_dotenv", lambda: None)
    monkeypatch.setattr(keys, "_plaintext_key", lambda provider: None)
    monkeypatch.setattr(keys, "_keyring_key", lambda provider: None)
    assert resolve_api_key("anthropic", dotenv_path=empty) is None
    with pytest.raises(KeyResolutionError):
        require_api_key("anthropic", dotenv_path=empty)


def test_plaintext_keys_json_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(keys, "_discover_dotenv", lambda: None)
    monkeypatch.setattr(keys, "_keyring_key", lambda provider: None)
    kf = tmp_path / "keys.json"
    kf.write_text(json.dumps({"openai": "sk-openai-plain"}), encoding="utf-8")
    monkeypatch.setattr(keys, "_keys_json_path", lambda: kf)
    assert resolve_api_key("openai") == "sk-openai-plain"


def test_unknown_provider_has_no_env_mapping(monkeypatch) -> None:
    monkeypatch.setattr(keys, "_discover_dotenv", lambda: None)
    monkeypatch.setattr(keys, "_plaintext_key", lambda provider: None)
    monkeypatch.setattr(keys, "_keyring_key", lambda provider: None)
    # ollama is local-only (no key); resolve must not raise, just return None
    assert resolve_api_key("ollama") is None
