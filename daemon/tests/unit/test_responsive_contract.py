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


# Every list rendered as a CSS grid: an implicit `auto` track sizes to
# MAX-CONTENT, so one long nowrap child (a node .name, a query .prompt)
# blows the list -> the page width, and any ellipsis never engages.
# This is the v4.3.1 .query-log bug class; it recurred on .node-list
# and .query-mini (C1.R Task 6 -- 3rd & 4th instances). The rule is
# now systematized: EVERY grid-list declares an explicit minmax(0,...)
# track so it can shrink below max-content. New grid lists must join.
GRID_LIST_SELECTORS = (
    "\n.query-log {",
    "\n.node-list {",
    "\n.query-mini {",
    "\n.query details ul.hits {",
    "\ndl.meta {",
)


def test_all_grid_lists_constrain_long_content(app_css: str) -> None:
    for sel in GRID_LIST_SELECTORS:
        start = app_css.index(sel)
        body = app_css[start : app_css.index("}", start)]
        assert "minmax(0" in body, (
            f"{sel.strip()} is display:grid; it MUST set "
            f"grid-template-columns: minmax(0, ...) -- an implicit auto "
            f"track sizes to max-content and a long nowrap child blows "
            f"the page width (the v4.3.1 bug class, systematized C1.R)."
        )


