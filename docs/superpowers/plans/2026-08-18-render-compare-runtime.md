# Wire Render + Compare into the Runtime (Part 19) Implementation Plan

> **For agentic workers:** This plan is written for `superpowers:subagent-driven-development`.
> Execute it task-by-task with a fresh subagent per task; each task is a self-contained TDD
> cycle (write failing test → run and expect FAIL → minimal implementation → run and expect
> PASS → commit). Never batch tasks, never skip the failing-test step, and verify the exact
> expected output shown in each step before committing. Python commands run from
> `plugin/figmaforge` unless stated otherwise; TypeScript commands run from the repo root.
> `pytest` is NOT installed — always use `PYTHON_BIN=/opt/homebrew/bin/python3.14 python3 -m
> unittest discover -s tests` (full suite) or `PYTHON_BIN=/opt/homebrew/bin/python3.14
> python3 -m unittest tests.test_X -v` (targeted). The TS suite runs as
> `npx tsc && PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/tests/run_all.js`.

**Goal:** Make `figmaforge run` produce a **measured similarity score against a baseline** —
the render and compare stages become real pipeline stages. The render stage renders generated
web output through the real Playwright harness; the compare stage diffs it against a baseline
(explicit `--baseline`, live Figma render, or the IR reference render) using the existing
`pixel_diff` SSIM-gated comparator, and the run prints the measured score + perceptual verdict.

**Approved spec:** `docs/superpowers/specs/2026-08-18-render-compare-runtime-design.md`.

## Contract facts (verified against source at plan-writing time)

- Branch: `feat/part-19-render-compare` (off main `40269c2`; PR #21 for Part 18 is open and
  may merge before/after — resolve the small `cmdRun` registration overlap when it does).
- Baseline gate (this branch, off main @ `40269c2`): Python `Ran 515 tests ... OK`, ZERO
  skips (43 files) via `PYTHON_BIN=/opt/homebrew/bin/python3.14`; TS `npx tsc` clean and
  `131 passing, 0 failing`; `claude plugin validate --strict plugin/figmaforge` passes. If
  PR #21 (Part 18) merges first, the baseline shifts to 526/133 — re-derive the expected
  counts from the actual gate at each task.
- `scripts/pipeline.py` has subcommands `ingest|normalize|resolve|layout|assets|generate`
  (Parts 15–18); each prints ONE JSON line on success, `{"error": ...}` + nonzero exit on
  failure (argparse errors → exit 2, token missing → exit 3, invalid input → exit 4).
- `core/render_harness.py`: `RenderHarness(output_dir).render(content_html, viewport_spec,
  build_id, full_page=True) -> RenderResult(screenshot_path, layout_metadata)`; raises
  `RenderHarnessError` (with install hint) when Playwright is missing; `build_id` must match
  `^[A-Za-z0-9._-]+$`; `normalize_viewport` accepts `{"w","h"}`/`{"width","height"}`.
- `core/render_html.py`: `generate_render_html(document, styles, viewport_spec, title=...)`
  renders `document.pages[0].children` (fallback `document.root`) into `#figmaforge-root`
  with `data-node-id` on every element + the `window.__figmaforge_meta` script. `styles` is
  `Dict[str, VStyle]` keyed by node id (empty dict → unstyled nested divs).
- `backends/html_css/__init__.py` `_apply_styles(vnode, plan, style_gen, ir_by_id, assets)`
  recurses `style_gen.generate_style(plan)` + `extend_ir_style(style, plan, ir_node)` —
  the shared web lowering to reuse for the reference baseline styles.
- `core/figma_assets.py`: `download_baselines(client, file_key, node_ids, asset_manager,
  asset_handler=None, scale=1.0, fmt="png", transport=None, timeout_seconds=30.0,
  max_retries=2) -> Dict[node_id, BaselineAsset{node_id, local_path, content_hash,
  deduped}]`; `FigmaClient(file_key, token).get_images(file_key, node_ids, fmt, scale)`.
- TS `runtime/src/core/pipeline.ts`: `StageHandler(ctx, input) -> Promise<Record>`; handlers
  share via `ctx.shared` (Map); artifacts via `ctx.artifacts.storeJSON(kind, stage, name,
  data)` / `storeBuffer(kind, stage, name, buffer, ext)`; `stageToArtifactKind` already maps
  `render → "screenshot"`, `compare → "diff_report"`; `PipelineContext` has NO metrics seam —
  `StateMachine.updateMetrics(partial)` exists (checkpoint.ts `CheckpointMetrics` includes
  `similarityScore: 0`), so the coordinator constructor must expose it (e.g.
  `ctx.updateMetrics = (p) => this.sm.updateMetrics(p)`).
