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

import json
import os
from pathlib import Path

from mnemo import paths

ENV_VAR: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}


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


def require_api_key(provider: str, *, dotenv_path: Path | None = None) -> str:
    key = resolve_api_key(provider, dotenv_path=dotenv_path)
    if not key:
        var = ENV_VAR.get(provider, "<env var>")
        raise KeyResolutionError(
            f"no API key for provider {provider!r}. Set {var}, add it to "
            f"the repo .env, or save it in mnemo Settings."
        )
    return key
