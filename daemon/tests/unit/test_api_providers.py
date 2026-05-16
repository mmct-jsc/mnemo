"""C2 (v4.1): GET /v1/providers exposes the registry (feeds C4 v4.2)."""

from fastapi.testclient import TestClient

from mnemo.server import create_app


def test_v1_providers_exposes_the_registry() -> None:
    client = TestClient(create_app())
    r = client.get("/v1/providers")
    assert r.status_code == 200
    data = {p["name"]: p for p in r.json()}
    assert {"anthropic", "openai", "google", "ollama"} <= set(data)

    a = data["anthropic"]
    assert a["requires_key"] is True
    assert a["env_var"] == "ANTHROPIC_API_KEY"
    assert a["default_model"] == "claude-sonnet-4-5-20250929"
    assert a["default_model"] in a["known_models"]
    assert "claude-sonnet-4-6" in a["supports_compaction_models"]

    assert data["ollama"]["requires_key"] is False
    assert data["ollama"]["env_var"] is None
