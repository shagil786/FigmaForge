# Backend Implementations — React+Tailwind / Vue / Svelte / SwiftUI / Flutter (Part 14) Implementation Plan

> **For agentic workers:** This plan is written for `superpowers:subagent-driven-development`.
> Execute it task-by-task with a fresh subagent per task; each task is a self-contained
> TDD cycle (write failing test → run and expect FAIL → minimal implementation → run and
> expect PASS → commit). Never batch tasks, never skip the failing-test step, and verify
> the exact expected output shown in each step before committing. Python commands run from
> `plugin/figmaforge` unless stated otherwise. `pytest` is NOT installed — always use
> `python3 -m unittest discover -s tests` (full suite) or `python3 -m unittest tests.test_X -v`
> (targeted). **This machine's default `python3` is 3.9.6 and cannot run this codebase** —
> run every Python command with `PYTHON_BIN=/opt/homebrew/bin/python3.14`.

**Goal:** Replace the five placeholder backend generators (react_tailwind, vue, svelte,
swiftui, flutter) with real LayoutPlan → target-code lowering, while keeping the protocol
contract, capability declarations, and the html_css reference backend byte-for-byte
unchanged in behavior. Coverage targets the common IR surface (flex layout, sizing,
padding, solid fills, borders, radius, opacity, typography, text, tokens, per-screen
components); everything beyond is an explicit `FidelityLoss` with a named fallback — never
silent.

**Architecture:** One shared web style-mapping implementation, five web entry points.
`backends/web_common.py` absorbs the VNode/VStyle/CSS-style machinery currently private to
`html_css` (verbatim, public names); html_css re-imports it (transparent refactor).
React+Tailwind converts the shared CSS-style output into deterministic Tailwind
arbitrary-value classes; Vue/Svelte emit scoped-CSS SFCs/components reusing the CSS rules
directly. SwiftUI and Flutter are self-contained lowerings (modifier chains / widget tree)
with no web machinery. Fidelity: `preflight` stays the single loss source; every generator
fallback is either named in `FidelityLoss.fallback_applied` or marked inline in the
generated file (`// fidelity:`, `{/* fidelity: */}`, `<!-- fidelity: -->`).

**Tech Stack:** Python 3 stdlib only (NO new dependencies), `unittest`, the repo's
snapshot convention (`REWRITE_SNAPSHOTS=1`), git/gh for the branch → PR workflow (final
task creates the PR; it is NOT merged).

**Approved spec:** `docs/superpowers/specs/2026-08-15-backend-implementations-design.md`

## Contract facts (verified against source at plan-writing time)

- Branch: `feat/part-14-backend-implementations` (already checked out; contains the
  approved spec, committed `d5c4e9f`).
- Baseline suite state: Python `Ran 395 tests ... OK` with ZERO skips (via
  `PYTHON_BIN=/opt/homebrew/bin/python3.14`); TS untouched by this part (`npx tsc` clean,
  `117 passing`) — TS is not re-run per task; it is re-verified only in Task 8.
- `backends/html_css/__init__.py` (517 lines) contains, in order: `VStyle`, `VNode`
  (both with `to_dict`), `_SEMANTIC_TAG_BY_NAME`, `_CSSStyleGenerator.generate_style`
  (LayoutNodePlan → VStyle camelCase CSS props incl. display/sizing/padding/gap/justify/
  align/grid), `_VNodeBuilder(resolution)` (LayoutNodePlan → VNode tree with semantic tags,
  props, text), `_HtmlEmitter` (`emit(root)` → `(html, css_rules)`), `_render_attrs`,
  `_camel_to_kebab`, `_escape_html`, `_escape_attr`, `_HTML_CSS_SUPPORTED/_PARTIAL`,
  `HtmlCssBackend` (`generate`, `_apply_styles`, `_wrap_html_document`).
- `LayoutNodePlan` (core/layout_types.py:413): `node_id`, `name`, `kind`, `display`,
  `direction`, `box`, `sizing`, `spacing` (`padding` top/right/bottom/left, `gap`),
  `alignment` (`justify`, `align`), `text`, `overflow`, `breakpoints`, `children`;
  `screen.walk()` yields all descendants.
- `backends/protocol.py`: `preflight()` (default, walks IR nodes vs capabilities →
  `List[FidelityLoss]`), `FidelityLoss(feature, node_id, message, severity, fallback_applied)`.