- TS `runtime/src/core/screenshot_compare.ts`: `ScreenshotComparator.compare(a, b)` →
  `ScreenshotComparison {similarity, diffPixelCount, diffPercentage, totalPixels, identical,
  meanAbsoluteError, ssim, minRegionSsim, ssimClean, ...}`; hash fast-path for identical
  buffers; shells to `python3 -m core.pixel_diff` (cwd = pluginDir).
- TS `runtime/src/cli/main.ts`: `cmdRun` registers 6 handlers (ingest…generate) and prints
  `Score: ${result.similarityScore}`; `--viewport=WxH` parses to `config.viewport`.
- `defaultRenderer(framework)` (types.ts) → `"browser"` for react/vue/svelte/html/angular/
  solid, else native renderers. Only html_css generated output is directly renderable
  (standalone HTML); react/vue/svelte need a bundler (documented in `renderDemoWeb`).
- Playwright **is installed** on this machine (verified: `import playwright` OK); chromium
  presence is checked at smoke time.
- Fixture: `plugin/figmaforge/fixtures/figma/layout_desktop.json` (single screen, no
  image fills) — the offline `run` smoke input; html_css generates `screen_0.html` + `styles.css`.

## Task 1: `pipeline.py render` — `--html` (shot) + `--ir --layout` (reference) modes (TDD)

**Test file:** `tests/test_pipeline_render.py` (new).

Steps:

