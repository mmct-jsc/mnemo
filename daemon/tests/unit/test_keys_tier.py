"""C4 (v4.2): key-resolution-tier indicator + delete-key.

resolve_api_key_tier is a read-only mirror of the resolve_api_key
ladder (env -> dotenv -> keychain -> file); it returns WHERE a key
would come from, never the secret. delete_api_key removes the stored
key (keychain + plaintext); env/.env tiers are not ours to delete.
"""

import json

from mnemo import keys


def test_resolve_tier_reports_env_when_env_set(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert keys.resolve_api_key_tier("anthropic") == "env"


def test_resolve_tier_dotenv(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('OPENAI_API_KEY="sk-dot"\n', encoding="utf-8")
    monkeypatch.setattr(keys, "_keyring_key", lambda p: None)
    monkeypatch.setattr(keys, "_plaintext_key", lambda p: None)
    assert keys.resolve_api_key_tier("openai", dotenv_path=env) == "dotenv"


def test_resolve_tier_none_when_unresolvable(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(keys, "_discover_dotenv", lambda: None)
    monkeypatch.setattr(keys, "_keyring_key", lambda p: None)
    monkeypatch.setattr(keys, "_plaintext_key", lambda p: None)
    assert keys.resolve_api_key_tier("openai") is None


def test_resolve_tier_ollama_has_no_env_var(monkeypatch) -> None:
    monkeypatch.setattr(keys, "_discover_dotenv", lambda: None)
    monkeypatch.setattr(keys, "_keyring_key", lambda p: None)
    monkeypatch.setattr(keys, "_plaintext_key", lambda p: None)
    assert keys.resolve_api_key_tier("ollama") is None


def test_delete_api_key_removes_plaintext(tmp_path, monkeypatch) -> None:
    kp = tmp_path / "keys.json"
    kp.write_text(json.dumps({"openai": "sk-file", "google": "sk-g"}), encoding="utf-8")
    monkeypatch.setattr(keys, "_keys_json_path", lambda: kp)
    monkeypatch.setattr(keys, "_delete_keyring_key", lambda p: None)
    keys.delete_api_key("openai")
    remaining = json.loads(kp.read_text(encoding="utf-8"))
    assert "openai" not in remaining
    assert remaining["google"] == "sk-g"  # other keys untouched


def test_delete_api_key_noop_when_absent(tmp_path, monkeypatch) -> None:
    kp = tmp_path / "keys.json"  # does not exist
    monkeypatch.setattr(keys, "_keys_json_path", lambda: kp)
    monkeypatch.setattr(keys, "_delete_keyring_key", lambda p: None)
    keys.delete_api_key("openai")  # must not raise
