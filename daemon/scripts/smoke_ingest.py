"""End-to-end smoke test: index + embed real memory, then query.

Run after ingestion / embed / store changes:

    uv run python scripts/smoke_ingest.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from mnemo import ingest, paths
from mnemo.embed import Embedder, embed_all_unembedded
from mnemo.store import Store


def _safe(text: str) -> str:
    return text.encode("ascii", errors="replace").decode("ascii")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db = Path(tmpdir) / "smoke.db"
        store = Store(db)
        try:
            t0 = time.time()
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

            t1 = time.time()
            print(f"Ingestion: {t1 - t0:.2f}s")

            embedder = Embedder()
            n_embedded = embed_all_unembedded(store, embedder)
            t2 = time.time()
            print(f"Embedded {n_embedded} nodes in {t2 - t1:.2f}s "
                  f"(includes {embedder.dim}-d MiniLM model load)")

            # Three sample queries hitting different parts of the memory.
            queries = [
                "no co-author trailer in commit messages",
                "MQTT broker authentication credentials",
                "godot child timer cinematic safety",
            ]
            for q in queries:
                vec = embedder.embed_text(q)
                results = store.vec_search(vec, k=3)
                print(f"\nQuery: {_safe(q)!r}")
                for node_id, _idx, _text, dist in results:
                    node = store.get_node(node_id)
                    if node is None:
                        continue
                    desc = _safe((node.description or "")[:60].replace("\n", " "))
                    name = _safe(node.name[:35])
                    print(f"  d={dist:.3f}  [{node.type:18}] {name:35}  {desc}")
        finally:
            store.close()


if __name__ == "__main__":
    main()
