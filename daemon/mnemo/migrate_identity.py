"""v5.28.0: code-node identity migration + dry-run report.

The migration itself is LAZY -- it happens during any reindex via the
reconcile fallback (:func:`mnemo.ingest._migrate_legacy_code_node`).
This module wraps a reindex and DIFFS code-node identity before/after so
the impact can be reviewed: how many legacy line-range nodes were
re-keyed IN PLACE (id preserved, no churn, no re-embed) vs how many
would be orphaned (genuinely-removed code). :func:`dry_run_from_db` runs
it against a throwaway COPY of the live DB so nothing live is touched
until the numbers are approved -- the safety gate for the migration.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from mnemo import ingest
from mnemo.parsers import code
from mnemo.store import Store


def _is_legacy_code_key(source_path: str) -> bool:
    """True if a code node is still on a legacy line-range key
    (``<file>:<start>-<end>``) -- i.e. not yet migrated to the stable
    ``::`` form. Module / endpoint keys (no line range) are not legacy."""
    if "::" in source_path:
        return False
    _f, rng = code.code_file_and_range(source_path, None)
    return rng is not None


def _code_keys(store: Store) -> dict[str, str]:
    return {
        n.id: n.source_path for n in store.list_nodes(limit=10**9) if n.type.startswith("code_")
    }


def run_code_identity_migration(store: Store, *, embedder: object | None = None) -> dict:
    """Run the lazy migration (via a reindex) against ``store`` and report
    the effect on code-node identity.

    MUTATES the store -- the caller decides whether it is a throwaway
    copy (dry-run) or the live DB (apply). The headline safety signals:
    ``rekeyed_in_place`` should track ``legacy_before`` and
    ``would_orphan`` should be small (only genuinely-removed code); a
    high ``new`` with low ``rekeyed_in_place`` would mean churn.
    """
    before = _code_keys(store)
    legacy_before = {nid for nid, sp in before.items() if _is_legacy_code_key(sp)}

    ingest.reindex(store, embedder=embedder)

    after = _code_keys(store)
    rekeyed = sum(1 for nid in legacy_before if nid in after and "::" in after[nid])
    still_legacy = sum(
        1 for nid in legacy_before if nid in after and _is_legacy_code_key(after[nid])
    )
    would_orphan = sum(1 for nid in before if nid not in after)
    new = sum(1 for nid in after if nid not in before)
    return {
        "code_nodes_before": len(before),
        "code_nodes_after": len(after),
        "legacy_before": len(legacy_before),
        "rekeyed_in_place": rekeyed,
        "still_legacy_after": still_legacy,
        "would_orphan": would_orphan,
        "new": new,
        "id_preserved_pct": (
            round(100.0 * rekeyed / len(legacy_before), 1) if legacy_before else 100.0
        ),
    }


def dry_run_from_db(db_path: Path) -> dict:
    """Copy the DB to a temp file, run the migration against the COPY,
    and return the report. The live DB is never touched; the copy is
    discarded afterward."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="mnemo-dryrun-"))
    tmp_db = tmp_dir / "mnemo.db"
    try:
        shutil.copy2(db_path, tmp_db)
        for suffix in ("-wal", "-shm"):
            side = db_path.with_name(db_path.name + suffix)
            if side.exists():
                shutil.copy2(side, tmp_db.with_name(tmp_db.name + suffix))
        store = Store(tmp_db)
        try:
            report = run_code_identity_migration(store, embedder=None)
        finally:
            store.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    report["dry_run"] = True
    return report
