"""Plugin packaging tests: manifest + marketplace validity against the REAL
Claude Code plugin contract (v5.24.0).

Pre-v5.24.0 this file asserted mnemo's OWN invented schema -- a fictional
``platforms`` hook key + a flat ``{command, platforms}`` shape + bare-string
``commands``/``skills`` paths. Those passed CI while the plugin was, in fact,
unloadable: it shipped no ``marketplace.json`` (so it could not be installed)
and its hooks would never have fired. This rewrite pins the contract verified
against the on-disk ``plugin-dev`` reference + real installed examples:

- ``.claude-plugin/marketplace.json`` exists (single-repo, ``source: "./"``).
- ``plugin.json`` carries only valid fields; every component-path is
  ``./``-prefixed; NO ``platforms`` key anywhere.
- hooks live in ``./hooks/hooks.json`` (the canonical file), not inline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mnemo import __version__

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = REPO_ROOT / ".claude-plugin"
HOOKS_DIR = REPO_ROOT / "hooks"
COMMANDS_DIR = REPO_ROOT / "commands"

_KEBAB = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_PATH_FIELDS = ("commands", "agents", "skills", "hooks", "mcpServers")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def marketplace() -> dict:
    return json.loads((PLUGIN_DIR / "marketplace.json").read_text(encoding="utf-8"))


# --- plugin.json ----------------------------------------------------------


def test_manifest_exists() -> None:
    assert (PLUGIN_DIR / "plugin.json").is_file()


def test_manifest_required_keys(manifest: dict) -> None:
    for key in ("name", "version", "description", "license"):
        assert key in manifest, f"missing key: {key}"
    assert manifest["name"] == "mnemo"
    assert _KEBAB.match(manifest["name"]), "name must be kebab-case"
    assert manifest["license"] == "MIT"


def test_no_fictional_platforms_key(manifest: dict) -> None:
    """``platforms`` is not a real CC manifest/hook field; it was the bug."""
    assert "platforms" not in json.dumps(manifest)


def test_author_is_object_not_string(manifest: dict) -> None:
    """`claude plugin install` rejects a string author ("expected object,
    received string") even though the marketplace-level validator accepts it.
    Caught live during the v5.24.0 install verify; lock it so CI does too."""
    author = manifest.get("author")
    assert isinstance(author, dict), "plugin.json author must be an object {name: ...}"
    assert author.get("name"), "author object needs a name"


def test_component_paths_are_dot_slash_prefixed(manifest: dict) -> None:
    """CC requires component paths to start with ``./`` (forward slashes,
    no ``../``). The old bare-string ``"commands": "commands/"`` was invalid."""
    for key in _PATH_FIELDS:
        if key not in manifest:
            continue
        val = manifest[key]
        if isinstance(val, str):
            assert val.startswith("./"), f"{key} path must start with ./ (got {val!r})"
        elif isinstance(val, list):
            for v in val:
                assert isinstance(v, str), f"{key}: {v!r} must be a string path"
                assert v.startswith("./"), f"{key}: {v!r} must start with ./"
        # dict value (inline hooks/mcpServers) is a config object, not a path


def test_hooks_autodiscovered_not_double_referenced(manifest: dict) -> None:
    # Claude Code auto-loads ./hooks/hooks.json from the default location.
    # Referencing it in the manifest TOO triggers a "Duplicate hooks file"
    # load failure (caught live during the v5.24.0 install verify). So the
    # file must exist, and the manifest must NOT point `hooks` at it.
    assert (HOOKS_DIR / "hooks.json").is_file()
    assert manifest.get("hooks") != "./hooks/hooks.json", (
        "do not reference the auto-loaded hooks/hooks.json (causes a duplicate-load failure)"
    )


def test_no_legacy_bare_string_path_keys(manifest: dict) -> None:
    """commands/ + skills/ auto-discover from default dirs; the invalid
    bare-string keys must be gone (not merely corrected)."""
    assert manifest.get("commands") != "commands/"
    assert manifest.get("skills") != "skills/"


# --- marketplace.json -----------------------------------------------------


def test_marketplace_exists_and_valid(marketplace: dict) -> None:
    assert marketplace["name"], "marketplace needs a name"
    assert marketplace["owner"]["name"], "marketplace owner needs a name"
    plugins = marketplace["plugins"]
    assert isinstance(plugins, list), "marketplace plugins must be a list"
    assert plugins, "marketplace needs >=1 plugin"
    p = plugins[0]
    assert p["name"] == "mnemo"
    assert p["source"] == "./", "single-repo plugin uses source './'"


def test_versions_aligned(manifest: dict, marketplace: dict) -> None:
    """plugin.json, marketplace plugin entry, and __version__ must agree
    (catches the recurring forget-to-bump-one-file drift)."""
    assert manifest["version"] == __version__
    assert marketplace["plugins"][0]["version"] == __version__


# --- hook scripts are gone; the CLI replaces them -------------------------


def test_legacy_hook_scripts_removed() -> None:
    """The 6 hooks/*.sh + *.ps1 scripts are replaced by `mnemo hook <event>`."""
    leftovers = [p.name for p in HOOKS_DIR.glob("*.sh")] + [p.name for p in HOOKS_DIR.glob("*.ps1")]
    assert leftovers == [], f"legacy hook scripts should be deleted: {leftovers}"


# --- slash commands (unchanged contract) ----------------------------------


def test_all_slash_commands_present() -> None:
    expected_stems = {
        "mnemo-query",
        "mnemo-add",
        "mnemo-reindex",
        "mnemo-ui",
        "mnemo-status",
        "mnemo-hooks",
        "mnemo-show",
        "mnemo-prompt",
    }
    actual = {p.stem for p in COMMANDS_DIR.glob("*.md")}
    assert expected_stems.issubset(actual), f"missing commands: {expected_stems - actual}"


def test_command_files_have_frontmatter() -> None:
    for cmd_file in COMMANDS_DIR.glob("*.md"):
        text = cmd_file.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{cmd_file.name}: no frontmatter"
        assert "description:" in text.split("---\n")[1], f"{cmd_file.name}: missing description"
