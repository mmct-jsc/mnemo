"""End-to-end smoke test: index + embed real memory, then run retrieval.

Run after ingestion / embed / retrieve changes:

    uv run python scripts/smoke_ingest.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from mnemo import ingest, paths, retrieve
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
            print(f"Total sources: {len(store.list_sources())}")

            report = ingest.reindex(store)
            print(
                f"Reindex: added={report.added}, updated={report.updated}, "
                f"unchanged={report.unchanged}, removed={report.removed}, "
                f"errors={len(report.errors)}"
            )
            for path, err in report.errors[:5]:
                print(f"  ERROR {_safe(path)}: {_safe(err)}")
            print(f"Node counts: {store.count_nodes()}")
            t1 = time.time()
            print(f"Ingestion: {t1 - t0:.2f}s")

            embedder = Embedder()
            n_embedded = embed_all_unembedded(store, embedder)
            t2 = time.time()
            print(
                f"Embedded {n_embedded} nodes in {t2 - t1:.2f}s "
                f"(includes {embedder.dim}-d MiniLM model load)"
            )

            queries = [
                "no co-author trailer in commit messages",
                "MQTT broker authentication credentials",
                "godot child timer cinematic safety",
                "where do we keep deployment files",
                "should I always prefer terse responses",
            ]
            for q in queries:
                result = retrieve.query(store, embedder, q, k=5, budget_tokens=400)
                print(
                    f"\nQuery: {_safe(q)!r}"
                    f"   intent={result.intent_tags} tokens_used={result.tokens_used}"
                )
                for hit in result.hits:
                    name = _safe(hit.name[:35])
                    desc = _safe((hit.description or "")[:55].replace("\n", " "))
                    print(
                        f"  s={hit.score:.3f}  [{hit.type:18}] {name:35}  {desc}"
                    )
        finally:
            store.close()


if __name__ == "__main__":
    main()