- Stub state (verified): each stub `generate()` emits a placeholder containing
  `TODO: Generate from LayoutPlan for node {plan.node_id}`; capabilities are honest
  (e.g. react_tailwind declares ABSOLUTE_POSITIONING unsupported; swiftui/flutter declare
  several unsupported). `tests/test_backends.py` `TestStubBackends` asserts ONLY
  name/capabilities/file extensions — generated content is unasserted, so real generators
  keep it green.
- Deterministic fixture pipeline (from `tests/test_generator_snapshot.py`): 
  `FixtureLoader(plugin_root / "fixtures" / "figma")` → `loader.load("layout_desktop")` →
  `FigmaFile.from_dict("lay1440", ...)` → `IRBuilder().build(...)` →
  `LayoutAnalyzer().analyze(doc, library=LibraryLoader().load())` → a multi-node LayoutPlan
  with design tokens and breakpoints. Use this for backend fixture building; add a small
  programmatic IR fixture for unsupported-feature cases (gradient fill, absolute position,
  grid) built from `core.ir_types`/`core.layout_types` per the `_make_plan` convention.
- Snapshot dir: `tests/snapshots/` (existing: `file.json`, `generator/`, `layout-plan.json`,
  `resolution-report.json`). New backend snapshots go under `tests/snapshots/backends/`.
- No compilers are invoked anywhere; verification is structural + golden snapshots.

## Task 1: `backends/web_common.py` — extract shared web machinery (refactor)

**No new behavior.** Moves code verbatim from `html_css/__init__.py`; the existing suite is
the red/green signal (any accidental behavior change breaks html_css tests or snapshots).

Steps:

1. Create `backends/web_common.py` containing, moved verbatim from html_css (renamed to
   public names): `VStyle`, `VNode` (same `to_dict`), `SEMANTIC_TAG_BY_NAME` (+ a small
   `semantic_tag(name) -> str` lookup), `CssStyleGenerator` (was `_CSSStyleGenerator`),
   `VNodeBuilder` (was `_VNodeBuilder`, takes optional `ResolutionReport`), `camel_to_kebab`
   (was `_camel_to_kebab`), `escape_html` (was `_escape_html`), `escape_attr` (was
   `_escape_attr`). Imports: `from ..protocol import ...` (no circular import — web_common
   imports protocol, backends import web_common).
2. Rewrite `html_css/__init__.py` to import these from `..web_common` and keep only:
   `_HtmlEmitter`, `_render_attrs`, `_wrap_html_document`, `_HTML_CSS_SUPPORTED/_PARTIAL`,
   `HtmlCssBackend`. Zero behavior change — do NOT touch `HtmlCssBackend.generate` or the
   emitter logic.
3. Add `tests/test_backends.py` two tests:
   - `test_web_common_shared_machinery` — `web_common` exposes `VNode`, `VStyle`,
     `CssStyleGenerator`, `VNodeBuilder`, `escape_html`, `camel_to_kebab`; a trivial
     `CssStyleGenerator().generate_style` on a one-node LayoutNodePlan yields the expected
     `display: flex` base style (guards the shared module in isolation).
   - `test_html_css_emit_smoke` — `HtmlCssBackend().generate` on the fixture pipeline plan
     (below) returns the same file set and includes the screen's node_ids (guards the
     refactored html_css end-to-end).
4. Run targeted (`tests.test_backends`) → green; run the FULL suite →
   **expect `Ran 39X tests ... OK`, ZERO skips** (395 + 2). Any failure = refactor broke
   html_css — fix by restoring exact code, never by changing html_css output.
5. Commit: `git add backends/web_common.py backends/html_css/__init__.py tests/test_backends.py && git commit -m "refactor(backends): extract shared web machinery into backends/web_common.py"`.

## Task 2: react_tailwind — real TSX generator (TDD)

**Test file:** `tests/test_react_tailwind_backend.py` (new). Fixture builders are
self-contained in the file (repo convention): `_web_plan()` via the fixture pipeline
(layout_desktop → IR → analyzer) and `_unsupported_plan()` programmatic (a node with a
gradient fill + an absolute-positioned node).

Tests:

1. `test_generate_files_and_node_coverage` — one `.tsx` per screen, `node_ids` covers the
   screen's nodes; `tailwind.config.figmaforge.js` present.
2. `test_no_placeholder_markers` — generated content contains no
   `TODO: Generate from LayoutPlan`.
3. `test_structural_markers` — TSX contains `export function` (or `export const`) and
   `className`.
