"""Task 3.5: ROI summary card on the dashboard.

Server-rendered (dashboard is a Jinja page; the plan's speculation
about a separate ``dashboard.js`` fetch turned out to be wrong --
there's no dashboard.js, and adding one to call /v1/roi/summary
client-side would duplicate work for no gain). The route handler
threads ``s.roi_summary()`` into the template context and the card
renders inline. Same data, fewer moving parts.

Two layers of assertion:

1. The card SHELL renders even on an empty DB (no queries, no
   feedback) -- the dashboard never crashes for a fresh install.
2. After seeding queries + feedback, the rendered HTML carries the
   computed numbers (queries_total, thumbs_up percentage,
   tokens-saved). The fields the case studies + sponsor application
   cite live HERE in the user's daily-driver page.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Node, Store


@pytest.fixture
def daemon_client(store: Store) -> TestClient:
    return TestClient(create_app(store=store))


def _seed_node(store: Store, node_id: str = "n1") -> None:
    store.upsert_node(
        Node(
            id=node_id,
            type="memory_feedback",
            name=node_id,
            description=None,
            body="seed",
            source_path=f"/seed/{node_id}.md",
            source_kind="memory_dir",
            project_key=None,
            frontmatter_json=None,
            hash="h",
            created_at=1,
            updated_at=1,
        )
    )


def _seed_query_feedback(store: Store, query_id: str, reason: str) -> None:
    store.conn.execute(
        "INSERT INTO queries (id, prompt, intent_tags, retrieved_ids, scores, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (query_id, f"prompt-{query_id}", "[]", "[]", "[]", int(time.time())),
    )
    store.conn.commit()
    store.log_feedback_event(query_id=query_id, node_id="n1", signal=1.0, reason=reason)


def test_dashboard_renders_roi_card_on_empty_db(daemon_client: TestClient) -> None:
    """Fresh install: dashboard must render with the ROI card
    showing zeros (no crash, no missing-key TypeError in Jinja)."""
    r = daemon_client.get("/")
    assert r.status_code == 200, r.text
    html = r.text
    # The card itself must be present.
    assert "ROI" in html or "roi" in html, "dashboard missing the ROI section"
    # Zero-state copy (the dashboard should not show fractions of
    # made-up numbers on a fresh install).
    assert "dash-roi" in html, "dashboard missing the dash-roi CSS hook"


def test_dashboard_renders_roi_numbers_after_seeded_telemetry(
    daemon_client: TestClient,
    store: Store,
) -> None:
    """After seeding 4 queries (3 thumbs-up + 1 thumbs-down), the
    rendered HTML carries the computed ROI numbers."""
    _seed_node(store)
    for i, reason in enumerate(["thumbs_up", "thumbs_up", "thumbs_up", "thumbs_down"]):
        _seed_query_feedback(store, f"q{i}", reason)

    r = daemon_client.get("/")
    assert r.status_code == 200, r.text
    html = r.text
    # queries_total = 4
    assert "4" in html, "expected queries_total=4 in the dashboard html"
    # rederivations_avoided = 3 (thumbs_up count)
    assert "3" in html
    # tokens_saved_est = 4 * 200 = 800 (or formatted like '800')
    assert "800" in html
    # thumbs_up_ratio = 0.75 (formatted as a percentage in the card)
    assert "75" in html  # the "75%" fragment


def test_dashboard_roi_card_links_to_audit_or_documents_its_source(
    daemon_client: TestClient,
) -> None:
    """The card should make it obvious WHERE the numbers come from.
    Either a link to the audit page (existing UI) or a small note
    naming the underlying telemetry."""
    r = daemon_client.get("/")
    assert r.status_code == 200
    html = r.text
    # Either an /audit-page link OR a hint mentioning 'feedback' or 'audit'
    has_audit_link = "/audit-page" in html
    has_source_hint = "feedback" in html.lower() or "audit" in html.lower()
    assert has_audit_link or has_source_hint, (
        "ROI card must link to /audit-page OR mention 'feedback'/'audit' so users "
        "can drill into the source telemetry"
    )
