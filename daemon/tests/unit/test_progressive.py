"""Phase 1 of the v2.2 progressive-UX rollout: client primitives.

These tests verify the contract of ``daemon/mnemo/ui/static/app.js`` --
that the four primitives (mnemoSkeleton / mnemoStaggeredReveal /
mnemoStreamFromSSE / mnemoStreamText) and the reduced-motion check are
defined on ``window``, loaded by ``base.html``, and discoverable from
every page that extends the base layout.

We don't run the JS itself (the project deliberately has no Node
toolchain). Live smoke testing of behavior happens via the preview
tool. The tests here lock the SURFACE so refactors don't accidentally
drop a primitive or change a name.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemo.server import create_app
from mnemo.store import Store
from tests.conftest import FakeEmbedder


@pytest.fixture
def client(store: Store, fake_embedder: FakeEmbedder) -> Iterator[TestClient]:
    app = create_app(store=store, embedder=fake_embedder)
    with TestClient(app) as c:
        yield c


# --- the JS module is served ---------------------------------------------


def test_app_js_is_served_under_static(client: TestClient) -> None:
    """``/static/app.js`` must return 200 with a JavaScript content type."""
    r = client.get("/static/app.js")
    assert r.status_code == 200, (
        "app.js must be served under /static -- phase 1 of progressive UX "
        "(see docs/plans/2026-05-14-ux-progressive-design.md)"
    )
    ctype = r.headers.get("content-type", "").lower()
    assert "javascript" in ctype, (
        f"app.js should be served with a JavaScript content type, got {ctype!r}"
    )


def test_app_js_exists_in_static_dir() -> None:
    """The file lives where base.html expects to find it."""
    static = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static"
    assert (static / "app.js").is_file(), f"daemon/mnemo/ui/static/app.js not found in {static}"


# --- the four primitives are exposed -------------------------------------


@pytest.fixture(scope="module")
def app_js() -> str:
    path = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static" / "app.js"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "primitive",
    [
        "mnemoSkeleton",
        "mnemoStaggeredReveal",
        "mnemoStreamFromSSE",
        "mnemoStreamText",
    ],
)
def test_app_js_defines_each_primitive(app_js: str, primitive: str) -> None:
    """Each of the four primitives must be assigned onto ``window``.

    Either ``window.<name> =`` or a destructured ``Object.assign`` would
    work; we accept any pattern that names the primitive in an assignment
    to ``window``.
    """
    pattern = rf"window\.{primitive}\s*="
    assert re.search(pattern, app_js), (
        f"window.{primitive} must be defined in app.js. Pattern searched: {pattern}"
    )


def test_app_js_honors_reduced_motion(app_js: str) -> None:
    """All four primitives must consult ``prefers-reduced-motion: reduce``.

    A single shared check at module init is the contract -- the test
    just confirms the matchMedia query exists somewhere in the file.
    """
    assert "prefers-reduced-motion" in app_js, (
        "app.js must check ``prefers-reduced-motion: reduce`` so animations "
        "collapse to 0 for accessibility. See § 5 of the design doc."
    )


def test_app_js_has_cancellation_handle(app_js: str) -> None:
    """Reveal + text-stream primitives must return ``{ cancel() }``."""
    assert re.search(r"\bcancel\b\s*[:(]", app_js), (
        "app.js must expose a ``cancel()`` method on the reveal handles "
        "so callers can abort an in-flight animation mid-fade. See § 5."
    )


# --- base.html loads app.js (every page inherits it) ---------------------


def test_base_html_loads_app_js(client: TestClient) -> None:
    """Any page extending base.html must include the <script> tag.

    We probe the dashboard (which extends base.html) and assert the
    script reference is present. We also assert the cache-bust query
    string is wired so a daemon version bump invalidates the browser
    cache for the JS the same way it does for the CSS.
    """
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    pattern = r'<script[^>]+src="/static/app\.js\?v=[^"]+"[^>]*></script>'
    assert re.search(pattern, body), (
        "base.html must load /static/app.js with a ``?v={{ mnemo_version }}`` "
        "cache-bust query, just like /static/app.css. Pattern: " + pattern
    )


# --- CSS rules for the reveal classes ------------------------------------


@pytest.fixture(scope="module")
def app_css() -> str:
    path = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static" / "app.css"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "selector",
    [".skeleton", ".reveal-item", ".fade-in"],
)
def test_app_css_defines_reveal_class(app_css: str, selector: str) -> None:
    """The three reveal classes must be defined.

    ``.skeleton`` paints the shimmer placeholder, ``.reveal-item`` is
    the stagger fade-in, ``.fade-in`` is the per-chunk Nebula fade-in.
    """
    pattern = re.escape(selector) + r"\s*[{,:]"
    assert re.search(pattern, app_css), (
        f"CSS class {selector!r} must be defined in app.css. Pattern searched: {pattern}"
    )