4. `test_style_lowering_exact` — a colored button node's classes include the exact
   arbitrary-value classes for its fill/radius/size (e.g. `bg-[#3366cc]`, `rounded-[8px]`,
   `w-[120px]`), flex container has `flex` + `flex-col` + `gap-[Npx]` + `items-center`.
5. `test_typography_lowered` — text node classes include `text-[Npx]` and a mapped weight
   class (`font-semibold`).
6. `test_breakpoints_mapped` — a plan with breakpoint changes emits
   `max-[{width}px]:`-prefixed classes (arbitrary Tailwind variant).
7. `test_unsupported_features_losses_and_degrade` — `_unsupported_plan()` → `preflight`
   losses present with `node_id`; `generate()` returns files without crashing and the
   absolute-positioned node is either skipped with an inline `{/* fidelity: ... */}` marker
   or named in `fallback_applied`.
8. `test_tokens_extracted` — `tailwind.config.figmaforge.js` contains real token values
   from the resolution/library (e.g. `primary: "#..."`) — no empty `TODO: Extract` blocks.
9. `test_deterministic` — two `generate()` calls → identical file contents.
10. `test_golden_snapshot` — write `tests/snapshots/backends/react_tailwind.tsx` (and the
    tailwind config) via the snapshot helper; assert matches checked-in (REWRITE_SNAPSHOTS=1
    to regenerate intentionally).
11. `test_capabilities_unchanged` — `ReactTailwindBackend().capabilities` unchanged from
    today (stub-test compatibility).

Steps: write tests → run targeted → **expect FAIL** (placeholder output fails markers/
classes/losses) → implement the real generator (VNodeBuilder + CssStyleGenerator from
web_common; `_css_to_tailwind_class(vstyle) -> str` mapper with arbitrary-value strategy;
config token extraction; fidelity markers) → run targeted → **expect PASS** → full suite →
**expect `Ran 39X tests ... OK`** → commit
`feat(backends): real React+Tailwind generator (Part 14)`.

## Task 3: vue — real `.vue` SFC generator (TDD)

**Test file:** `tests/test_vue_backend.py` (new; same fixture builders).

Tests: `test_generate_files_and_node_coverage` (one `.vue` per screen, node_ids covered);
`test_no_placeholder_markers`; `test_structural_markers` (`<template>`, `<script setup>`,
`<style scoped>`); `test_style_lowering` (scoped CSS rules contain `display: flex`,
`padding-top: 24px`, `background: #3366cc`-style values for the button node — reuse the
shared CssStyleGenerator output); `test_typography_lowered`; `test_breakpoints_mapped`
(`@media (max-width: …)` inside `<style scoped>`); `test_unsupported_features_losses`
(preflight losses for the unsupported fixture; template degrades without crashing);
`test_deterministic`; `test_golden_snapshot`
(`tests/snapshots/backends/Screen0.vue`); `test_capabilities_unchanged`.

Steps: tests → **expect FAIL** (placeholder) → implement (VNode tree → `<template>` divs
with `class="n-{id}"`, `<script setup>` with `defineProps`, `<style scoped>` from
CssStyleGenerator base + breakpoint rules; escape all dynamic text/attrs) → PASS → full
suite → commit `feat(backends): real Vue SFC generator (Part 14)`.

## Task 4: svelte — real `.svelte` component generator (TDD)

**Test file:** `tests/test_svelte_backend.py` (new).

Tests: file set + node coverage; no placeholders; structural markers (`<script>`,
`<style>`, `class="n-{id}"` markup); style lowering (scoped CSS rules present);
typography; breakpoints (`@media` in `<style>`); unsupported losses (grid/absolute →
losses; markup degrades); deterministic; golden snapshot
(`tests/snapshots/backends/Screen0.svelte`); capabilities unchanged.

Steps: tests → **expect FAIL** → implement (Svelte markup + `<script>` props + `<style>`
from shared CSS output) → PASS → full suite → commit
`feat(backends): real Svelte component generator (Part 14)`.

## Task 5: swiftui — real `.swift` view generator (TDD)

**Test file:** `tests/test_swiftui_backend.py` (new).

