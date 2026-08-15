# Repair + Verify Stages — auto-repair and final verification through the TS runtime (Part 20) — Design Spec

> **Branch:** `feat/part-20-repair-verify` (from main @ `40269c2`; Parts 18–19 are open PRs #21/#22 — this branch assumes only the Part 17 main state).
> **Gate baseline (this branch, off main):** Python **515 OK, zero skips** (43 files) · TS `npx tsc` clean, **131 passing** · `claude plugin validate --strict` ✔. If #21/#22 merge first, counts shift to 526/133 and 533/141 respectively — the plan's per-task expectations state the branch-real numbers.

## Problem

`figmaforge run` exercises **eight real stages** (Part 19) and ends with a measured `Score` + `Visual verdict`, but it stops there: `PIPELINE_STAGES` has declared `repair`/`verify` slots since Part 7, no handlers are registered, and `cmdRepair` is a read-only stub that prints mismatch counts and tells the user to run `figmaforge run`. Nothing in the runtime *fixes* a low score, and nothing *gates* the run on a final pass/fail. The user's ask: **auto-repair and re-verify until the SSIM gate passes.**

All the repair machinery already exists and is tested (Parts 8/11/12/13): `RepairLoop` (render → diff → classify → plan → execute → re-render, with SSIM gating, rollback, and baseline auto-refresh), `RepairClassifier` (9 categories), `PatchPlanner`/`PatchExecutor` (tokens/layout/position/style/asset/structure targets), `make_render_callable` (real harness → `RepairLoop(render_fn=...)`), and `reference_styles_from_plan` (Part 19 — the shared web lowering, the same styles the html_css backend computes). The runtime has a `Repairs:` summary line, a `repairIterations` metric, a `repair_result` artifact kind, `--max-repair` flag, and `budget.repairIterations` — all waiting on the two handlers.

## Verified findings (not assumed)

1. **The loop repairs the shared style/layout layer, not generated files.** `RepairLoop.run(plan, document, library, styles, manifest)` mutates in-memory `LayoutPlan` (positions/sizes/spacing), `VStyle` dicts (per node), `ProjectLibrary` tokens, and IR asset refs. `make_render_callable` renders `generate_render_html(document, styles)` — the *reference* HTML — through the real harness. It never renders backend output. This is deliberate ("Prefer regeneration over manual editing"; "The loop modifies *source code and design tokens*, never screenshots").
2. **Therefore propagation = regeneration from the repaired layer.** The plan object is mutated *in place*, so serializing it after the loop and re-lowering the backend from it automatically carries layout/position/spacing patches. Style patches (VStyle `base` mutations) are NOT carried by re-lowering — the backend recomputes styles from IR via `extend_ir_style`. So regeneration needs a `styles_override` seam on the backend's `generate()`.
3. **Against the reference baseline, repair is inert-by-construction.** The loop renders the reference HTML and diffs against the baseline. If the baseline *is* the reference render (Part 19 default), the loop's first diff is 1.0 → threshold satisfied → 0 iterations. A low compare score with a reference baseline means a *codegen* regression — invisible to the loop (it never renders generated files) and correctly unrepairable. The honest contract: **repair helps when the baseline is external** (`--baseline` / `--figma-baseline`); with a reference baseline the repair stage short-circuits with an explanatory note and verify still fails honestly.
4. **Pixel-derived color repair is currently broken (real Part 8 gap).** `pixel_mismatch` → `CATEGORY_COLOR` → `PatchPlanner._determine_new_value` returns `expected` (a region dict + `baseline_mae`), and `_determine_property` returns `"color"` — so the loop would set `style.base["color"] = {region: …}`. The raster tests never hit it because the capped pixel weight (0.15) crosses the 0.95 threshold before any patch applies. Part 20 must make pixel→color repair real: extract the baseline's mean color in the attributed region and patch `background`.
5. **Runtime surfaces exist:** `stageToArtifactKind` maps `repair → repair_result`, `verify → metrics`; the summary prints `Repairs: ${result.repairIterations}` from `metrics.repairIterations`, which `executeStage` overwrites from `budget.current.repairIterations` after each handler — so the repair handler must bump the budget, not just `updateMetrics`. `--max-repair` already parses into `budgets.maxRepairIterations`.
6. **Renderability constraint:** only html_css produces standalone browser-renderable output in the runtime (react/vue/svelte need a bundler; native targets need simulators — Part 19 renders degrade to notes). Repair regeneration is therefore scoped to html_css; other backends are an honest non-goal.

## Design

### 1. Contained repair: pixel → color value extraction (`patch_planner.py` / `repair_loop.py`)

`PatchPlanner` gains an optional `baseline_png: Optional[str]` (default `None`). For a `CATEGORY_COLOR` candidate whose mismatch has an `expected.region` and a decodable baseline PNG, `_determine_new_value` decodes the baseline (`core.png_codec.decode_png`), averages the RGB over the region (clamped to image bounds), and returns a `#rrggbb` string; `_determine_property` returns `"background"` (the property html_css actually emits for fills). Non-decodable baseline / no region / `baseline_png=None` → current behavior (region dict) — honest degrade, never a crash. `RepairLoop.run` passes `config.baseline_png` to the planner it constructs. Existing tests (`test_repair_loop_raster`, `test_repair_classifier_pixel`) must stay green unchanged.

### 2. `html_css` styles override seam (`backends/html_css/__init__.py`)

`generate(..., options={"styles_override": {node_id: {"base": {...}, "breakpoints": {...}}}})` applies the per-node overrides in `_apply_styles` **after** `extend_ir_style` (union on top — computed styles remain the base). Absent/empty `styles_override` → byte-identical output (backward compat, locked by the existing deterministic tests). The repaired render and the regenerated code then share one style rule.

### 3. `pipeline.py repair` subcommand (one-JSON-line contract, injection seams)

```
pipeline.py repair --ir <ir.json> --layout <layout.json> --baseline <png>
  [--viewport WxH] [--out <dir>] [--backend html_css]
  [--max-iterations N] [--threshold X] [--no-ssim] [--require-approval]
```

1. Load + schema-validate IR and plan (Part 16 round-trip loaders); `--baseline` must exist (exit 4 otherwise); `--backend` must be `html_css` (exit 2 otherwise — honest, only browser-renderable standalone output).
2. `styles = reference_styles_from_plan(doc, plan)`; `library = LibraryLoader().load()` (token patches); harness = real `RenderHarness(<out>/renders)`; `render_fn = make_render_callable(harness, default_height=viewport_h)`.
3. `RepairLoop(RepairConfig(similarity_threshold=threshold, max_iterations=N, baseline_png=…, ssim_enabled=not --no-ssim, require_approval=…), render_fn).run(plan, doc, library=library, styles=styles, run_id=…)` — the loop already stops at `threshold_satisfied`, `no_safe_repair`, `insufficient_progress`, `max_iterations`, `regression_detected`, `approval_denied`.
4. Serialize the repaired styles `{node_id: {"base":…,"breakpoints":…}}` to `<out>/styles.repaired.json`; **regenerate** html_css from the (mutated) plan + `styles_override` into `<out>/generated/html_css/`; skip regeneration only when 0 repairs ran (report `generated: null`).
5. Emit exactly one JSON line: `{ok, success, final_score, iterations_run, stop_reason, unresolved_differences, repairs: [per-iteration {iteration, similarity_before, similarity_after, patch_count, applied, rejected}], categories, repaired_styles, generated: {backend, files: [{path, language}]}}`.
6. Exit codes mirror the family: **2** usage/unsupported backend, **4** unreadable/invalid input or missing baseline, **1** unexpected failure (never a traceback). A non-converged loop is a *valid report* → exit 0 with `success:false` (the run completes and verify gates honestly). No token exit (3) — the baseline comes from the compare stage, which owns token-gated live downloads.
7. `repair_main(argv, harness_cls=…)` injection seam (tests use a fake harness, Part 19 style); `main()` dispatch refactor shared with the other subcommands.

### 4. TS repair stage handler (`backend_codegen.ts`)

- `invokeRepair(cfg, irJson, layoutJson, baseline, outDir, opts)` — stages IR/layout to temp, spawns `pipeline.py repair`, parses the single JSON line; nonzero exit → typed error with stderr detail.
- `createRepairStageHandler()` — reads shared `diffReport` + `compareBaseline`/`compareBaselineKind` (the compare handler must now share the resolved baseline it chose). **Short-circuit, never spawn**: no screenshots → `{repairs: 0, success: null, note: "no measured score — nothing to repair"}`; `baseline_kind === "reference"` → note the by-construction contract (reference is the intended render; a low score is a codegen regression verify will catch); score ≥ threshold → `{repairs: 0, note: "gate already satisfied"}`. Otherwise spawn repair into `<run>/repair/`, store the `repair_result` artifact, share `repairOut`/`repairManifest`/`repairStylesPath` for verify, and **bump the budget** `repairIterations` by `iterations_run` (via the existing budget update API) so the `Repairs:` summary line is real. `ctx.updateMetrics({repairIterations})` alone is insufficient — `executeStage` overwrites it from the budget.

### 5. TS verify stage handler (`backend_codegen.ts`)

- Reads shared: `repairOut`/`repairManifest` (regenerated files), `compareBaseline` + `compareBaselineKind`, threshold (`--similarity-threshold`, default 0.95), viewport.
- If repair regenerated files: re-render each via `invokeRender` into `<run>/verify-renders/`, compare each against the **same** baseline via the SSIM-gated `ScreenshotComparator` → fresh final score (the honest post-repair measurement).
- Else (no repair): reuse the compare stage's `diffReport` score — the final check of the same measurement.
- Verdict: `passed = score >= threshold`. Store the `metrics`-kind artifact `{passed, similarity_score, threshold, baseline_kind, screens}` and `ctx.updateMetrics({similarityScore})`. No screenshots anywhere → `{passed: null, note: "no measured score — cannot verify"}` — never a fabricated pass/fail.

### 6. `cmdRun` wiring (`main.ts`)

Register all **ten real stages** (ingest → normalize → resolve → layout → assets → generate → render → compare → repair → verify). New flags: `--no-repair` (force the repair short-circuit), `--similarity-threshold <0..1>` (default 0.95), thread the existing `--max-repair` into repair's `--max-iterations`. After the run, print a `Verification:` line (PASSED / FAILED / cannot verify) alongside the existing `Score`/`Repairs`/`Visual verdict` lines. Repair/verify are now the terminal gate: a failed verification does **not** fail the run (the report is valid output — `run` exits 0 with a FAILED verification, consistent with compare never exiting nonzero for a low score).

## Honesty contract (documented in the spec and the docs)

- Repair operates on the shared style/layout layer and propagates by **regeneration** — it never edits generated files, hides differences, or blurs screenshots.
- Repair is inert against a reference baseline by construction; its meaningful path is an external baseline (`--baseline`/`--figma-baseline`), where it genuinely moves the intended render toward the Figma truth and the regenerated code follows.
- A codegen regression (generated code ≠ reference) is unrepairable by the loop and surfaced honestly: repair short-circuits with the contract note, verify still measures and fails.
- Non-renderable targets (bundler/simulator) get the same honest no-measured-score degrade as Part 19 — no fabricated repairs or verdicts.

## Risks / edge cases (reviewed)

1. **Pixel-color extraction bounds:** regions from `attribute_regions` may exceed the image or overlap multiple nodes — clamp to image bounds; attribution comes from the existing `render_meta` overlap logic (largest overlap wins), already tested in `test_pixel_diff.py`. Mean-color over a mixed region is an approximation — the patch is one node's `background`, and the loop re-renders to measure the effect; if SSIM judges the remainder perceptually identical, `clean` stops further work (Part 13 gate).
2. **Style override + regeneration drift:** the repaired reference render and the regenerated html_css both lower the same styles + overrides — but html_css adds its own emission (selectors, `data-node-id`, wrapping). Verify measures the *regenerated* output, so any drift shows up as a real number. The money test (Task 7) proves convergence end-to-end with real chromium.
3. **Determinism:** `reference_styles_from_plan` + the loop's deterministic planner give byte-identical `styles.repaired.json` and regenerated files for identical inputs; browser renders vary only below the SSIM noise floor (Part 13). Two-run byte-compare on the artifact JSON in tests.
4. **Budget interplay:** `executeStage` overwrites `repairIterations` from the budget after every handler — the repair handler must bump the budget (design point 4), else `Repairs:` stays 0. Regression-tested in Task 7.
5. **Threshold plumbing:** one flag (`--similarity-threshold`) feeds compare's report (measurement), repair's `RepairConfig.similarity_threshold`, and verify's pass/fail — no divergent knobs.
6. **`--no-repair`** is a hard short-circuit, not a threshold tweak — it must never silently weaken verification (verify still runs and reports honestly).
7. **No Figma token path in repair:** live baselines are downloaded by the compare stage (token-gated, exit 3 there); repair consumes the downloaded PNG — no new secret surface.

## Scope

**In:** the planner pixel→color fix, html_css `styles_override`, `pipeline.py repair`, TS repair + verify handlers, cmdRun wiring + flags + `Verification:` line, docs, gate.

**Out (non-goals, deferred):** repair regeneration for react/vue/svelte/swiftui/flutter (not browser-renderable in the runtime); `cmdRepair` single-command overhaul (it stays a read-only inspector); token-patch propagation into generated code (tokens live in the library; the html_css lowering reads concrete IR colors — recorded in the repair history, honest); compiling/executing generated native/TSX code; Figma OAuth.

## Success criteria

- `figmaforge run --file=<fixture> --target=html+css` → **10 artifacts**, `Score` ≥ threshold, `Repairs: 0`, `Verification: PASSED`.
- `figmaforge run … --baseline <color-shifted.png>` → compare score < threshold → repair runs (≥ 1 iteration) → regenerated html_css carries the repaired color → `Verification:` reflects the measured post-repair score.
- `figmaforge run --target=flutter` → render degrades → repair + verify short-circuit with honest notes, run still completes, 10 artifacts.
- Gate: **515 → ~535 Python** / **131 → ~150 TS**, `claude plugin validate --strict` ✔, real-chromium smoke at the gate.
