"""v5.25.0 step 7: ``import mnemo.cli`` must stay LIGHT.

Every Claude Code hook fire (each prompt, each Edit/Write, in EVERY open
session) and every statusline refresh spawns a fresh python that imports
mnemo.cli. Live profiling showed the module-top imports pulling
ingest -> git_log -> store -> sqlite_vec -> numpy (~1.1s of a 1.6s cold
import) that most spawns never use. Heavy deps must load inside the
commands that need them.
"""

from __future__ import annotations

import subprocess
import sys

HEAVY = (
    "numpy",
    "sqlite_vec",
    "sentence_transformers",
    "torch",
    "mnemo.ingest",
    "mnemo.retrieve",
    "mnemo.store",
    "mnemo.embed",
    "mnemo.auto_router",
)


def test_import_mnemo_cli_pulls_no_heavy_modules() -> None:
    code = (
        f"import sys; import mnemo.cli; print(','.join(m for m in {HEAVY!r} if m in sys.modules))"
    )
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    leaked = proc.stdout.strip()
    assert leaked == "", (
        f"heavy modules leaked into `import mnemo.cli`: {leaked}. "
        "Keep them function-local -- every hook/statusline spawn pays this."
    )
