# mnemo — C1.R Responsive / Adaptive Layout — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans. RIGID: superpowers:test-driven-development, superpowers:verification-before-completion. Live-verify per `feedback_reproduce_user_exact_scenario` at EXPLICIT viewports (375/768/1280 — the 0-height/0-width preview artifact, gotcha 19; numbers are ground truth, screenshots are a flaky env artifact). This is a **C1 contract extension** — same single-source + guard-test discipline.

**Goal:** Make responsiveness a C1 contract every page inherits: breakpoint tokens, adaptive mobile nav drawer, collapsible chat/dock session list, systematized fit/no-overflow primitives — so small windows utilize space, never overflow, and feel great.

**Architecture:** Extend `app.css :root` with `--bp-*` tokens (single-source, like C1 colors); migrate the 15 scattered `@media` literals to them; add `.u-truncate`/`.u-clamp` shared primitives + the generalized no-overflow rule; a `navDrawer()` Alpine + CSS off-canvas nav; a `CHAT_SURFACES`-gated rail-collapse on the shared `_chat_rail`; adaptive full-window shells. CSS owns layout; Alpine owns only stateful toggles. No new deps, no build.

**Tech Stack:** plain CSS (tokens, media/container queries, clamp, line-clamp), Alpine.js (drawer/collapse state + localStorage), Jinja2 + `CHAT_SURFACES`, pytest grep-guard tests, preview tool.

---

## Context & prerequisites

- **Branch:** the next minor, off `main` **after v4.3.2** (`git checkout main && git pull && git checkout -b release/<next>.0`). Renderer swap (`...-v4.4-nebula-renderer-swap.md`) is the minor AFTER this — do not interleave.
- **Validated design:** `docs/plans/2026-05-17-mnemo-responsive-adaptive-layout-design.md`.
- **Reading list:** `reference_mnemo_v3.md` (g19 0-vp artifact, g35 shell), `feedback_reproduce_user_exact_scenario` (277px-sliver, real-viewport verify), `feedback_mnemo_alpine_gotchas` (named factory, no dup x-data, localStorage namespacing, `alpine:initialized`), `feedback_alpine_double_init` (no `x-data`+`x-init="init()"`), `feedback_summary_flex_marker_phantom` (Chrome flex/`<summary>` trap — relevant to collapse headers), `reference_mnemo_pipelines` §4/§13, `docs/architecture.md` Page-shell contract + the v4.3.1 no-overflow rule.
- **Hard rules:** conventional commits; **no Co-Authored-By**; no emojis; HEREDOC; run from `daemon/`; git from repo root; **stop the preview daemon before any `uv run`/version bump** (mnemo.exe-lock gotcha); after a branch switch use `-p no:cacheprovider` or full `tests/unit` dir (stale-cache gotcha).
- **Ground-truth (verified):** 15 `@media` rules in `app.css` at 1100/980/1080/800/900/1000 px (`grep -n '@media' mnemo/ui/static/app.css`); `:root` ~`app.css:3-46` (C1 tokens); topbar `base.html:547` (`brand`+`<nav>`:553+`workspace-switcher`:578+bell+help, NO hamburger); shared rail `_chat_rail.html` + `.rail--dock` in app.css; `CHAT_SURFACES` `mnemo/ui/chat_surface.py`; guard-test pattern `tests/unit/test_design_system_contract.py`.

---

## Task 1: Breakpoint token layer + migrate the 15 scattered media queries

**Files:** Modify `daemon/mnemo/ui/static/app.css` (`:root` + the 15 `@media`); Test: `daemon/tests/unit/test_responsive_contract.py` (Create).