def test_table_scroll_primitive_single_sourced(app_css: str) -> None:
    """A wide data <table> is non-responsive; it must scroll WITHIN a
    .table-scroll box (overflow-x:auto), never force a document
    scrollbar. The primitive is single-sourced in app.css and the
    sources table references it (the canonical responsive-table fix)."""
    assert app_css.count(".table-scroll {") == 1, (
        ".table-scroll must have one canonical definition in app.css."
    )
    start = app_css.index(".table-scroll {")
    body = app_css[start : app_css.index("}", start)]
    assert "overflow-x: auto" in body, (
        ".table-scroll must set overflow-x:auto (the table scrolls in its own box, not the page)."
    )
    sources = (TPL / "sources.html").read_text(encoding="utf-8")
    assert 'class="table-scroll"' in sources, (
        "sources.html must wrap its <table> in .table-scroll (a wide "
        "table otherwise forces a document scrollbar < --bp-md)."
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


@pytest.fixture(scope="module")
def base_html() -> str:
    return (TPL / "base.html").read_text(encoding="utf-8")


def test_nav_drawer_markup_and_a11y(base_html: str) -> None:
    """Below --bp-md the topbar collapses into an off-canvas drawer.
    base.html must carry the hamburger toggle (a11y: aria-controls +
    aria-expanded bound to the open state), the #nav-drawer container,
    and a scrim -- the markup the responsive CSS hangs off."""
    assert 'class="nav-toggle"' in base_html, (
        "base.html must have a .nav-toggle hamburger button (the < --bp-md "
        "affordance; topbar overflows on a narrow window without it)."
    )
    assert 'aria-controls="nav-drawer"' in base_html, (
        'the hamburger must declare aria-controls="nav-drawer" (a11y).'
    )
    assert ':aria-expanded="open"' in base_html, (
        "the hamburger must bind :aria-expanded to the drawer open state."
    )
    assert 'id="nav-drawer"' in base_html, (
        "base.html must have the #nav-drawer off-canvas container."
    )
    assert 'class="nav-scrim"' in base_html, (
        "base.html must have a .nav-scrim (click-outside-to-close, a11y)."
    )


def test_nav_drawer_factory_named_no_double_init(base_html: str) -> None:
    """navDrawer() is a named factory (feedback_mnemo_alpine_gotchas) and
    is NOT paired with x-init="init()" -- Alpine auto-runs init(); the
    pair double-fires it (feedback_alpine_double_init)."""
    assert "function navDrawer()" in base_html, (
        "navDrawer() must be a named factory (mirrors bellHistory())."
    )
    assert 'x-data="navDrawer()"' in base_html, 'the topbar must mount x-data="navDrawer()".'
    assert not re.search(r'x-data="navDrawer\(\)"\s+x-init="init\(\)"', base_html), (
        'x-data="navDrawer()" + x-init="init()" double-runs init() '
        "(feedback_alpine_double_init). Drop the redundant x-init."
    )
    assert "localStorage" in base_html, "navDrawer() must persist its open state in localStorage."
    assert "mnemo.nav" in base_html, (
        "navDrawer() must use a namespaced localStorage key (feedback_mnemo_alpine_gotchas)."
    )


def test_nav_drawer_css_desktop_parity_and_collapse(app_css: str) -> None:
    """>= --bp-md the drawer is display:contents so nav/workspace/bell/
    help stay direct topbar flex items (desktop pixel-parity, like the
    Task 1 consolidation). The toggle is hidden by default and only the
    < --bp-md (60rem) media query turns it on + makes .nav-drawer a
    fixed off-canvas panel."""
    nd_start = app_css.index(".nav-drawer {")
    nd_body = app_css[nd_start : app_css.index("}", nd_start)]
    assert "display: contents" in nd_body, (
        ".nav-drawer must be display:contents at >= --bp-md so its "
        "children stay direct topbar flex items (desktop parity)."
    )
    nt_start = app_css.index(".nav-toggle {")
    nt_body = app_css[nt_start : app_css.index("}", nt_start)]
    assert "display: none" in nt_body, (
        ".nav-toggle must be display:none by default (desktop has the "
        "inline nav; the hamburger only appears < --bp-md)."
    )
    assert ".nav-drawer.open" in app_css, (
        "the open state (.nav-drawer.open -> translateX(0)) must exist."
    )
    assert ".nav-scrim" in app_css, ".nav-scrim must be styled (the drawer backdrop)."


def test_no_template_inline_style_has_raw_px_breakpoint() -> None:
    """The breakpoint contract binds inline page <style> too, not just
    app.css. NO template may carry a width @media with a raw px literal
    outside the 3-token set (chat.html's old 1100px shell breakpoint
    and settings.html's 700px .weight-row were the last two; this guard
    makes a stray per-page breakpoint impossible to reintroduce)."""
    offenders: list[str] = []
    for tpl in sorted(TPL.glob("*.html")):
        text = tpl.read_text(encoding="utf-8")
        for prelude in re.findall(r"@media([^{]+)\{", text):
            for value in re.findall(r"(?:min|max)-width:\s*([0-9.]+(?:px|rem|em))", prelude):
                if value not in ALLOWED_BP_LITERALS:
                    offenders.append(f"{tpl.name}: @media({prelude.strip()}) -> {value}")
    assert not offenders, (
        "Inline <style> width media queries must use only the C1.R "
        f"breakpoint tokens {sorted(ALLOWED_BP_LITERALS)} (single-source, "
        "like app.css). Offending rules:\n  " + "\n  ".join(offenders)
    )


def test_full_window_shells_collapse_and_keep_panels_reachable(
    app_css: str,
) -> None:
    """< --bp-md the 3-panel shells collapse to a single usable pane
    (no 277px-sliver pathology) AND the side panels stay reachable via
    a toggle (not display:none-forever). The .nebula-shell collapse
    lives in app.css (token-gated); nebula()/mnemoChat() own a minimal
    mPanel state; the toggles are in the templates."""
    # .nebula-shell collapses to one column inside a --bp-md media
    # query (window-based, not comment-delimited -- robust to inline
    # explanatory comments inside the @media block).
    nb = app_css.index(".nebula-shell {")
    nb_media = app_css.index("@media (max-width: 60rem)", nb)
    nb_block = app_css[nb_media : nb_media + 900]
    assert ".nebula-shell {" in nb_block, (
        ".nebula-shell must have a < --bp-md collapse rule (single "
        "column -- the graph must not crush to a sliver)."
    )
    sh = nb_block.index(".nebula-shell {")
    sh_body = nb_block[sh : nb_block.index("}", sh)]
    assert "display: flex" in sh_body, (
        "the collapsed .nebula-shell must be display:flex, NOT the "
        "5-track desktop grid -- grid keeps auto-generating implicit "
        "columns from the in-grid children (canvas/filterbar/gutters), "
        "crushing the graph to a sliver. flex makes grid-column inert."
    )
    graph = (TPL / "graph.html").read_text(encoding="utf-8")
    chat = (TPL / "chat.html").read_text(encoding="utf-8")
    assert "mPanel" in graph, (
        "nebula() must own a minimal mPanel state (which side panel is "
        "open as a drawer on small screens)."
    )
    assert "mPanel" in chat, (
        "mnemoChat()/chat.html must own mPanel (mobile side-panel "
        "drawer state) so the rail/cite stay reachable < --bp-md."
    )
    assert "mpanel-toggle" in graph, (
        "graph.html must render the side-panel toggle affordance "
        "(.mpanel-toggle) so the tree/detail drawers are reachable."
    )
    assert "mpanel-toggle" in chat, (
        "chat.html must render the side-panel toggle affordance so the "
        "rail/cite drawers are reachable < --bp-md."
    )
