# Repair + Verify Stages — Implementation Plan (Part 20)

> **Branch:** `feat/part-20-repair-verify` (from main @ `40269c2`). See the [design spec](./2026-08-18-repair-verify-runtime-design.md) for findings, honesty contract, and risk review.
> **Gate baseline (this branch, off main):** Python **515 OK, zero skips** (43 files) · TS **131 passing**, `npx tsc` clean · `claude plugin validate --strict` ✔. Expected after: **~535 / ~150**.
>
> **BRANCH-STATE DISCOVERY (at Task 4):** main @ `40269c2` has **no Part 19 TS machinery** — no `createRenderStageHandler`/`createCompareStageHandler`, no `invokeRender*`, no `ctx.updateMetrics` seam, no render/compare wiring in `cmdRun`. Tasks 4–7 as written depend on that code, which exists only on the open PR #22 branch. **Tasks 1–3 (Python) are branch-independent and complete (536 OK). Tasks 4–7 are gated on PRs #21 (Part 18) and #22 (Part 19) merging into main first**, then this branch re-based:
>
> 1. Merge #21, then #22 into main (repo convention, merge commits).
> 2. `git checkout feat/part-20-repair-verify && git rebase main`.
> 3. Resolve the two trivial overlaps: `web_common.reference_styles_from_plan` (identical duplicate — keep the merged copy) and `pipeline.py main()` dispatch (reconcile the `repair` dispatch with #22's `_execute`/`_report_error` refactor, and reuse `_execute` instead of the standalone `repair_main` error contract if it fits).
> 4. Re-verify the Python suite (expect 536, unless #22's render tests shift it), then execute Tasks 4–7 against the real Part 19 TS state.
>
> Tasks 4–7 below are therefore written against the post-merge state; do NOT attempt them on the pre-merge branch (they would duplicate PR #22's work and force conflicts).
> **Conventions:** test-first, one commit per task after the full suite goes green, commits on `feat/part-20-repair-verify`, PR against main (no merge, repo convention). Chrome: real-chromium tests only where deterministic; unit tests inject a fake harness (Part 19 render-test convention).

## Task 1 — Pixel → color repair (Python, test-first, contained)

**Tests (red)** in `tests/test_repair_planner_color.py`:
- A pixel_mismatch candidate with `expected.region` + a synthetic baseline PNG (solid `#ff0000` region at the mismatch bbox) → planner emits a `TARGET_STYLE` patch with `property_name == "background"` and `new_value == "#ff0000"`.
- Region color averaged across a mixed region (half red / half white) → mean `#ff8080`-ish (exact byte math asserted).
- Region clamped to image bounds (region larger than the PNG).
- `baseline_png=None` → unchanged legacy behavior (`new_value` stays the region dict, `property_name` stays `"color"`) — honest degrade.
- Undecodable baseline (corrupt bytes) → legacy fallback, no raise.
- `RepairLoop.run` passes its `config.baseline_png` into the planner: an end-to-end fake-render run (reuse `test_repair_loop_raster` scaffolding: shot = baseline + red block, render_fn returns a shot that becomes the baseline once `background` is patched) reaches `threshold_satisfied` after ≥ 1 iteration and the history's patch record shows the real `#rrggbb` value.

**Implement**: `core/patch_planner.py` — `PatchPlanner(..., baseline_png=None)`; in `_determine_new_value`/`_determine_property`, for `CATEGORY_COLOR` with `expected.region` and a decodable baseline: decode via `core.png_codec.decode_png`, clamp the region, average RGB, return `#rrggbb`; property → `"background"`. `core/repair_loop.py` — pass `baseline_png=self._config.baseline_png` to the planner it constructs in `run()`.

**Verify**: new tests green; existing `test_repair_loop_raster.py`, `test_repair_classifier_pixel.py`, `test_diff_engine_ssim.py` untouched and green; full Python suite **515 + 6 = 521 OK**.
**Commit**: `fix(repair): extract real baseline region color for pixel-mismatch repair patches`.

## Task 2 — html_css styles override seam (Python, test-first)

**Tests (red)** in `tests/test_html_css_backend.py` (or the existing html_css test file):
- `generate(..., options={"styles_override": {node: {"base": {"background": "#ff0000"}, "breakpoints": {}}}})` → emitted `styles.css` contains the overridden `background: #ff0000` for that node's selector.
- Override only touches the listed node; siblings keep computed styles.
- `options` absent / empty override → **byte-identical** output to today (two runs equal).
- Override on a node with breakpoints → `breakpoints` merged.

**Implement**: `backends/html_css/__init__.py` — thread `opts` into `_apply_styles`; after `extend_ir_style`, apply `opts["styles_override"][node_id]` as a union (`base.update`, `breakpoints.update`).

**Verify**: new tests green; existing html_css determinism/golden tests unchanged and green; full Python suite.
**Commit**: `feat(html_css): apply per-node styles_override in generate for repaired styles`.

## Task 3 — `pipeline.py repair` subcommand (Python, test-first)

**Tests (red)** in `tests/test_pipeline_repair.py` (fake-harness injection, no browser — Part 19 render-test convention):
- `repair_main(argv, harness_cls=FakeHarness)` with a fake harness whose renders converge once the style is patched → one JSON line with `ok:true`, `success:true`, `final_score ≥ threshold`, `iterations_run ≥ 1`, `stop_reason: threshold_satisfied`, `repairs` with per-iteration scores, `repaired_styles` path, `generated.files` non-empty, and the **regenerated CSS contains the repaired color** (read back from `<out>/generated/html_css/`).
- Short-circuit: baseline already matches the computed reference (fake harness returns the baseline) → `iterations_run: 0`, `generated: null`, `success: true`.
- Exit codes: `--ir` without `--layout` (or vice versa) → 2; `--baseline` missing → 4; unreadable `--ir` → 4; unsupported `--backend` (e.g. `flutter`) → 2; bad `--viewport` → 2; missing Playwright → 1 with the install hint (fake-harness tests bypass this; one real-harness error test).
- Determinism: two identical runs → byte-identical stdout JSON (fake harness).
- `--no-ssim` → config `ssim_enabled=False` (assert via the report path / iteration raster stats absence).

**Implement**: `scripts/pipeline.py` — `repair` subcommand + `repair_main(argv, harness_cls=RenderHarness)` following the `render_main` pattern (`_execute`/`_report_error` shared dispatch). Steps per the spec design point 3. Reuse `reference_styles_from_plan`, `make_render_callable`, `LibraryLoader`, `RepairLoop`, and a small `styles_to_dict` helper (in `backends/web_common.py`).

**Verify**: new tests green; full Python suite **~545 OK**; one real-chromium smoke (fixture vs a color-shifted reference baseline → regenerated CSS carries the shifted color, exit 0).
**Commit**: `feat(pipeline): add repair subcommand — RepairLoop + html_css regeneration in one atomic unit`.

## Task 4 — Compare handler shares its resolved baseline (TS, test-first)  [blocked: requires #21 + #22 merged + re-base]

**Tests (red)** in `runtime/tests/backend_codegen.test.ts`:
- Existing reference-baseline compare test extended: after the compare stage, `ctx.shared` contains `compareBaseline` (an existing PNG path) and `compareBaselineKind` (`"reference"` | `"explicit"` | `"figma"`).

**Implement**: `runtime/src/core/backend_codegen.ts` — in `createCompareStageHandler`, after baseline resolution: `ctx.shared.set("compareBaseline", baseline)`; `ctx.shared.set("compareBaselineKind", baselineKind)` (additive; the `diffReport` share stays).
**Verify**: extended test green; TS suite still **141 passing**.
**Commit**: `feat(runtime): compare stage shares its resolved baseline for repair/verify`.

## Task 5 — TS repair stage handler (TS, test-first)  [blocked: requires #21 + #22 merged + re-base]

**Tests (red)** in `runtime/tests/backend_codegen.test.ts`:
- Clean-compare short-circuit: full chain on the fixture (reference baseline, score ≥ threshold) → repair artifact `{repairs: 0, note: "gate already satisfied"}`, no repair spawn (assert `Repairs`/budget untouched), run completes with 9 artifacts (8 stages + event log) — i.e., repair+verify not yet registered.
- `baseline_kind === "reference"` short-circuit note (the by-construction contract) — even when score < threshold.
- No-screenshot degrade (flutter target) → `{repairs: 0, success: null, note}`.
- **Real repair path:** full chain with `--baseline` = solid-red PNG (Part 19's explicit-baseline trick) → compare score < threshold → repair runs the real Python loop + real chromium → artifact has `iterations_run ≥ 1`, `repaired_styles` path exists, regenerated `generated/html_css` files exist and contain the repaired `background`, and **budget `repairIterations` ≥ 1** (so `Repairs:` will be real).
- `invokeRepair` error: missing baseline → typed error.

**Implement**: `runtime/src/core/backend_codegen.ts` — `invokeRepair` + `createRepairStageHandler` per spec point 4 (short-circuits first, then spawn into `<run>/repair/`, share `repairOut`/`repairManifest`/`repairStylesPath`, bump budget `repairIterations` via the existing budget update API).
**Verify**: new tests green; TS suite **~145 passing**, tsc clean; Python unchanged.
**Commit**: `feat(runtime): repair stage handler — real RepairLoop spawn with honest short-circuits`.

## Task 6 — TS verify stage handler (TS, test-first)  [blocked: requires #21 + #22 merged + re-base]

**Tests (red)** in `runtime/tests/backend_codegen.test.ts`:
- No-repair verify: full chain on the fixture (repair short-circuits) → verify artifact `{passed: true, similarity_score ≈ 1.0, threshold, baseline_kind: "reference"}`, artifact kind `metrics`.
- **Post-repair verify (the money test):** full chain with `--baseline` red PNG → repair regenerates → verify re-renders the **regenerated** files (real chromium) against the same red baseline → `similarity_score` is a number, `passed` is a boolean, and the regenerated-code score is **higher** than the pre-repair compare score (asserted with plain `assert`).
- Degraded render (flutter) → `{passed: null, note: "no measured score — cannot verify"}` — never fabricated.
- `--no-repair` path: repair short-circuits, verify still runs and reports honestly.
- `similarityScore` metric updated by verify (checkpoint reflects the final score).

**Implement**: `runtime/src/core/backend_codegen.ts` — `createVerifyStageHandler` per spec point 5 (re-render regenerated files via `invokeRender` into `<run>/verify-renders/`, compare via `ScreenshotComparator` vs `compareBaseline`, threshold from shared, store `metrics`-kind artifact, `ctx.updateMetrics({similarityScore})`).
**Verify**: new tests green; TS suite **~149 passing**, tsc clean.
**Commit**: `feat(runtime): verify stage handler — final measured pass/fail gate`.

## Task 7 — cmdRun wiring: ten stages + flags + Verification line (TS, test-first)  [blocked: requires #21 + #22 merged + re-base]

**Tests (red)** in `runtime/tests/test_all.ts` (CLI level, Part 19 conventions):
- `run --target=html+css` (fixture) → exit 0, **10 artifacts**, `Score` ≥ 0.95, `Repairs: 0`, `Visual verdict` present, **`Verification: PASSED`** in stdout.
- `run --target=html+css --baseline <red.png>` → `Repairs` ≥ 1 (parsed from stdout), `Verification:` present and reflects the measured post-repair score, exit 0.
- `run --target=html+css --no-repair --baseline <red.png>` → `Repairs: 0`, `Verification: FAILED` (honest — no repair, low score), exit 0.
- `run --target=flutter` → 10 artifacts, `Verification: cannot verify` note, exit 0.
- `--similarity-threshold 0.99` threads into verify (clean fixture with threshold above 1.0 impossible — use 0.99 on a clean run → PASSED still; threshold flag accepted and parsed).

**Implement**: `runtime/src/cli/main.ts` — register `repair`/`verify` handlers (10 stages), `--no-repair` flag → shared short-circuit, `--similarity-threshold` → shared, pass `maxRepairIterations` → repair's `--max-iterations`, `Verification:` summary line after the `Visual verdict` block (reads the `metrics`-kind verify artifact), help text.
**Verify**: new tests green; TS **~150 passing**, tsc clean; Python unchanged.
**Commit**: `feat(cli): wire repair+verify stages — 10 real stages, --no-repair/--similarity-threshold, Verification line`.

## Task 8 — Docs (README, CLAUDE.md, real-figma-demo.md, architecture.md, DEVELOPMENT_LOG)

- `docs/real-figma-demo.md` — eight-stage → **ten-stage run**; artifact list + `repair_result`/`metrics` (verify) artifacts; `--no-repair`/`--similarity-threshold` flags; `Verification:` line; repair honesty contract (reference baseline inert-by-construction; external baseline is the meaningful path); counts → 545/150 (fill from actual runs).
- `docs/DEVELOPMENT_LOG.md` — Part 20 entry: planner pixel→color fix, html_css styles_override, `repair` subcommand, TS repair/verify handlers, 10-stage run + `Verification` gate, honesty contract, counts.
- `README.md` — status header Parts 1–20, checklist lines, counts, Next Steps (drop the repair/verify wiring item).
- `CLAUDE.md` — pipeline CLI line (+`repair`), `backend_codegen.ts` line (+repair/verify handlers), test counts (files + counts), module bullet.
- `docs/architecture.md` — `pipeline.py` bullet (+repair), `backend_codegen.ts` bullet (+repair/verify handlers), status paragraph (10-stage run, measured verification).
**Commit**: `docs: document Part 20 repair+verify stages`.

## Task 9 — Final gate, push, PR (no merge)

1. Python suite (~545 OK) + `claude plugin validate --strict` in parallel.
2. `npx tsc` + TS suite (~150 passing).
3. Real CLI smoke twice: clean fixture run (10 artifacts, `Verification: PASSED`) and red-baseline run (Repairs ≥ 1, post-repair verification); dart/native degrade run.
4. Fill the DEVELOPMENT_LOG counts with the actual gate numbers (amend or follow-up commit if drifted).
5. `git push -u origin feat/part-20-repair-verify`; `gh pr create --base main` (title `feat: Part 20 — repair + verify stages, auto-repair and final verification in figmaforge run`); report open/mergeable, not merged.
