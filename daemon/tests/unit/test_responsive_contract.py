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

# The ONLY length literals permitted inside a width media query.
ALLOWED_BP_LITERALS = {"40rem", "60rem", "80rem"}


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