1. Write `tests/test_pipeline_render.py` using an injectable FakeHarness (duck-typed
   `render(content_html, viewport_spec, build_id, full_page=True) -> RenderResult` recording
   calls, plus a `FailingHarness` raising `RenderHarnessError`). Use a tiny fixture IR + layout
   plan built in-test (mirror `tests/test_render_adapter.py`'s `_make_plan`/`_make_document`):
   - `test_html_mode_renders_file` — `render_main(["--html", html_path, "--out", out],
     harness_cls=FakeHarness)` → exit 0, stdout parses to ONE JSON line with
     `{"ok": true, "kind": "generated", "screenshot": <path>, "meta": {...}}`; the harness
     received the file's content, the default viewport, a valid build_id.
   - `test_html_mode_viewport_flag` — `--viewport 390x844` → harness viewport
     `{"width": 390, "height": 844}`; bad viewport → exit 1 with `{"error"}` (no traceback).
   - `test_reference_mode_uses_layout_styles` — `--ir ir.json --layout layout.json` (fixture
     JSON round-tripped through `to_dict`) → the harness's HTML contains the node ids AND the
     layout-derived style (e.g. a node with a box width → `width: <w>px` present in inline CSS,
     proving the shared style lowering ran); `{"kind": "reference"}` in the JSON line.
   - `test_reference_mode_no_layout_exits_2` — `--ir` without `--layout` → exit 2, `{"error"}`.
   - `test_missing_playwright_clean_error` — FailingHarness (or monkeypatched
     `RenderHarnessError`) → exit 1, stdout `{"error": "...playwright..."}` — never a traceback.
   - `test_missing_html_file_exits_4` — nonexistent `--html` path → exit 4, `{"error"}`.
   - `test_output_json_contract` — every success line has EXACTLY the documented keys
     (`ok`, `kind`, `screenshot`, `html`, `meta`, `viewport`); failures have exactly `error`.
2. Run `PYTHON_BIN=/opt/homebrew/bin/python3.14 python3 -m unittest tests.test_pipeline_render
   -v` → **expect FAIL** (module-level import of `render_main` fails / unknown subcommand).
   Red confirmed.
3. Implement:
   - `core/render_html.py`: add `reference_styles_from_plan(document, layout_plan) ->
     Dict[str, VStyle]` — walk each `layout_plan.screens` tree, per node compute
     `CssStyleGenerator().generate_style(node_plan)` then `extend_ir_style(style, node_plan,
     ir_by_id.get(node_id))` (import from `backends.web_common`; the html_css backend already
     does exactly this in `_apply_styles` — reuse, don't fork).
   - `scripts/pipeline.py`: add `render` subcommand with `--html | (--ir + --layout)`,
     `--viewport` (default `1440x900`, parsed via a small helper reusing the runtime's
     `WxH` convention), `--out` (default a temp dir). Implement `render_main(argv=None,
     harness_cls=RenderHarness)` (exported for tests): build content via `--html` (read the
     file verbatim) or `--ir/--layout` (load + schema-validate IR; build styles via
     `reference_styles_from_plan`; `generate_render_html(document, styles, viewport)`), render
     through `harness_cls(out_dir)` with build_id like `ff-render-<hash8>` (hash of content,
     deterministic), print the single JSON line. Map failures: missing html file → exit 4;
     missing `--layout` → exit 2; `RenderHarnessError`/ValueError → exit 1 `{"error"}`;
     wrap `main()` so `scripts.pipeline render` dispatches to it.
4. Run targeted → **expect PASS**. Full suite → **expect `Ran 527 tests ... OK`, ZERO skips**
   (515 on this branch + 12 new; if PR #21 merged first: 538). Commit:
   `git add core/render_html.py scripts/pipeline.py tests/test_pipeline_render.py && git commit
   -m "feat(pipeline): add render subcommand with generated-html and reference-IR modes"`.

## Task 2: `pipeline.py render --baselines` — live Figma baseline mode (TDD)

**Test file:** `tests/test_pipeline_render.py` (extend).

Steps:

1. Extend `tests/test_pipeline_render.py`:
   - `test_baselines_mode_downloads` — stub `FigmaClient`-like object + a stub transport
     (returns PNG bytes) injected via `render_main(argv, ..., client_cls=..., transport=...)`
     or a module-level seam; `--baselines --file-key fk --nodes n1,n2 --out out` → exit 0,
     JSON line `{"ok": true, "kind": "figma", "baselines": {"n1": <path>, "n2": <path>},
     "assets_dir": <dir>}`; paths exist, bytes are the stub PNG.
   - `test_baselines_missing_token_exits_3` — no `FIGMA_TOKEN` env → exit 3, `{"error": ...}`.
   - `test_baselines_missing_nodes_exits_2` — `--baselines` without `--nodes` → exit 2.
2. Run targeted → **expect FAIL** (unknown mode / missing seam). Red.
3. Implement in `scripts/pipeline.py`: `--baselines` requires `--file-key` + `--nodes`; token
   from `FIGMA_TOKEN` (never logged/printed); construct `FigmaClient` + `AssetManager` under
   `--out/assets`, call `download_baselines(client, file_key, node_ids, asset_manager,
   transport=transport)` with a default transport; emit the JSON line with per-node
   `BaselineAsset.local_path`. Missing token → exit 3; `FigmaAssetError` → exit 1 `{"error"}`.
   (Use an injectable seam `client_cls`/`transport` for tests, real defaults in the CLI.)
4. Run targeted → **expect PASS**; full suite → `Ran 53X ... OK`, ZERO skips. Commit:
   `git add scripts/pipeline.py tests/test_pipeline_render.py && git commit -m "feat(pipeline):
   add render --baselines live Figma baseline download mode"`.

## Task 3: TS `invokeRender` + `createRenderStageHandler` (TDD)

**Test file:** `runtime/tests/backend_codegen.test.ts` (extend — mirrors the Part 17/18
handler-test conventions; real Python spawns, stubbed upstream stages).

Steps:

1. Write the tests (red — `invokeRender`/`createRenderStageHandler` don't exist):
   - `invokeRender` renders a real generated HTML file: stage a minimal standalone HTML with a
     colored box to a temp file, `invokeRender(cfg, htmlPath, viewport)` → `{screenshot,
     meta}` with an existing PNG (real harness — Playwright is installed; keep the assertion
     to the returned shape + file existence, not pixel content).
   - `createRenderStageHandler` full-chain: stub ingest/normalize/layout/generate with
     injected IR + layout (like Part 18's staged test) so generate produces html_css output,
     then run the REAL render handler → artifact kind `screenshot`, shared `renderOutputs`
     has one entry for `screen_0.html`, screenshot file exists under
     `<run>/renders/`.
   - Non-renderable target (flutter): handler returns the honest `{note, screenshotPath:
     null}` shape without invoking python.
2. Run `npx tsc` → **expect FAIL** (missing exports). Red confirmed.
3. Implement in `runtime/src/core/backend_codegen.ts`:
   - `invokeRender(cfg, htmlPath, viewport)` — spawns `pipeline.py render --html <path>
     --viewport WxH --out <tmp>` (temp out dir), parses the JSON line via `parseJsonLine`,
     returns `{screenshot, meta}`; nonzero exit → typed error with stderr detail.
   - `createRenderStageHandler()` — read `generatedManifest` shared (+ recompute
     `filesDir = path.join(outputDir, runId, "generated", backend)`); if the target's
     renderer is `"browser"` AND generated files are directly renderable (html_css — check
     `backend === "html_css"` or the presence of `*.html` in filesDir; react/vue/svelte
     degrade): render each `*.html` into `<run>/renders/`, collect rows, set
     `ctx.shared.set("renderOutputs", rows)`, return `{screenshots, rendersDir}`. Otherwise
     return the honest degrade note (no python, no fabrication).
4. Run `npx tsc` → clean; `PYTHON_BIN=/opt/homebrew/bin/python3.14 node
   dist/runtime/tests/run_all.js` → **expect `13X passing, 0 failing`** (133 + new). Also run
   the Python suite (unchanged → 53X) to prove no cross-contamination. Commit:
   `git add runtime/src/core/backend_codegen.ts runtime/tests/backend_codegen.test.ts && git
   commit -m "feat(runtime): render stage handler — real browser screenshots of generated html"`.

## Task 4: TS `createCompareStageHandler` + metrics seam (TDD)

**Test file:** `runtime/tests/backend_codegen.test.ts` (extend).

Steps:

1. Write the tests (red):
   - `ctx.updateMetrics` exists: extend `PipelineContext` in `pipeline.ts` with
     `updateMetrics(partial: Partial<CheckpointMetrics>): void` wired in the constructor to
     `this.sm.updateMetrics(...)`; a unit test asserts a stage calling it changes
     `pipeline.state.metrics.similarityScore` (via a tiny test handler or direct check).
   - `createCompareStageHandler` reference baseline: run the real chain (ingest→…→render,
     stubbed upstream as in Task 3) then the REAL compare handler → artifact kind
     `diff_report` with `similarity_score` a number ≥ 0, `raster_stats.ssim_clean` a boolean,
     `baseline_kind === "reference"`, and `ctx` metrics `similarityScore` updated to the same
     value; the score is > 0.9 for the fixture (the html_css output reproduces the reference
     render — the honest regression-gate property).
   - `--baseline` override: shared `baselinePath` set → `baseline_kind === "explicit"`, the
     provided PNG is used (compare against a deliberately-different PNG → similarity < 0.9,
     `ssim_clean === false`).
   - No-screenshot degrade: compare after a non-renderable render → `{similarity_score:
     null, note: ...}`, metrics.similarityScore untouched.
2. Run `npx tsc` → **expect FAIL** (missing exports / ctx field). Red.
3. Implement in `pipeline.ts` (context seam) + `backend_codegen.ts`:
   - `createCompareStageHandler()`: resolve baseline (shared `baselinePath` →
     `invokeRenderBaselines` when shared `figmaBaseline` flag → else reference via
     `invokeRender` with `--ir/--layout` from shared), compare each screenshot row with
     `ScreenshotComparator.compare`, build the `diff_report` payload per the spec's JSON
     shape (headline = mean across screens, per-screen rows), store in shared (`diffReport`),
     `ctx.updateMetrics({ similarityScore: overall })` when a score exists, return the
     payload as the stage artifact. No screenshot → null score + note, no metrics write.
4. Run `npx tsc` → clean; TS suite → **expect `13X passing, 0 failing`**; Python suite →
   unchanged `53X OK`. Commit:
   `git add runtime/src/core/pipeline.ts runtime/src/core/backend_codegen.ts
   runtime/tests/backend_codegen.test.ts && git commit -m "feat(runtime): compare stage —
   measured similarity + SSIM verdict against a baseline"`.

## Task 5: Wire `cmdRun` — register stages, flags, measured-score summary (TDD)

**Test file:** `runtime/tests/test_all.ts` (extend — mirror the existing `cmdRun`/
`cmdCompare` capture conventions).

Steps:

1. Write the tests (red):
   - `cmdRun` with `--file=<fixture> --target=html+css --no-approval` completes with
     **9 artifacts** (6 prior + screenshot + render_meta + diff_report), exit 0, and the
     captured stdout contains a line with the measured score (e.g. `Score:` not `0`) and a
     visual-verdict line (e.g. `Perceptually identical` / `SSIM`).
   - `--baseline` flag threads through: run with `--baseline <png>` → the diff_report
     artifact records `baseline_kind: "explicit"`.
   - `--figma-baseline` without token → stage error (exit 1, stderr mentions token) — the
     honest live-gate contract.
2. Run `npx tsc` → **expect FAIL** (help/flags/registration absent). Red.
3. Implement in `runtime/src/cli/main.ts`:
   - Register `createRenderStageHandler()` + `createCompareStageHandler()` in `cmdRun` after
     generate (update the registration comment: render+compare wired, repair/verify Part 20).
   - Thread `--baseline <path>` (resolved path) and `--figma-baseline` (boolean) into shared
     before `pipeline.run()`.
   - After the run: read the diff report (shared or artifact) and print a `Visual verdict:`
     line (similarity + SSIM clean/change + baseline kind); keep the existing summary lines.
   - Update `printHelp` Options: document `--baseline` and `--figma-baseline`.
4. Run `npx tsc` → clean; TS suite → **expect `13X passing, 0 failing`**. Commit:
   `git add runtime/src/cli/main.ts runtime/tests/test_all.ts && git commit -m "feat(cli):
   wire render+compare stages into figmaforge run with measured visual verdict"`.

## Task 6: Docs — real-figma-demo.md, DEVELOPMENT_LOG, README, CLAUDE.md, architecture.md

Steps:

1. `docs/real-figma-demo.md`: the single-backend `run` section documents the **eight real
   stages** (…→generate→render→compare), the `render`/`compare` artifacts (`screenshot_*`,
   `diff_report_*`), baseline resolution order (`--baseline` → `--figma-baseline` →
   reference), and the honest contract (reference baseline = codegen-fidelity gate;
   `--figma-baseline` = true Figma gate; non-renderable targets report no measured score).
2. `docs/DEVELOPMENT_LOG.md`: full Part 19 entry (render subcommand modes, reference-style
   sharing, TS handlers, metrics seam, real counts with FILL markers).
3. `README.md`: status header → **Parts 1–19**; checklist lines for the render + compare
   stages and the measured-score claim; counts → new totals; Next Steps: strip render/compare
   from the punch list, leaving repair/verify.
4. `CLAUDE.md`: pipeline CLI line gains `render`; backend_codegen.ts line gains the two
   handlers; counts everywhere; test-category lists.
5. `docs/architecture.md`: both component bullets (scripts/pipeline.py + backend_codegen.ts)
   and the status paragraph → eight-stage front half + measured verdict.
6. Commit: `git add docs/real-figma-demo.md docs/DEVELOPMENT_LOG.md README.md CLAUDE.md
   docs/architecture.md && git commit -m "docs: document runtime render+compare stages (Part 19)"`.

## Task 7: Final verification gate + push + PR (do NOT merge)

Steps:

1. Python full suite → **expect `Ran 53X tests ... OK`, ZERO skips**.
2. TS: `npx tsc` clean, then `PYTHON_BIN=/opt/homebrew/bin/python3.14 node
   dist/runtime/tests/run_all.js` → **expect `13X passing, 0 failing`**.
3. `claude plugin validate --strict plugin/figmaforge` → **expect ✔ Validation passed**.
4. Fill the DEVELOPMENT_LOG Part 19 counts with the actual numbers; amend the docs commit if
   it is the last commit, else a follow-up `docs: fill Part 19 final test counts`.
5. End-to-end smoke (real browser): `figmaforge run --file=<fixture> --target=html+css
   --no-approval` in a temp output dir → expect exit 0, **9 artifacts**, stdout shows a
   measured `Score` > 0.9 and a `Visual verdict` line; run twice into different dirs →
   deterministic (byte-identical diff_report modulo run-id, like Parts 16–18); then a
   `--baseline` smoke with a deliberately-different PNG → score drops below 0.9 and
   `ssim_clean === false` (the honest drop, mirroring the Part 13 smoke). Clean up temp dirs.
6. `git status --short` → expect empty. Push (`git push -u origin
   feat/part-19-render-compare`) and create the PR (`gh pr create --base main --title "Part
   19: render + compare wired into the runtime — measured similarity vs baseline" --body
   "..."`). Do NOT merge — that is the user's decision, per repo convention.
