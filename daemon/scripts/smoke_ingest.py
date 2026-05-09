"""Manual smoke test: index real memory under ``~/.claude/`` into a temp DB.

Run after ingestion changes to confirm parsing handles real-world memory files.

    uv run python scripts/smoke_ingest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from mnemo import ingest, paths
from mnemo.store import Store


def _safe(text: str) -> str:
    return text.encode("ascii", errors="replace").decode("ascii")


def main() -> None:
    # Force stdout to utf-8 if possible so we don't choke on memory file content.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db = Path(tmpdir) / "smoke.db"
        store = Store(db)
        try:
            n_new = ingest.register_default_sources(store, paths.claude_home())
            print(f"Registered {n_new} new sources")

            sources = store.list_sources()
            print(f"Total sources: {len(sources)}")

            report = ingest.reindex(store)
            print(
                f"Reindex: added={report.added}, updated={report.updated}, "
                f"unchanged={report.unchanged}, removed={report.removed}, "
                f"errors={len(report.errors)}"
            )
            for path, err in report.errors[:5]:
                print(f"  ERROR {_safe(path)}: {_safe(err)}")

            counts = store.count_nodes()
            print(f"Node counts: {counts}")

            print("Sample nodes:")
            for node in store.list_nodes(limit=10):
                desc = _safe((node.description or "")[:60].replace("\n", " "))
                name = _safe(node.name[:35])
                print(f"  [{node.type:18}] {name:35}  {desc}")
        finally:
            store.close()


if __name__ == "__main__":
    main()