Tests: `test_generate_files_and_node_coverage` (one `.swift` per screen, node_ids);
`test_no_placeholder_markers`; `test_structural_markers` (`struct …View: View`, `#Preview`,
`var body: some View`); `test_container_lowering` (flex column → `VStack(spacing: N)` with
alignment; row → `HStack`); `test_style_modifiers` (`.frame(width: height:)`,
`.padding(.top, N)` / `.padding(N)` when uniform, `.background(Color(red: green: blue:))`,
`.cornerRadius(N)`, `.opacity(N)`, `.foregroundColor(...)`); `test_typography_modifiers`
(`.font(.system(size: 14, weight: .semibold))`, `.multilineTextAlignment`,
`.lineSpacing`/`.kerning`); `test_unsupported_features_losses` (gradient fill + absolute +
grid → `preflight` losses, generated view compiles-shaped: no crash, `// fidelity:`
marker for degraded nodes); `test_deterministic`; `test_golden_snapshot`
(`tests/snapshots/backends/Screen0View.swift`); `test_capabilities_unchanged`.

Steps: tests → **expect FAIL** (placeholder has no modifiers) → implement (recursive
`LayoutNodePlan` → Swift code with `_to_pascal_case` reuse; hex→`Color(red:0.20,green:0.40,
blue:0.80)`; escaping for string literals) → PASS → full suite → commit
`feat(backends): real SwiftUI view generator (Part 14)`.

## Task 6: flutter — real `.dart` widget generator (TDD)

**Test file:** `tests/test_flutter_backend.py` (new).

Tests: file set + node coverage; no placeholders; structural markers (`class …Screen
extends StatelessWidget`, `Scaffold`, `build(BuildContext context)`); `test_container_lowering`
(column → `Column` with `mainAxisAlignment`/`crossAxisAlignment` + `SizedBox(width: N)`
separators; row → `Row`); `test_style_widgets` (`Container` with `BoxDecoration(
color: Color(0xFF....), borderRadius: BorderRadius.circular(N), border: Border.all(...))`,
`EdgeInsets.only(...)`, `SizedBox(width:height:)`); `test_typography` (`TextStyle(
fontSize: 14, fontWeight: FontWeight.w600, color: Color(...), height: …))`; alignment →
`Align`); `test_unsupported_features_losses` (losses + `// fidelity:` degrade); 
`test_deterministic`; `test_golden_snapshot` (`tests/snapshots/backends/Screen0.dart`);
`test_capabilities_unchanged`.

Steps: tests → **expect FAIL** → implement (recursive lowering; `_to_snake_case` reuse for
file/widget names where apt) → PASS → full suite → commit
`feat(backends): real Flutter widget generator (Part 14)`.

## Task 7: Docs

1. `docs/DEVELOPMENT_LOG.md`: append the Part 14 entry (FILL markers for final counts):
   web_common extraction, five real generators, fidelity rule, 5 golden snapshots, test
   counts.
2. `README.md`: Part 10 checklist line "react_tailwind/vue/svelte/swiftui/flutter stubs" →
   implemented; status header Parts 1–14 + new counts; Next Steps drop the stub item
   (replacing it with the actual remaining deferred work — real-Figma demo, real-repo
   testing, rollback docs, diff heatmap / extended PNG formats).
3. `CLAUDE.md`: module line (`backends/…` — "5 stubs" → implemented) + test counts.
4. `docs/architecture.md`: implementation-status paragraph — five backends implemented.
5. Each stub module's docstring: drop "placeholder/stub" language; describe the real
   lowering.
6. Commit: `docs: document Part 14 backend implementations`.

## Task 8: Final verification gate + PR (do NOT merge)

1. Python full suite with `PYTHON_BIN=/opt/homebrew/bin/python3.14 python3.14 -m unittest
   discover -s tests` → **expect `Ran N tests ... OK`, ZERO skips** (N = 395 + 2 + ~51).
2. TS unchanged: `npx tsc` clean, `PYTHON_BIN=/opt/homebrew/bin/python3.14 node
   dist/runtime/tests/run_all.js` → **expect `117 passing, 0 failing`**.
3. `claude plugin validate --strict plugin/figmaforge` → **expect ✔ Validation passed**.
4. Fill the DEVELOPMENT_LOG Part 14 counts with the actual N; amend or follow-up commit.
5. Cross-backend smoke: run all six backends on the fixture pipeline plan via a short
   script — each returns files, no crashes, and the five new ones carry no placeholder
   markers; print the loss counts for the unsupported fixture.
6. `git status --short` → empty. Push (`git push -u origin feat/part-14-backend-implementations`)
   and create the PR (`gh pr create --base main --title "feat: Part 14 backend
   implementations (react+tailwind, vue, svelte, swiftui, flutter)" --body "..."`).
   Do NOT merge — that is the user's decision, per repo convention.
