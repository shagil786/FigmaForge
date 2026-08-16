# Web-Backend Repair Regeneration through the Bundler Harness (Part 22) — Design Spec

> **Branch:** `feat/part-22-web-backend-repair` (from main @ `a618e15`; Parts 1–21 complete, gate green at **605 Python / 162 TS**).
> **Gate baseline:** Python **605 OK, zero skips** (51 files) · TS `npx tsc` clean, **162 passing** · `claude plugin validate --strict` ✔.

## Problem

Part 21 made react/vue/svelte **measurable** — `figmaforge run --target=react+tailwind` renders through the real Vite harness and produces a measured `Score` + `Verification:` — but **not repairable**. The repair stage's real path spawns `pipeline.py repair`, which hardcodes html_css regeneration:

> `scripts/pipeline.py` `_run_repair`: `if args.backend != "html_css": raise _CliError(2, "repair regeneration supports 'html_css' only …")` — and the regeneration block constructs `HtmlCssBackend()` directly.

So an external-baseline run against a **bundler-backed target** with a failing gate does the *wrong thing*: `invokeRepair` never passes `--backend` (the CLI default is html_css), so repair regenerates **html_css** into `<run>/repair/generated/html_css/` — not the target's react/vue/svelte output — and the verify stage's post-repair path then re-renders those `.html` files (`invokeRender` per file) against the same baseline. The honest `Repairs:` count is real, but the regenerated code is the wrong backend and verify measures html_css, not the run's target. For a `--target=react+tailwind --baseline <red>` run this is a silent back-end mix-up inside an otherwise honest loop.

The docs advertise the gap explicitly (Part 21 spec + DEVELOPMENT_LOG non-goals): *"Web-backend repair regeneration (the repair loop stays html_css-scoped; the bundler targets measure but don't auto-repair yet)"*. Part 22 closes it: the repair loop's regeneration reaches react/vue/svelte, the regenerated output re-bundles through the Part-21 harness, and verify re-measures it — so bundler-rendered targets get the same auto-repair → re-verify loop html_css already has.

## Verified findings (not assumed)

