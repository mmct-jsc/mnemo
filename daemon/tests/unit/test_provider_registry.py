"""C2 Provider Contract (v4.1) -- registry mirrors agent_tools.TOOLS."""

import pytest

from mnemo.providers import (
    DEFAULT_MODELS,
    PROVIDERS,
    BaseProvider,
    ProviderDescriptor,
    get_provider,
    register_provider,
)


def test_descriptor_has_the_contract_fields() -> None:
    d = ProviderDescriptor(
        name="x",
        display_name="X",
        impl_class=object,
        env_var=None,
        requires_key=False,
        default_model="m",
        known_models=("m",),
        base_url=None,
        native_compaction_models=frozenset(),
    )
    assert d.name == "x"
    assert d.requires_key is False


def test_register_provider_stores_and_rejects_dupes() -> None:
    desc = ProviderDescriptor(
        name="dupe-probe",
        display_name="Dupe",
        impl_class=object,
        env_var=None,
        requires_key=False,
        default_model="m",
        known_models=("m",),
        base_url=None,
        native_compaction_models=frozenset(),
    )
    register_provider(desc)
    assert PROVIDERS["dupe-probe"] is desc
    with pytest.raises(ValueError, match="duplicate provider registration"):
        register_provider(desc)
    del PROVIDERS["dupe-probe"]  # keep global registry clean for other tests


def test_all_four_providers_registered_with_correct_fields() -> None:
    import mnemo.providers  # noqa: F401  (triggers bottom-of-__init__ imports)

    assert {"anthropic", "openai", "google", "ollama"} <= set(PROVIDERS)

    a = PROVIDERS["anthropic"]
    assert a.env_var == "ANTHROPIC_API_KEY"
    assert a.requires_key is True
    assert a.default_model == "claude-sonnet-4-5-20250929"  # UNCHANGED
    assert {
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
    } <= a.native_compaction_models

    o = PROVIDERS["ollama"]
    assert o.env_var is None  # ollama needs no key
    assert o.requires_key is False
    assert o.native_compaction_models == frozenset()

    for name in ("openai", "google"):
        assert PROVIDERS[name].requires_key is True
        assert PROVIDERS[name].env_var is not None
        assert PROVIDERS[name].default_model  # non-empty, preserves DEFAULT_MODELS


def test_get_provider_and_default_models_derive_from_registry() -> None:
    # DEFAULT_MODELS is now a registry-derived view (identical values):
    assert set(DEFAULT_MODELS) == set(PROVIDERS)
    for n, d in PROVIDERS.items():
        assert DEFAULT_MODELS[n] == d.default_model

    # get_provider DERIVES from PROVIDERS: a provider registered at
    # runtime is constructible -- a hand-written if/elif chain could
    # never resolve a name it was not edited to know about.
    class _Dummy(BaseProvider):
        name = "dummy-derive"

    register_provider(
        ProviderDescriptor(
            name="dummy-derive",
            display_name="D",
            impl_class=_Dummy,
            env_var=None,
            requires_key=False,
            default_model="d",
            known_models=("d",),
            base_url=None,
            native_compaction_models=frozenset(),
        )
    )
    try:
        assert isinstance(get_provider("dummy-derive"), _Dummy)
    finally:
        del PROVIDERS["dummy-derive"]

    # message shape preserved for test_get_provider_unknown_raises:
    with pytest.raises(ValueError, match="unknown provider"):
        get_provider("nope-not-real")


def test_keys_config_compaction_are_single_sourced_from_registry() -> None:
    from mnemo import compaction, keys
    from mnemo.config import Config

    for n, d in PROVIDERS.items():
        if d.env_var:
            assert keys.ENV_VAR[n] == d.env_var
    assert "ollama" not in keys.ENV_VAR  # no env var -> not in the map

    cfg = Config()
    assert set(cfg.providers) == set(PROVIDERS)
    for n, d in PROVIDERS.items():
        assert cfg.providers[n]["model"] == d.default_model

    assert compaction.supports_native_compaction("anthropic", "claude-sonnet-4-6") is True
    assert compaction.supports_native_compaction("anthropic", "claude-sonnet-4-5-20250929") is False
    assert compaction.supports_native_compaction("openai", "gpt-4o-mini") is False
    assert compaction.supports_native_compaction("nope", "x") is False


def test_no_duplicate_capability_tables() -> None:
    """The 4 scattered hand-maintained tables are gone; PROVIDERS is the
    only source (this is the contract's teeth -- proves single-sourcing,
    not value coincidence)."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2]  # daemon/
    keys_txt = (src / "mnemo" / "keys.py").read_text(encoding="utf-8")
    comp_txt = (src / "mnemo" / "compaction.py").read_text(encoding="utf-8")
    cfg_txt = (src / "mnemo" / "config.py").read_text(encoding="utf-8")

    assert "PROVIDERS" in keys_txt
    assert '"OPENAI_API_KEY"' not in keys_txt, "keys.ENV_VAR must derive from PROVIDERS"
    assert "PROVIDERS" in comp_txt
    assert "claude-opus-4-7" not in comp_txt, (
        "compaction must derive native_compaction_models from PROVIDERS"
    )
    assert "PROVIDERS" in cfg_txt
    assert '"gpt-4o-mini"' not in cfg_txt, "Config.providers default must derive from PROVIDERS"
