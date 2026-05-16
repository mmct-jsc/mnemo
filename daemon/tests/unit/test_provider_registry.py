"""C2 Provider Contract (v4.1) -- registry mirrors agent_tools.TOOLS."""

import pytest

from mnemo.providers import (
    PROVIDERS,
    ProviderDescriptor,
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