1. **The regeneration is hardcoded.** `pipeline.py repair` (Part 20) — the `if args.backend != "html_css"` guard (line ~851) and the direct `HtmlCssBackend().generate(...)` call (line ~933) with `options={"styles_override": repaired_styles}`. `invokeRepair` (TS) builds args `["repair", "--ir", …, "--baseline", …, "--out", …, "--viewport", …]` — **no `--backend`**, so the CLI default html_css wins for every target.
2. **The override contract is backend-agnostic already.** The repaired styles serialize as `{node_id: {base, breakpoints}}` (`styles_to_dict`, web_common) and html_css applies them as a **union on top** of the computed style, after `extend_ir_style` (`_apply_styles`, Part 20). The same union seam is the whole mechanism — it carries the loop's `background` color patches (Part 20: the planner emits `background`, the property html_css uses for fills).
3. **vue/svelte share one style path — the seam drops in cleanly.** Both use `ScopedCssGenerator._node` (web_common): `generate_style(plan_node)` → `_extend(...)` (IR fills/radius/opacity/typography) → selector emission (plus `@media` from `style.breakpoints`). The identical override union applies after `_extend`, before the absolute-position pop (which only removes display/position/left/right/top/bottom — never `background`). Component-fallback divs carry the same scoped class, so repaired styles flow through fallbacks automatically.
4. **react has no `background` mapping — it needs one small addition.** `_classes_for` computes classes from `generate_style(plan_node)` via `_css_class` (no `background` prop mapping today) plus IR fills via `_ir_style_classes` (`bg-[#hex]` for solid fills). The repair loop only patches `background` today, so react needs: override `background` → `bg-[<color>]`, with override **precedence** over the IR fill class for that node. Any other repaired base/breakpoint props react already maps (`gap-*`, `pt-*`, `w-*`, `max-[..]:…`) flow through the same `_css_class`/`_breakpoint_class` functions.
5. **The loop mutates the plan AND the styles.** `patch_executor` holds `self._plan` and looks up `self._plan.node(patch.target_key)` (line ~238) while the styles dict receives the color patches. So layout repairs flow to **all** backends automatically via the mutated `LayoutPlan`; only the styles-dict `background` patch needs the per-backend override seam.
6. **The verify post-repair path is `.html`-shaped.** `createVerifyStageHandler` re-renders `generated.files` via `invokeRender(cfg, htmlPath, viewport, verifyDir)` — for a web backend the regenerated dir holds `.tsx`/`.vue`/`.svelte` + config, which `invokeRender` cannot render (it screenshots a standalone HTML file). It must branch: html_css → existing per-file `invokeRender`; bundler backend → `invokeBundleRender` on the regenerated dir (which already takes the generated dir + asset manifest + viewport and returns per-component `{file, html, screenshot}` rows — mapping directly to the comparator's per-screen loop).
7. **Assets must thread through regeneration.** `invokeBundleRender` needs the asset manifest — shared `assetManifest` is available in the verify stage. But if repair's `generate()` doesn't receive `--assets`, image fills degrade to the honest marked fallback in the **regenerated** files, so the re-bundled render would lose image backgrounds (a fidelity regression vs the pre-repair output). `pipeline.py repair` gains an optional `--assets <manifest>` threaded into `options["assets"]` — same contract as `generate --assets` (Part 18).
8. **The short-circuits are already backend-agnostic.** Repair short-circuits (no score / gate satisfied / reference baseline / `--no-repair`) never touch the backend; native targets never reach the spawn because their render stage degrades (no baseline/score). Only the real-spawn path needs the backend threaded.

## Design review findings (critical pass before implementation — all code-verified)

The following were found by stress-testing the design against the real code. **F1–F3 are real fidelity gaps that would have shipped silently; the rest are ordering/robustness fixes.**

- **F1 — Resolution is dropped in repair regeneration (would regress web output).** `_run_repair` loads only `--ir`/`--layout`. Regenerating react/vue/svelte without a `ResolutionReport` makes `VNodeBuilder(resolution=None)` — component/instance nodes would lose their resolved names, the Part-21 fallback machinery becomes moot, and the `component_instance approximated` markers vanish: the regenerated output would *differ from the original run's output* on any fixture with components/instances. **Fix:** `pipeline.py repair` gains `--resolution <json>` (exactly like `generate --resolution`, Part 16), threaded into `generate(resolution=...)`; the TS repair handler stages the shared `resolutionJson` (verified: `createResolveStageHandler` sets it — `backend_codegen.ts` line 485). html_css is unaffected (it never used resolution).
- **F2 — The react breakpoint-override wording was wrong; the seam design is simpler.** `_breakpoint_class` takes a plan `BreakpointChange` object, not the override's `{width: {prop: value}}` dict. The cleaner + correct react design: **apply the override into the computed style first** (`style.base.update(override["base"])` — the exact html_css union semantics), *then* run the existing class loops. Every layout prop react already maps (`gap-*`, `pt-*`, `w-*`, …) then flows automatically; only three props need new mappings: `background` → `bg-[<value>]`, `color` → `text-[<value>]`, `fontSize` → `text-[<px>]` (the loop's category fallback can emit `color`/`fontSize` — unverified, `_css_class` maps neither today). Override breakpoints synthesize `max-[{width}px]:{_css_class(prop, value)}` per prop.
- **F3 — The fill-suppression rule must cover image fills, stated explicitly.** The pixel path always patches `#rrggbb` (verified: `patch_planner._determine_property` returns `background` only when `new_value.startswith("#")` — so `bg-[#…]` is always Tailwind-safe, no rgb/spaces risk). An override `background` must suppress the IR fill classes **including `bg-[url(...)] bg-cover bg-center`** on image-fill nodes — override wins entirely, which is exactly what html_css does (the override *replaces* a computed `background: url(...)`). Tests must cover the image-fill case.
- **F4 — The money test must not assert monotonic improvement.** Part 20's own smoke: repair *improves* a red-baseline score but does **not** converge (0 → 0.0796, still FAILED). Task 4 asserts: real iterations ≥ 1, the **right** backend regenerated, `source: "re-rendered"`, a real re-measured score, `Verification:` printed — not "beats pre-repair".
- **F5 — `styles_to_dict` serializes the FULL style layer, not just repairs** (`{node_id: {base, breakpoints}}` for every node). This is a feature, not a bug: the override union is **idempotent** for un-repaired props, which is exactly why "absent/empty override → byte-identical" holds, and why re-applying the full base through react's class loop re-emits identical classes. Document it so the seam isn't "optimized" later.
- **F6 — Override-before-pop ordering is required, verified.** `generate_style` writes `position: absolute` into `style.base` for absolute nodes (`web_common.py` line 171–172), so the serialized override carries it; the `ScopedCssGenerator` absolute-pop must run **after** the override union (else the override re-adds absolute positioning). Confirmed: the pop only removes display/position/left/right/top/bottom — never `background`.
- **F7 — Defensive guard:** if `generatedManifest` is missing in the repair handler (legacy `--file` generate fallback), default the backend to html_css (today's behavior) with a note rather than crashing.
- **F8 — One asset manifest, threaded end-to-end.** The SAME shared `assetManifest` must flow into repair regeneration (`--assets`) and the verify re-bundle (`invokeBundleRender`) so regenerated output's image fidelity matches the original run (no drift between the run's generated code and the repaired code).

## Design

### 1. Python — `styles_override` seam for the three web backends (byte-identical when absent)

