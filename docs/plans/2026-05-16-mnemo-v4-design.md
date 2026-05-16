# mnemo v4.x — Contract-Pattern Refactor + Feature Backlog (design)

**Status:** design (validated section-by-section before the TDD execution plan)
**Date:** 2026-05-16
**Decisions locked with the user:** merge v3.2 now (done — PR #55 → main `c9a35a0`); v4.x = **contracts + features** in one line; **incremental, contract-first** (each PR shippable, no big-bang).

## Why

The system scaled and the seams show. Concretely, this session burned
multiple rounds on a `/chat` layout bug whose root cause was *the
absence of a contract*: a per-page `<style>` block, a nested `<main>`
that silently inherited a global `app.css` rule, per-page `html,body`
overrides, and a magic number (`46rem`) copy-pasted across three rules.
The user's framing is correct: **"we cannot fix file by file; we need a
contract backbone and build up from that cleanly."**

The audit found the same shape in three more places. v4.x introduces
**four explicit contracts**, each modelled on the ONE pattern already
proven in this codebase: `palette.py` / `agent_tools.TOOLS` —
*a single declarative registry, many consumers, adding a thing = one
entry*. Features the user asked for are then delivered **on** those
contracts, not bolted beside them.

---

## The four contracts

| # | Contract | Replaces (today's pain) | Proven model to copy |
|---|----------|--------------------------|----------------------|
| C1 | **Design-System / Page-Shell** | 5 inline `<style>` blocks, 3× `calc(100vh-65px)`, duplicated `.mnem-working`/`.lo-pill`, 8 button classes, hex literals, the nested-`<main>` trap | `palette.py` (single source → Jinja + JS + CSS custom props) |
| C2 | **Provider** | `get_provider()` if/elif + 5 files to add a provider + 4 scattered capability tables | `agent_tools.TOOLS` + `_register()` |
| C3 | **Chat Surface** | page vs dock divergence lives in templates; dock missing list/switch/bookmark/examples though the factory already has the logic | `mnemoChat()` factory (logic already shared; only *rendering* diverges) |
| C4 | **Settings / Config** | two settings pages, provider UI config-derived not registry-driven, not in nav, no model picker / key-tier / delete-key | C2 registry feeds the UI declaratively |

---

## C1 — Design-System / Page-Shell Contract

**Audit facts:** tokens in `app.css :root` are good for color/shape/font
but magic numbers are NOT tokenized — `65px` topbar in 3 files
(`app.css:741`, `app.css:3723`, `chat.html:273`), `1600px`, `2rem`
page-pad, `46/40/28/30rem` caps, `999px` pill (6+ sites), `#06201e`
accent-fg (5 sites), `#1a0f0c` warn-fg (2). `.mnem-working`/
`.load-older`/`.lo-pill` are duplicated (base.html vs chat.html) with
divergent sizes. `.link-button` is defined nowhere central. The
nested-`<main>` trap has no structural guard.

**Contract:**

1. **Token layer (extend `:root`)** — add the missing primitives so a
   value lives in exactly one place: `--topbar-h: 65px`,
   `--content-max: 1600px`, `--page-pad: 2rem`, `--radius-pill: 999px`,
   `--accent-fg: #06201e`, `--warn-fg: #1a0f0c`, plus the named
   reading widths (`--measure: 46rem`, etc.). Every `calc(100vh -
   65px)` becomes `calc(100vh - var(--topbar-h))`.
2. **Two page-shell modes, no third.** Documented + test-guarded:
   - *Centered*: override only `{% block content %}`; NEVER emit a
     `<main>` inside it.
   - *Full-window*: override `{% block layout %}`; emit exactly
     `<main class="full">` then one `<section class="shell-NAME">`
     whose height is `calc(100vh - var(--topbar-h))`. (`graph.html`'s
     `.nebula-shell` is the reference; `.mn` already conforms.)
3. **Shared primitives move to `app.css`** and pages MUST NOT redefine
   them: `.mnem-working`, `.load-older`/`.lo-pill`, a single
   `.link-button`, and a `.btn-pill` base that `send`/`mc-send`/
   `chat-jump`/`msg-copy`/`tool-chip` converge on.
4. **Guard test (the enforcement):** a unit test (mirrors
   `test_nebula_progressive` template-grep style) asserting NO page
   template contains `html, body {`, a bare `main {`, a second
   `<main`, a raw `calc(100vh - 65px)` literal, or a redefinition of a
   shared-primitive class. This is the contract's teeth — it makes the
   class-of-bug from this session impossible to reintroduce silently.

**Out of scope (YAGNI):** no CSS framework, no build step, no
component library. Plain CSS custom properties + Jinja, as today.

---

## C2 — Provider Contract

**Audit facts:** `BaseProvider.stream(...)` + the `('text_delta'|
'tool_call'|'usage'|'compaction'|'stop')` event tuple are clean and
stay. The problem is *registration*: `get_provider()` is a hand-edited
if/elif; adding a provider touches **5 files** (`providers/__init__.py`
elif + `DEFAULT_MODELS`, `providers/<name>.py`, `keys.py:ENV_VAR`,
`config.py:Config.providers`, `compaction.py:NATIVE_COMPACTION`).

**Contract:** one `ProviderDescriptor` dataclass + a `PROVIDERS`
registry + `register_provider(desc)`, mirroring `agent_tools.TOOLS` /
`_register()` exactly (dict + validating registrar that raises on
dupes). Descriptor fields (collapse the 4 scattered tables):

```
ProviderDescriptor(
  name, display_name, impl_class,        # replaces get_provider if/elif
  env_var | None, requires_key,          # replaces keys.py:ENV_VAR
  default_model, known_models,           # replaces DEFAULT_MODELS; feeds UI picker
  base_url | None,                       # Ollama + future local
  native_compaction_models: set[str],    # replaces compaction.py:NATIVE_COMPACTION
)
```

`get_provider`, `keys.resolve_*`, `config` defaults, and
`compaction.supports_native_compaction` all *derive from the registry*.
**Adding a provider = one `register_provider(...)` call + one
`stream()` impl class.** New endpoint `GET /v1/providers` exposes the
registry (name, display, models, requires_key, env_var) for C4's UI.
Behaviour-preserving: the four existing providers register themselves;
all existing provider tests pass unchanged.

---

## C3 — Chat Surface Contract

**Audit facts:** ALL logic is already shared in `mnemoChat()`
(`loadConversations`, `openConversation`, `newConversation`,
`groupedConversations`, `loadBookmarks`, `toggleBookmark`,
`renameConversation`-able PATCH, …). Divergence is purely in the
**templates**: the dock renders only `.mc-new` + close (no list,
switch, back, bookmark star, examples) even though the factory loads
that data. Page-only rendered features: rail, cite side-panel, bookmark
star/strip, token bar, examples, draft cards.

**Contract:** a declared **surface capability matrix**. Each surface
(`page`, `dock`) declares which capabilities it renders; shared Jinja
**partials** (`_chat_rail.html`, `_chat_bookmarks.html`,
`_chat_examples.html`, `_chat_composer.html`) are included by both,
gated by the surface's declared capabilities — so a capability is
written once and a surface opts in, instead of being re-implemented or
silently missing. The factory already guarantees the logic; the
contract guarantees the *rendering* can't drift. Brings the dock to
parity: conversation list + switch + back, bookmark star/strip,
examples — by inclusion, not duplication.

---

## C4 — Settings / Config Contract

**Audit facts (correcting the user's "not present"):** the provider/
key UI **exists** at `/settings/chat` (`chat_settings.html` Providers &
keys tab: provider dropdown, per-provider model text field, password
key input, `POST /v1/settings/providers`). Real gaps: (a) not linked
from main nav (only an inverse link from `/settings`); (b) provider
list derived from `cfg.providers` not a registry → a registered-but-
unconfigured provider is invisible; (c) model is free-text, no picker;
(d) no key-resolution-tier indicator (env/keychain/file) and no
delete-key.

**Contract:** settings panels are **registry/schema-derived**. The
provider tab reads `GET /v1/providers` (C2) → every registered provider
appears automatically with a real model picker from `known_models`.
Add `DELETE /v1/settings/providers/<name>/key`, surface the resolved
key tier (read-only, from `keys.py`). One settings information-
architecture: a single nav entry with tabs (retrieval tuning ∪
companion ∪ providers) — do NOT physically merge the v1.2 retrieval
page's route/context (regression-guarded historically, gotcha 9);
unify the *nav + shell*, keep routes.

---

## Feature backlog → mapped onto contracts

| User-reported item | Contract | Note |
|---|---|---|
| Dock can't switch/back/new-then-return | C3 | dock includes `_chat_rail` partial (logic already exists) |
| Conversation rename missing | C3 | **backend already complete** (`PATCH /v1/chat/{id}`, `ChatPatchIn.name`, `store.rename_conversation`); frontend-only: add `renameConversation()` + inline-edit affordance in the shared rail partial |
| Bookmark unverified | C3 | backend + page done; add bookmark star/strip to the shared partial → dock gets it free; add a dock surface test |
| Suggested Qs coexist; want 1-at-a-time | C3 | eagerly hide welcome on click before the async `newConversation()` resolves; drop the redundant `draft=ex` flash; dock renders `_chat_examples` |
| Send arrow not centered | C1 | CSS centering is already correct; root cause is the **SVG path's optical imbalance** (bottom-heavy stem). Fix = corrected arrow path/viewBox in the shared icon, not more CSS |
| Provider/API-key settings "not present" | C4 | exists; wire to C2 registry, add nav link, model picker, key-tier, delete-key |
| Nebula highlight does "nothing" | — | **known closed ceiling** (cosmos.gl; gotcha 31 / reference_cosmos_gl_nebula). NOT re-wired here. v4.x fix = **honesty**: the companion must NOT claim "highlighted in the graph view" when the listener is reverted — adjust the tool result / system guidance to say "highlighted in the side panel" (and the real renderer-swap is its own future chapter). |

---

## Incremental phase plan (each = one shippable minor, TDD, branch-per-minor)

Per `feedback_mnemo_release_branching` (one branch per minor, single
`chore(release)` at the tip) and `reference_mnemo_pipelines` (TDD,
phased commits, per-version handover + reindex).

- **v4.0 — C1 Design-System.** Token layer; migrate `app.css` +
  `chat.html`/`base.html`/`settings.html` off duplicated/inline CSS to
  primitives + tokens; the guard test. *Pure refactor, zero behaviour
  change, full suite green, live-verify the three full-window pages
  pixel-identical.* Ships the de-risking foundation.
- **v4.1 — C2 Provider registry.** `ProviderDescriptor`+`PROVIDERS`+
  `register_provider`; derive `get_provider`/keys/config/compaction;
  `GET /v1/providers`. *Behaviour-preserving; all provider tests
  unchanged.*
- **v4.2 — C4 Settings + provider/key UX.** Registry-driven provider
  tab, nav unification, model picker, key-tier, delete-key endpoint.
- **v4.3 — C3 Chat Surface + feature backlog.** Shared partials +
  capability matrix; dock parity (list/switch/back, bookmark,
  examples); rename frontend; 1-at-a-time welcome; send-arrow optical
  fix; nebula-highlight honesty.

Each phase: failing tests first → minimal impl → live-verify in the
preview (geometry/computed-style numbers, the user's exact scenarios —
`feedback_reproduce_user_exact_scenario`) → phased commits → per-
version handover + reindex. Nothing from the closed cosmos ceiling is
reopened (reference_cosmos_gl_nebula).

## Explicitly out of scope (YAGNI)

Node build/bundler; CSS framework; component library; Nebula renderer
swap (separate future chapter); merging the v1.2 retrieval route into
chat settings (nav-only unification); multi-user/auth; new providers
beyond making the *contract* that makes adding them trivial.

## Next step

Validate this design (section-by-section) → then the
`superpowers:writing-plans` skill turns the validated design into the
per-phase TDD execution plan (`docs/plans/2026-05-16-mnemo-v4.0-*.md`
first, contract-first).
