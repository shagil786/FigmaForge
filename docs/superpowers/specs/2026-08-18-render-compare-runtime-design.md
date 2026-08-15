# Wire Render + Compare into the Runtime (Part 19) — Design Spec

> **Status:** Approved for implementation planning.
> **Branch:** `feat/part-19-render-compare` (from main @ `40269c2`, after Part 17 merged; Part 18 is PR #21, open — Part 19 assumes only the Part 17 main state).
> **Gate baseline (this branch, off main):** Python **515 OK, zero skips** (43 files) · TS `npx tsc` clean, **131 passing** · `claude plugin validate --strict` ✔. If PR #21 merges before Part 19's gate, the counts shift to 526/133 — the plan's per-task expectations state both.

## Problem

`figmaforge run` exercises **six real stages** (ingest → normalize → resolve → layout → assets → generate), but stops at code generation. The runtime has **no measured visual verdict**: nothing renders the generated code and diffs it against a baseline, so "pixel parity" is claimed, never proven. All the machinery exists in Python — `RenderHarness` (real Playwright, Part 7/11), `generate_render_html` (IR → reference HTML, Part 11), `pixel_diff` CLI with the SSIM gate (Part 12/13), `DiffEngine._diff_raster` with the noise-floor + regional-SSIM `clean` verdict (Part 13), and `figma_assets.download_baselines` (live Figma renders, Part 12). The TS `ScreenshotComparator` already shells out to `pixel_diff` with a hash fast-path, and `PIPELINE_STAGES` has declared `render`/`compare` slots since Part 7 — but **no stage handlers are registered** for them.

Part 19 closes that gap: `figmaforge run` renders the generated web output in a real browser and prints a measured similarity score against a baseline, with the SSIM verdict included.

## Design

### 1. One CLI, three modes: `pipeline.py render`

A new `render` subcommand on the existing pipeline CLI, sharing the pixel_diff contract (exactly one JSON line on stdout; failures emit `{"error": "..."}` and exit 1 — never a traceback).

```
python3 -m scripts.pipeline render --html <generated.html> [--viewport WxH] [--out DIR]
python3 -m scripts.pipeline render --ir <ir.json> --layout <layout.json> [--viewport WxH] [--out DIR]
python3 -m scripts.pipeline render --baselines --file-key <key> --nodes <a,b> [--out DIR]
```

- **`--html` mode (the shot):** renders a generated standalone HTML file (e.g. html_css `screen_0.html`) through the real `RenderHarness` at the given viewport → writes `<out>/<build_id>.png` + extracts `window.__figmaforge_meta` (empty for generated output that lacks the meta script — stored anyway for downstream use).
- **`--ir + --layout` mode (the reference baseline):** computes the intended per-node VStyles from the layout plan via the **same shared web lowering** the html_css backend uses (`CssStyleGenerator.generate_style` + `extend_ir_style`, recursively over each screen), builds the reference document via `generate_render_html(document, styles, viewport)`, renders it through the same harness → the baseline PNG. The reference render carries `data-node-id`s and the meta script, so its `render_meta` is populated.
- **`--baselines` mode (live Figma baseline, token-gated):** wraps the existing `download_baselines` (FigmaClient.get_images → presigned URLs → bounded-retry fetch → `AssetManager` content-addressing). Returns the local path of each downloaded baseline. Missing `FIGMA_TOKEN` → clean error (exit 3, mirroring the assets stage); no `--nodes` → exit 2.

The harness is injectable (`render_main(argv, harness_cls=RenderHarness)`), so unit tests run with a FakeHarness — no browser in the suite (zero-skips discipline preserved). Missing Playwright surfaces as `RenderHarnessError` → `{"error": "...playwright is required..."}` exit 1, exactly like the Part 7 contract.

### 2. Render stage handler (TS): `createRenderStageHandler`

Reads the generate-stage output (`generatedManifest` shared → `filesDir` recomputed as `<outputDir>/<runId>/generated/<backend>`). For a **browser-renderable target** (`defaultRenderer(framework) === "browser"` AND generated files are directly renderable — html_css standalone HTML today), it invokes `render --html` on each generated `*.html`, collects `{file, screenshot, meta}` rows, stores them in shared (`renderOutputs`, `screenshotPaths`), and returns `{screenshots, rendersDir}` as the stage artifact (kind `screenshot`).

**Honest degradation:** react/vue/svelte outputs require a bundler and native targets require simulators — the handler produces a `{note, screenshotPath: null}` artifact (kind `screenshot` with a `note` payload), mirroring `renderDemoWeb`'s documented behavior. The compare stage then reports **no measured score** (null similarity + note), never a fabricated one. Playwright/chromium missing → the render stage fails cleanly with the harness's install hint (a real stage failure — the runtime must not silently skip its measurement, because a run that claims completion without a score would violate the honesty rule).

### 3. Compare stage handler (TS): `createCompareStageHandler`

Baseline resolution, in priority order:

1. **`--baseline <path.png>`** (explicit flag, existing `cmdCompare` contract — wins over everything).
2. **`--figma-baseline`** (live: requires `--file-key` + `FIGMA_TOKEN`; invokes `render --baselines` → the true Figma render. Missing token/file key → clean error exit 3/2, surfaced as a stage error).
3. **Reference render** (default): invokes `render --ir + --layout` from shared `irJson`/`layoutJson` → the intended render.

Then, per generated screenshot: `ScreenshotComparator.compare(shot, baseline)` (SHA-256 hash fast-path + `core.pixel_diff` SSIM gating — existing machinery). The handler builds a **`diff_report` artifact** shaped like the Python `DiffReport` for downstream tooling:

```json
{
  "similarity_score": 0.9984,
  "categories": {"geometry": null, "style": null, "pixels": 0.9984},
  "raster_stats": {"ssim": 0.9991, "min_region_ssim": 0.9972, "ssim_clean": true,
                   "diff_percentage": 0.0012, "mae": {...}, "region_count": 1},
  "screens": [{"file": "screen_0.html", "similarity": 0.9984, "ssim_clean": true}],
  "baseline": "<path>", "baseline_kind": "reference" | "explicit" | "figma",
  "note": null
}
```

The headline `similarity_score` is the **mean** across screens (conservative detail rows kept per screen). The handler stores `diffReport` in shared and updates the run's metrics via a new `PipelineContext.updateMetrics(partial)` seam (wired in the coordinator constructor to `StateMachine.updateMetrics`) — so `figmaforge run` prints the **real measured Score**, persisted to checkpoints. When no screenshot exists (non-renderable target): `similarity_score: null`, `note` set, metrics unchanged (honest "no measured score").

**Honesty contract (documented):** the default reference baseline is derived from the *same* IR + shared style machinery the generated code lowers from — a clean verdict therefore means **"the generated code reproduces the intended render"** (a codegen-fidelity regression gate). Design judgment — does the IR match Figma? — is the `--figma-baseline` / `--baseline` path. Both are real measurements; neither is claimed to be the other.

### 4. CLI wiring

`cmdRun` registers `render` + `compare` handlers (after `generate`, before `repair`), threads `--baseline <path>` and `--figma-baseline` into shared, and the run summary gains a `Visual verdict` line (similarity + SSIM verdict from the diff report). The standalone `cmdRender`/`cmdCompare`/`cmdRepair` commands keep working unchanged. `repair`/`verify` remain unwired — they need the LLM-driven RepairLoop and are the Part 20 follow-up (explicit non-goal).

## Non-goals (deferred, explicit)

- **Repair + verify stages** (Part 20): the Python `RepairLoop`/`DiffEngine`/`verify` pipeline stays unwired; Part 19 stops at a measured score per run.
- **Baseline auto-refresh at the runtime level**: Part 13's provenance-safe refresh lives in the repair loop; a run-level persistent baseline store is a documented follow-up.
- **Bundling react/vue/svelte output for rendering** (esbuild/vite): out of scope; those targets honestly report "no measured score" until a bundler stage exists.
- **Multi-screen reference baselines**: `generate_render_html` renders `page[0]`'s children — one reference per run. The per-screen shot rows still all diff against it; a per-screen reference mode is a documented follow-up.
- **Figma image-fill fit modes / stroke alignment** (the Part 14 audit items): not touched here.

## Risks & mitigations

- **Circular-ish baseline for html_css**: mitigated by honesty docs + the fact that a clean verdict still catches generator regressions; the true gate is `--figma-baseline`.
- **Playwright absence**: unit tests never need a browser (injectable harness); the end-to-end smoke requires `playwright` + chromium (verified installed on this machine) and degrades to a documented skip otherwise.
- **`similarityScore` plumbing**: the new `ctx.updateMetrics` seam is additive; existing stage handlers don't touch it, and `executeStage`'s budget update only overwrites budget fields.
- **Merge with Part 18 (PR #21)**: Part 19 touches the same `cmdRun` registration block; the merge conflict is small (two added `onStage` lines) and resolved when PRs merge in order.