- **vue / svelte**: `ScopedCssGenerator` gains an optional `overrides: Dict[str, Dict[str, Any]] = None`; `_node` applies `override.base`/`override.breakpoints` as a union on `style.base`/`style.breakpoints` right after `_extend` and **before** the absolute-position pop (F6). Both backends' `generate()` thread `opts.get("styles_override")` into the generator (fallbacks inherit via the scoped class).
- **react**: `_classes_for` gains the override map (threaded from `generate()` → `_render_component` → `_render_node`); it applies `override.base` into the computed style **first** (`style.base.update(...)` — the html_css union semantics, F2), then runs the existing class loops. New mappings for the loop's patch surface: `background` → `bg-[<value>]`, `color` → `text-[<value>]`, `fontSize` → `text-[<px>]`. An override `background` suppresses the IR fill classes **including image fills** (`bg-[url(...)] bg-cover bg-center`) — override wins entirely, matching html_css's replacement semantics (F3). Override breakpoints synthesize `max-[{width}px]:{_css_class(prop, value)}` per prop.
- All three: absent/empty override → byte-identical output (determinism suite locks it), mirroring html_css's Part-20 contract. The override is the full serialized layer when repair ran (F5) — idempotent for un-repaired props.

### 2. Python — `pipeline.py repair --backend <name>` (+ `--resolution`, `--assets`)

- Registry lookup by backend name; allowed set = browser-renderable backends (`html_css`, `react_tailwind`, `vue`, `svelte`); native backends (`swiftui`, `flutter`) → exit 2 with the honest reason (no browser harness). Default stays `html_css`.
- Regenerate through the resolved backend with `options={"styles_override": repaired_styles, "assets": assets}` and `resolution=<loaded report>` (F1 — `--resolution <json>`, so web regeneration keeps component/instance/token resolution and the Part-21 fallback markers; unreadable → exit 4); files under `out/generated/<backend>/`; payload `generated.backend` = the real backend. Exit-code contract unchanged (2 usage / 4 bad input / 1 failure). `--assets <manifest.json>` optional (unreadable → exit 4).

### 3. TS — repair handler passes the run's backend (+ resolution)

- `invokeRepair` gains `backend` (appended as `--backend`) and stages the shared `resolutionJson` (F1 — verified available at `backend_codegen.ts` line 485) as `--resolution` when present. `createRepairStageHandler` reads `generatedManifest.backend` from shared state and passes it; missing manifest → html_css default with a note (F7); a non-browser backend → inert note rather than a mis-targeted spawn.

### 4. TS — verify re-render branches for bundler backends

- Post-repair re-render: html_css → existing per-file `invokeRender`; bundler backend (`BUNDLE_BACKENDS`) → `invokeBundleRender` (injectable `bundleInvoker` seam for tests) on the regenerated dir with the **same** shared `assetManifest` (F8) + viewport into `verify-renders/`, then the existing comparator loop over the returned rows (`row.screenshot` vs the same baseline — the rows' `file`/`html`/`screenshot` shape maps directly; baseline unchanged, `source: "re-rendered"`, `ctx.updateMetrics` — the honest post-repair measurement). A bundler build failure here is an explicit error with the real vite stderr — never a fabricated score.

### 5. Honesty contract (unchanged + extended)

- Reference baseline → repair inert (the intended render is not something to converge toward) — untouched.
- Regeneration happens only when the loop actually ran (`iterations_run > 0`).
- A bundler build failure during verify re-render is an **explicit error** with the real vite stderr — never a fabricated score (Part 21 S4 contract).
- `--no-bundle` and native targets keep their honest degrades; repairs only ever target **external** baselines.

### 6. Money test (skippable, real toolchain)

The Part 22 analog of Part 20's red-baseline CLI test: `figmaforge run --target=react+tailwind --baseline <red>` (real npm + chromium) → `Repairs:` ≥ 1, regenerated **react** files under `<run>/repair/generated/react_tailwind/`, verify `source: "re-rendered"` with a real re-measured score, and a `Verification:` line. Per F4, **no monotonic-improvement assertion** (Part 20's smoke: 0 → 0.0796 — real improvement but never convergence on a red baseline; a FAILED verification is still valid output).

## Scope / non-goals

- **In scope:** the override seams (vue/svelte/react), `repair --backend` + `--assets`, TS repair/verify backend threading + bundler re-render branch, the money test, docs.
- **Not in scope:** swiftui/flutter repair (native simulators); LLM/human-approval iterations beyond the existing `--require-approval` gate; publishable production scaffolds (the harness remains a measurement scaffold); multi-backend repair in one run; `--no-bundle` + repair (degrade unchanged).

## Risks

- **react override precedence** — an override `background` must win over the IR solid-fill class; the test asserts exactly one `bg-` class per node when overridden.
- **ScopedCssGenerator ordering** — the override applies after `_extend`; the absolute-position pop must not remove repaired props (it doesn't — verified finding 3).
- **Verify timing** — re-bundling a regenerated dir runs a full npm install+build (~30–60s); the money test needs a generous timeout and skips cleanly without chromium/node_modules.

## Success criteria

`figmaforge run --target=react+tailwind|vue+scoped_css|svelte+scoped_css --baseline <red>` → real repair iterations, regenerated output of the **right** backend, re-bundled re-measure (`source: "re-rendered"`), honest `Verification:`; html_css path byte-identical (no regression). Gate: **605 → ~620 Python / 162 → ~168 TS**.