1. **Failing test:** `:root` defines `--bp-sm`/`--bp-md`/`--bp-lg`; assert no `@media (max-width:`/`(min-width:` with a raw `px` literal NOT in the documented allowed set (the 3 token-equivalent rem values + `prefers-reduced-motion`/`prefers-color-scheme`). RED.
2. Add `--bp-sm: 40rem; --bp-md: 60rem; --bp-lg: 80rem;` to `:root`; rewrite the 15 `@media` to the 3 nearest breakpoints (CSS `@media` can't use `var()` — the contract is "only these 3 documented breakpoint values; the test enforces it"; comment each with the token name). Choose mappings to preserve current desktop layout (1100→`--bp-lg` 80rem≈1280? — pick the rem value that keeps the existing behavior; document the chosen px↔rem map in the test's allowed-set + `docs/architecture.md`). GREEN.
3. `cd daemon && uv run pytest tests/unit/test_responsive_contract.py -q && uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — full suite green; **live-verify desktop pixel-parity** at 1280/1440 (the consolidation must not move desktop layout).
4. Commit `refactor(ui): C1.R -- breakpoint token layer; 15 media queries consolidated`.

## Task 2: `.u-truncate` / `.u-clamp` primitives + generalized no-overflow

**Files:** `app.css` (add primitives near the C1 shared block); the overflow-prone templates (`nodes.html`, `node.html`, `_search_results.html`, code views, `_chat_*`); Test: extend `test_responsive_contract.py`.

1. Test: `.u-truncate {` and `.u-clamp` single-sourced in app.css; not redefined in page templates; the long-text surfaces reference them; the v4.3.1 `minmax(0` rule still holds (re-assert `test_audit_grids` style across the listed grids). RED.
2. Implement `.u-truncate` (white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0) + `.u-clamp` (`-webkit-line-clamp` + standard `line-clamp`, `overflow:hidden`); apply to long descriptions/previews; ensure grid/flex ancestors carry `minmax(0,…)`/`min-width:0`. GREEN.
3. Full suite + ruff; **live-verify** a long description ellipsis/clamps (computed `scrollWidth>clientWidth` + visible ellipsis; `scrollWidth==clientWidth` at document level) at 375/768/1280 on nodes/audit/search.
4. Commit `feat(ui): C1.R -- u-truncate/u-clamp primitives + systematized no-overflow`.

## Task 3: Adaptive mobile nav drawer

**Files:** `base.html` (topbar markup + `navDrawer()` Alpine `<script>`); `app.css` (drawer CSS, `< --bp-md`); Test: extend the guard.

1. Test: `base.html` has a hamburger button (`aria-controls`/`aria-expanded`), an off-canvas `nav-drawer` container, `navDrawer()` factory (named, no `x-init="init()"` dup — `feedback_alpine_double_init`); CSS hides the toggle ≥ `--bp-md` and the inline nav < it. RED.
2. Implement: below `--bp-md`, `nav`+workspace-switcher+bell collapse into the drawer; hamburger toggles `open`; Escape closes; focus returns; `prefers-reduced-motion`; state persisted (localStorage key namespaced). Themed from C1 tokens only. GREEN.
3. Full suite + ruff; **live-verify** at 375/768: nav inline gone, hamburger opens a working drawer, Escape closes, links navigate, ≥`--bp-md` unchanged (desktop parity).
4. Commit `feat(ui): C1.R -- adaptive mobile nav drawer (CSS + minimal Alpine, a11y)`.

## Task 4: Collapsible session list (the bubble fix)

**Files:** `mnemo/ui/chat_surface.py` (add `collapse` capability), `_chat_rail.html` (collapse header/toggle, gated), `app.css` (`.rail--dock` collapsed state), `chat.js` (collapse state + localStorage); Test: extend `test_chat_surface_contract.py` + the guard.

1. Test: `CHAT_SURFACES` declares `collapse` for page+dock (page default expanded, dock default collapsed); `_chat_rail` has the gated "▸ Conversations (count)" toggle; collapsed dock rail does not exceed its bound as the list grows. RED.
2. Implement: a single shared collapse mechanic in `_chat_rail` (Alpine `railOpen`, persisted; dock starts collapsed). Reuse `.rail--dock` max-height/scroll for the expanded state. No per-surface duplication (C3 single-source). GREEN.
3. Full suite + ruff; **live-verify** in the dock on a non-chat page (real viewport): rail collapsed by default, expands to a bounded scroller, growing history never eats the bubble; page rail unchanged.
4. Commit `feat(ui): C1.R -- collapsible shared session rail (dock collapsed-by-default)`.

## Task 5: Adaptive full-window shells

**Files:** `app.css` (`.nebula-shell` / chat `.mn` `< --bp-md`); `graph.html`/`chat.html` minimal markup for the panel toggles; Test: extend the guard.

1. Test: below `--bp-md`, the 3-panel shells collapse side panels into toggleable drawers/tabs (no `< Npx` sliver); document-level `scrollWidth==clientWidth`. RED.
2. Implement: side panels (rail, cite) become a drawer/tab toggle on small; thread/graph take the full width (no 277px-sliver pathology). CSS-driven + the existing Alpine surface state. GREEN.
3. Full suite + ruff; **live-verify** /graph + /chat at 375/768: usable single-pane with toggles, no overflow, no sliver; desktop 3-panel unchanged.
4. Commit `feat(ui): C1.R -- adaptive full-window shells (panels -> drawers < --bp-md)`.

## Task 6: Polish + full live-verify matrix

Transitions, drawer scrim, touch target sizes (≥44px), focus rings — all from C1 tokens, dark-theme polished ("feel great" bar). Run the FULL §Testing matrix from the design at 375/768/1280 on every page: zero horizontal overflow, nav drawer, dock collapse, truncation/clamp, no slivers, desktop parity. Commit `feat(ui): C1.R -- responsive polish + full live-verify`.

## Task 7: Release the next minor

`reference_mnemo_pipelines` §4+§13: stop preview daemon → full suite + ruff → bump `daemon/pyproject.toml`+`daemon/mnemo/__init__.py`+`CHANGELOG.md`+`uv lock` → `chore(release): v<next>.0` → tag → restart daemon `/v1/health` check → push branch+tag → `gh pr create` → watch CI → merge → release.yml publishes → `session_handover_*` + `MEMORY.md` + final reindex (daemon kept UP, foreground). Then the v4.5 Nebula renderer swap is the following chapter.

---

## Done criteria

- [ ] `--bp-sm/md/lg` single-source; the 15 scattered media queries consolidated; guard test green; **desktop pixel-parity** (pure-refactor phase 1).
- [ ] `.u-truncate`/`.u-clamp` primitives single-sourced + applied; no-overflow rule systematized + guard-tested across surfaces.
- [ ] Mobile nav drawer (a11y) < `--bp-md`; collapsible shared session rail (dock collapsed-by-default, bounded as history grows); adaptive full-window shells (no sliver).
- [ ] Live-verified at 375/768/1280 on every page: zero horizontal overflow, content fits/clamps, space utilized, "feels great"; desktop unchanged.
- [ ] Full suite + ruff green; minor tagged, PR merged, published, handover + reindex done.
