# mnemo — C1.R Responsive / Adaptive Layout contract — design

**Status:** design (validated forks confirmed with the user, 2026-05-17)
**Decisions locked:** app-wide C1 *contract* extension, phased; CSS-first + minimal Alpine for the few stateful drawers; ships as the **next minor** (before the v4.5 Nebula renderer swap, which stays planned).
**Builds on:** C1 (token layer + page-shell contract + shared primitives + guard test) and the v4.3.1 no-overflow rule. This is C1's natural completion: *space is a design-system value, single-sourced and guard-tested* — not per-page media-query hacks.

## Why

The "utilize space / never overflow / feel great on small windows"
mindset is the C1 mindset, not yet made responsive. Concretely the
audit found the same C1-shaped pain for *space*: **15 ad-hoc `@media`
rules** in `app.css` at **six different, hand-picked breakpoints**
(1100/980/1080/800/900/1000 px) with **zero breakpoint tokens** — the
exact "magic numbers scattered, change = hunt every file" problem C1
fixed for color/spacing/radius. There is **no adaptive nav** (the
topbar `brand + nav + workspace-switcher + bell + help` simply
overflows/wraps on a narrow window — no drawer), and the chat/dock
**session list grows unbounded** (a long history eats the bubble).
Long descriptions/content fit was patched once for `/audit` (v4.3.1
`minmax(0,1fr)`) but the rule is not systematic.

## The contract (one system, many consumers — the C1 model)

**1. Breakpoint token layer (extend `app.css :root`).** A value lives
once: `--bp-sm: 40rem; --bp-md: 60rem; --bp-lg: 80rem;` (rem so they
scale with the user's font). Every responsive rule references a token
(via a documented convention) — the 15 scattered literals are migrated
to the 3 named breakpoints (consolidate, like C1's `--topbar-h`). A
guard test forbids new raw `max-width:/min-width:` pixel media-query
literals outside the documented token set.

**2. Adaptive nav (the mobile drawer).** Below `--bp-md` the topbar
collapses: brand + a hamburger button stay; `nav` + workspace-switcher
+ bell move into an off-canvas drawer toggled by one shared Alpine
component (`navDrawer()`, `aria-expanded`, Escape-to-close,
focus-return; state persisted per `feedback_mnemo_alpine_gotchas`
localStorage-namespacing). CSS does the layout; Alpine only owns the
open/close boolean. Themed 100% from C1 tokens (`--panel`, `--accent`,
`--radius`, `--shadow`, `--transition`).

**3. Collapsible session list (the bubble fix).** `_chat_rail` is
already the shared partial (C3) — add a *collapse* capability gated by
`CHAT_SURFACES` (page: expanded by default; dock: collapsed by
default — a "▸ Conversations (N)" header that expands a bounded,
scrollable list). Reuses the existing `.rail--dock` max-height/scroll;
adds the toggle + a localStorage-persisted open state. Growing history
never eats the bubble; one source, both surfaces inherit it.

**4. The fit / no-overflow rule, systematized.** Generalize v4.3.1
into C1 shared primitives: a `.u-truncate` (1-line ellipsis), a
`.u-clamp-N` (multi-line line-clamp for descriptions/previews), and
the documented "any grid/flex container with long/`nowrap` content
uses `minmax(0,…)` + `min-width:0`" rule. Apply across the
overflow-prone surfaces (nodes list, search/cite popover, code views,
node detail, audit already done). One guard test (extending
`test_design_system_contract` / `test_audit_grids`) asserts the rule
holds and the bug class can't return.

**5. Adaptive shells.** The full-window shells adapt below `--bp-md`:
the 3-panel `.nebula-shell` and the chat `.mn` (rail | thread | cite)
collapse side panels into toggleable drawers/tabs instead of
shrinking to unusable slivers (the documented v3.2 277px-sliver
pathology — `feedback_reproduce_user_exact_scenario`). Centered pages
already reflow via `main`'s `--content-max`/`--page-pad`; only the
horizontal padding tightens at `--bp-sm`.

## Architecture / blast radius

Reuses verbatim: the C1 `:root` token layer + page-shell contract +
shared-primitive single-sourcing model + the guard-test pattern; the
C3 `CHAT_SURFACES` matrix + shared partials; `palette.py`. Adds: 3
breakpoint tokens, `.u-truncate/.u-clamp`, `navDrawer()` Alpine, a
rail-collapse flag, token-migrated media queries, adaptive-shell
rules. No new dependency, no build step, no per-page CSS — every page
inherits via the shell + primitives (the contract's whole point).

## Error handling / a11y

Drawer: keyboard-operable, `aria-expanded`, Escape closes, focus
returns to the toggle, `prefers-reduced-motion` respected (existing
pattern). Collapse state corrupt/missing in localStorage → safe
default (page expanded, dock collapsed). No-JS → CSS keeps content
readable (drawer degrades to visible stacked nav; never a dead
hamburger).

## Testing

- **Responsive guard test** (mirrors `test_design_system_contract`):
  `--bp-sm/md/lg` exist in `:root`; no raw px media-query literal
  outside the token set; `.u-truncate`/`.u-clamp` single-sourced in
  app.css, not redefined per page; the nav drawer + rail-collapse
  markup/ARIA present; `CHAT_SURFACES` declares the collapse
  capability for both surfaces.
- **Live-verify (per `feedback_reproduce_user_exact_scenario`, at
  EXPLICIT viewports — the 0-height/0-width artifact, gotcha 19):** at
  375 / 768 / 1280 widths: no horizontal document overflow
  (`scrollWidth == clientWidth`) on every page; nav collapses to a
  working drawer < `--bp-md`; the dock session list is collapsed +
  expandable and never exceeds its bound as history grows; a long
  description ellipsis/clamps (computed `scrollWidth > clientWidth` +
  visible ellipsis), the layout shrinks gracefully, nothing extends.
- Full suite + ruff; centered/full-window pixel-parity at desktop
  widths (zero regression at the sizes that already worked).

## Phase plan (TDD, one branch, the next minor)

1. Breakpoint token layer + guard test; migrate the 15 `@media`
   literals to `--bp-*` (pure refactor, desktop pixel-identical).
2. `.u-truncate` + `.u-clamp` shared primitives + apply to the
   overflow-prone surfaces; generalize the no-overflow guard.
3. `navDrawer()` + the mobile nav drawer (CSS layout + minimal
   Alpine, a11y).
4. `_chat_rail` collapse capability (CHAT_SURFACES-gated; dock
   collapsed-by-default, persisted).
5. Adaptive full-window shells (.nebula-shell / .mn side-panels →
   drawers/tabs < --bp-md).
6. Polish + the full live-verify matrix (375/768/1280).
7. Release pipeline (one-branch-per-minor; bump set
   CHANGELOG+__init__+pyproject+uv.lock; tag; PR; CI; merge; publish;
   handover + reindex).

## Out of scope (YAGNI)

A separate mobile site/app; a CSS framework or build step; JS
width-observers driving layout broadly (CSS owns layout); per-page
bespoke responsive code; touch-gesture systems; reflowing the graph
*data*; the renderer swap (that is the following minor, v4.5).

## Next step

`superpowers:writing-plans` → per-phase TDD execution plan
(`docs/plans/2026-05-17-mnemo-responsive-adaptive-layout.md`),
executed off fresh `main` as the next minor.
