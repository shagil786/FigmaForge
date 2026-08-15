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

## Design

### 1. Python — `styles_override` seam for the three web backends (byte-identical when absent)

- **vue / svelte**: `ScopedCssGenerator` gains an optional `overrides: Dict[str, Dict[str, Any]] = None`; `_node` applies `override.base`/`override.breakpoints` as a union on `style.base`/`style.breakpoints` right after `_extend`. Both backends' `generate()` thread `opts.get("styles_override")` into the generator (fallbacks inherit via the scoped class).
- **react**: `_classes_for` gains the override map (threaded from `generate()` → `_render_component` → `_render_node`); override base props emit through `_css_class` (new mapping `background` → `bg-[<value>]`), with an override `background` suppressing the IR fill class for that node; override breakpoints emit through `_breakpoint_class`.
- All three: absent/empty override → byte-identical output (determinism suite locks it), mirroring html_css's Part-20 contract.

### 2. Python — `pipeline.py repair --backend <name>` (+ `--assets`)

- Registry lookup by backend name; allowed set = browser-renderable backends (`html_css`, `react_tailwind`, `vue`, `svelte`); native backends (`swiftui`, `flutter`) → exit 2 with the honest reason (no browser harness). Default stays `html_css`.
- Regenerate through the resolved backend with `options={"styles_override": repaired_styles, "assets": assets}`; files under `out/generated/<backend>/`; payload `generated.backend` = the real backend. Exit-code contract unchanged (2 usage / 4 bad input / 1 failure). `--assets <manifest.json>` optional (unreadable → exit 4).

### 3. TS — repair handler passes the run's backend

- `invokeRepair` gains `backend` (appended as `--backend`). `createRepairStageHandler` reads `generatedManifest.backend` from shared state and passes it (guard: only browser backends reach the spawn anyway, but a non-browser backend → inert note rather than a mis-targeted spawn).

### 4. TS — verify re-render branches for bundler backends

- Post-repair re-render: html_css → existing per-file `invokeRender`; bundler backend (`BUNDLE_BACKENDS`) → `invokeBundleRender` (injectable `bundleInvoker` seam for tests) on the regenerated dir with the shared `assetManifest` + viewport into `verify-renders/`, then the existing comparator loop over the returned rows (baseline unchanged, `source: "re-rendered"`, `ctx.updateMetrics` — the honest post-repair measurement).

### 5. Honesty contract (unchanged + extended)

- Reference baseline → repair inert (the intended render is not something to converge toward) — untouched.
- Regeneration happens only when the loop actually ran (`iterations_run > 0`).
- A bundler build failure during verify re-render is an **explicit error** with the real vite stderr — never a fabricated score (Part 21 S4 contract).
- `--no-bundle` and native targets keep their honest degrades; repairs only ever target **external** baselines.

### 6. Money test (skippable, real toolchain)

The Part 22 analog of Part 20's red-baseline CLI test: `figmaforge run --target=react+tailwind --baseline <red>` (real npm + chromium) → `Repairs:` ≥ 1, regenerated **react** files under `<run>/repair/generated/react_tailwind/`, re-bundled re-measure with `source: "re-rendered"` beating the pre-repair score, and a `Verification:` line. (Whether the loop fully converges to ≥ 0.95 on the red baseline is not asserted — Part 20's honest contract is that a FAILED verification is still valid output; the assertions are: real iterations, the right regenerated backend, and a re-measured score.)

## Scope / non-goals

- **In scope:** the override seams (vue/svelte/react), `repair --backend` + `--assets`, TS repair/verify backend threading + bundler re-render branch, the money test, docs.
- **Not in scope:** swiftui/flutter repair (native simulators); LLM/human-approval iterations beyond the existing `--require-approval` gate; publishable production scaffolds (the harness remains a measurement scaffold); multi-backend repair in one run; `--no-bundle` + repair (degrade unchanged).

## Risks

- **react override precedence** — an override `background` must win over the IR solid-fill class; the test asserts exactly one `bg-` class per node when overridden.
- **ScopedCssGenerator ordering** — the override applies after `_extend`; the absolute-position pop must not remove repaired props (it doesn't — verified finding 3).
- **Verify timing** — re-bundling a regenerated dir runs a full npm install+build (~30–60s); the money test needs a generous timeout and skips cleanly without chromium/node_modules.

## Success criteria

`figmaforge run --target=react+tailwind|vue+scoped_css|svelte+scoped_css --baseline <red>` → real repair iterations, regenerated output of the **right** backend, re-bundled re-measure (`source: "re-rendered"`), honest `Verification:`; html_css path byte-identical (no regression). Gate: **605 → ~620 Python / 162 → ~168 TS**.
