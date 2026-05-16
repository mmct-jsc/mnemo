"""v3: BYO API-key resolution (design S7).

Precedence, highest first:

1. A real exported environment variable (``ANTHROPIC_API_KEY`` etc.) --
   always wins, so a process-level override beats everything.
2. The repo ``.env`` file (where the user dropped the temp key). Parsed
   directly -- we do NOT mutate ``os.environ`` (keeps tests isolated and
   lets the daemon re-resolve at runtime).
3. The OS keychain via ``keyring`` (wired in phase 7; optional import so
   phase 2 doesn't hard-depend on it).
4. A plaintext ``~/.claude/mnemo/keys.json`` last-resort fallback
   (Linux-without-Secret-Service path, design S7).

Ollama has no key (local) -- ``resolve_api_key('ollama')`` returns None
and that is not an error.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from mnemo import paths
from mnemo.providers import PROVIDERS

# C2 (v4.1): single-sourced from the provider registry (was a
# hand-maintained literal). Providers with no env var (Ollama) are
# absent, exactly as before.
ENV_VAR: dict[str, str] = {n: d.env_var for n, d in PROVIDERS.items() if d.env_var}


class KeyResolutionError(RuntimeError):
    """No API key could be resolved for a provider that requires one."""


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines. Ignores blanks / ``#`` comments and
    strips one layer of matching quotes. Never raises on a bad line."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def _discover_dotenv() -> Path | None:
    """Walk up from this package and the CWD looking for a ``.env``.

    The repo layout is ``<root>/.env`` with the daemon at
    ``<root>/daemon/mnemo``; the user's temp key lives at the repo
    root, so check a handful of parents plus the working directory.
    """
    candidates: list[Path] = []
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:6]:
        candidates.append(parent / ".env")
    candidates.append(Path.cwd() / ".env")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _keys_json_path() -> Path:
    return paths.mnemo_home() / "keys.json"


def _plaintext_key(provider: str) -> str | None:
    p = _keys_json_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    val = data.get(provider) if isinstance(data, dict) else None
    return val or None


def _keyring_key(provider: str) -> str | None:
    try:
        import keyring  # phase 7 dep; optional here
    except Exception:
        return None
    try:
        return keyring.get_password("mnemo", f"provider:{provider}") or None
    except Exception:
        return None


def resolve_api_key(provider: str, *, dotenv_path: Path | None = None) -> str | None:
    """Best key for ``provider`` per the documented precedence, or None."""
    var = ENV_VAR.get(provider)
    if var:
        env_val = os.environ.get(var)
        if env_val:
            return env_val
    p = dotenv_path or _discover_dotenv()
    if var and p and p.is_file():
        d = parse_env_file(p)
        if d.get(var):
            return d[var]
    kr = _keyring_key(provider)
    if kr:
        return kr
    return _plaintext_key(provider)


def resolve_api_key_tier(provider: str, *, dotenv_path: Path | None = None) -> str | None:
    """C4 (v4.2): WHERE resolve_api_key(provider) would get its value
    -- a read-only mirror of the same ladder. Returns the tier name
    ('env' | 'dotenv' | 'keychain' | 'file') or None. NEVER the secret;
    reuses the same helpers so the two cannot drift."""
    var = ENV_VAR.get(provider)
    if var and os.environ.get(var):
        return "env"
    p = dotenv_path or _discover_dotenv()
    if var and p and p.is_file() and parse_env_file(p).get(var):
        return "dotenv"
    if _keyring_key(provider):
        return "keychain"
    if _plaintext_key(provider):
        return "file"
    return None


def _set_keyring_key(provider: str, key: str) -> bool:
    """Store into the OS keychain. Returns False if keyring is missing
    or the platform has no Secret Service (Linux fallback path)."""
    try:
        import keyring
    except Exception:
        return False
    try:
        keyring.set_password("mnemo", f"provider:{provider}", key)
        return True
    except Exception:
        return False


def set_api_key(provider: str, key: str) -> dict:
    """Persist a provider key. Keychain first; on failure fall back to
    a plaintext ``keys.json`` (mode 0600) with a security warning
    (design S7 Linux fallback chain)."""
    if _set_keyring_key(provider, key):
        return {"stored": "keychain", "warning": None}
    p = _keys_json_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[provider] = key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    with contextlib.suppress(OSError):
        p.chmod(0o600)
    return {
        "stored": "plaintext",
        "warning": (
            "No OS keychain available; key written to "
            f"{p} (mode 0600). Prefer a keychain-backed environment."
        ),
    }


def _delete_keyring_key(provider: str) -> None:
    """Best-effort keychain removal (mirror of _set_keyring_key).
    Silent if keyring is missing or the entry does not exist."""
    try:
        import keyring
    except Exception:
        return
    with contextlib.suppress(Exception):
        keyring.delete_password("mnemo", f"provider:{provider}")


def delete_api_key(provider: str) -> None:
    """C4 (v4.2): remove a stored key (keychain + plaintext keys.json).
    No-op if absent. The env / .env tiers are not ours to delete --
    callers surface those read-only via resolve_api_key_tier."""
    _delete_keyring_key(provider)
    p = _keys_json_path()
    if not p.is_file():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(data, dict) and provider in data:
        data.pop(provider)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        with contextlib.suppress(OSError):
            p.chmod(0o600)


def has_key(provider: str) -> bool:
    """True if a key resolves for ``provider`` -- without revealing it."""
    return resolve_api_key(provider) is not None


def require_api_key(provider: str, *, dotenv_path: Path | None = None) -> str:
    key = resolve_api_key(provider, dotenv_path=dotenv_path)
    if not key:
        var = ENV_VAR.get(provider, "<env var>")
        raise KeyResolutionError(
            f"no API key for provider {provider!r}. Set {var}, add it to "
            f"the repo .env, or save it in mnemo Settings."
        )
    return key
