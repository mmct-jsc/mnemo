"""Provider-specific shims. Each shim module:

- Defines a small ``Shim`` instance with ``matches(client)``,
  ``install(client, *, mode)`` returning a PatchState, and
  ``uninstall(client, state)``.
- Registers itself with ``_patcher.register_shim`` at import time.

Importing this package imports all shims (cheap -- they don't touch
their provider SDK at module load time, only when a call comes in).
"""

from __future__ import annotations

from mnemo_middleware._shims import anthropic, google, ollama, openai  # noqa: F401
