"""C1.R Responsive / Adaptive Layout contract guard.

Mirrors test_design_system_contract.py's grep style. These assertions
are the contract's teeth: they make the "magic breakpoint scattered,
change = hunt every file" bug class (the exact C1-shaped pain C1 fixed
for color/spacing/radius, not yet for *space*) impossible to
reintroduce silently.

Breakpoint contract (single-source, like the C1 colour tokens):

    --bp-sm: 40rem;   /* ~640px @ 16px root -- tight / mobile          */
    --bp-md: 60rem;   /* ~960px @ 16px root -- the primary collapse pt */
    --bp-lg: 80rem;   /* ~1280px @ 16px root -- wide desktop           */

CSS @media cannot use var(); the contract is therefore "only these
three documented breakpoint values appear in width media queries; the
test enforces it". The 15 ad-hoc literals (1100/980/1080/800/900/1000
px) were consolidated to the single natural cluster point --bp-md
(60rem) -- desktop pixel-parity at 1280/1440 is preserved (every
max-width rule stays inactive and every min-width rule stays active at
those widths, exactly as before). Documented px<->rem map:

    1100px max-width  -> 60rem (--bp-md)   main padding tighten
     800px max-width  -> 60rem (--bp-md)   .dash-row 2->1 col
     980px max-width  -> 60rem (--bp-md)   .dash-row.split-2 / node grid
    1080px max-width  -> 60rem (--bp-md)   .dash-row.split-3
     900px min-width  -> 60rem (--bp-md)   .code-columns / .ego-network
    1000px min-width  -> 60rem (--bp-md)   .project-columns

--bp-sm / --bp-lg are defined now (single-source) and consumed by the
later responsive phases (mobile nav drawer < --bp-md, tighter sm
padding, adaptive shells).
"""

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "static" / "app.css"
TPL = Path(__file__).resolve().parents[2] / "mnemo" / "ui" / "templates"

# The ONLY length literals permitted inside a width media query.
ALLOWED_BP_LITERALS = {"40rem", "60rem", "80rem"}

# Templates that must never redefine a shared primitive (C1 single-source).
PAGE_TEMPLATES = [
    "nodes.html",
    "node.html",
    "audit.html",
    "_search_results.html",
    "chat.html",
    "base.html",
    "dashboard.html",
]


@pytest.fixture(scope="module")
def app_css() -> str:
    return CSS.read_text(encoding="utf-8")


def _root_block(css: str) -> str:
    start = css.index(":root")
    return css[start : css.index("}", start)]


def test_root_defines_breakpoint_tokens(app_css: str) -> None:
    """The breakpoint scale lives once, in :root, like the C1 colours."""
    root = _root_block(app_css)
    for token, value in (
        ("--bp-sm:", "40rem"),
        ("--bp-md:", "60rem"),
        ("--bp-lg:", "80rem"),
    ):
        assert token in root, (
            f"{token} must be a :root token (C1.R breakpoint layer). A "
            f"breakpoint lives in exactly one place."
        )
        decl = root[root.index(token) : root.index(";", root.index(token))]
        assert value in decl, (
            f"{token} must be {value} (the documented px<->rem map). Got: {decl.strip()!r}."
        )


def test_no_raw_px_breakpoint_literals_outside_token_set(app_css: str) -> None:
    """No width media query may use a raw px literal or any rem value
    outside the documented 3-token set. Scoped to @media preludes so a
    CSS-property min-width:0 (v4.3.1 no-overflow rule) is never flagged."""
    preludes = re.findall(r"@media([^{]+)\{", app_css)
    offenders: list[str] = []
    for prelude in preludes:
        for value in re.findall(r"(?:min|max)-width:\s*([0-9.]+(?:px|rem|em))", prelude):
            if value not in ALLOWED_BP_LITERALS:
                offenders.append(f"@media({prelude.strip()}) uses {value}")
    assert not offenders, (
        "Width media queries must use only the documented breakpoint "
        f"tokens {sorted(ALLOWED_BP_LITERALS)} (CSS @media can't take "
        "var(); the rem literal IS the contract). Offending rules:\n  " + "\n  ".join(offenders)
    )


def test_truncation_primitives_single_sourced(app_css: str) -> None:
    """.u-truncate (1-line ellipsis) and .u-clamp (multi-line line-clamp)
    are C1 shared primitives: defined exactly once in app.css, never
    redefined in a page template (the C1 single-source model -- the same
    discipline as the colour tokens and .link-button/.btn-pill)."""
    assert app_css.count(".u-truncate {") == 1, (
        ".u-truncate must have exactly one canonical definition in "
        "app.css (the systematized v4.3.1 1-line-ellipsis primitive)."
    )
    assert app_css.count(".u-clamp {") == 1, (
        ".u-clamp must have exactly one canonical definition in app.css "
        "(the multi-line line-clamp primitive for descriptions/previews)."
    )
    for name in PAGE_TEMPLATES:
        html = (TPL / name).read_text(encoding="utf-8")
        for sel in (".u-truncate {", ".u-clamp {"):
            assert sel not in html, (
                f"{name} must NOT redefine {sel}; it is a shared app.css "
                f"primitive (C1 single-source)."
            )


def test_truncation_primitives_can_shrink(app_css: str) -> None:
    """The systematized no-overflow rule (v4.3.1, generalized): a
    truncation primitive that cannot itself shrink below its content is
    inert -- overflow:hidden;text-overflow:ellipsis does nothing without
    a width-constrained box. Both primitives MUST carry min-width:0 and
    overflow:hidden, and the v4.3.1 .query-log minmax(0,...) guard must
    still hold (no regression of the audit-overflow fix)."""
    for sel in (".u-truncate {", ".u-clamp {"):
        start = app_css.index(sel)
        body = app_css[start : app_css.index("}", start)]
        assert "min-width: 0" in body or "min-width:0" in body, (
            f"{sel} must set min-width:0 -- the v4.3.1 lesson generalized "
            f"(an un-shrinkable box makes the ellipsis/clamp inert)."
        )
        assert "overflow: hidden" in body or "overflow:hidden" in body, (
            f"{sel} must set overflow:hidden (clip the overflowing text)."
        )
    qlog_start = app_css.index("\n.query-log {")
    qlog = app_css[qlog_start : app_css.index("}", qlog_start)]
    assert "minmax(0" in qlog, (
        ".query-log must still use minmax(0,...) -- the v4.3.1 audit-"
        "overflow fix must not regress while systematizing the rule."
    )


def test_overflow_prone_surfaces_reference_the_primitives() -> None:
    """The long-text surfaces (node list, search/cite popover, audit
    row) reference the shared primitives instead of carrying their own
    bespoke ellipsis declarations -- so the no-overflow behaviour is
    single-sourced and the bug class can't return per-surface."""
    nodes = (TPL / "nodes.html").read_text(encoding="utf-8")
    assert 'class="desc u-clamp"' in nodes, (
        "nodes.html node-list description must use the .u-clamp primitive "
        "(a long description must clamp, not blow the list item)."
    )
    search = (TPL / "_search_results.html").read_text(encoding="utf-8")
    assert 'class="desc u-clamp"' in search, (
        "_search_results.html hit description must use .u-clamp (the "
        "cite/search popover is space-constrained)."
    )
    audit = (TPL / "audit.html").read_text(encoding="utf-8")
    assert "hit-desc u-truncate" in audit, (
        "audit.html .hit-desc must reference the .u-truncate primitive "
        "(the v4.3.1 1-line ellipsis, now single-sourced not inline)."
    )
