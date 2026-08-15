# Perceptual Diffing (SSIM) + Baseline Auto-Refresh (Part 13) Implementation Plan

> **For agentic workers:** This plan is written for `superpowers:subagent-driven-development`.
> Execute it task-by-task with a fresh subagent per task; each task is a self-contained
> TDD cycle (write failing test → run and expect FAIL → minimal implementation → run and
> expect PASS → commit). Never batch tasks, never skip the failing-test step, and verify
> the exact expected output shown in each step before committing. Python commands run from
> `plugin/figmaforge` unless stated otherwise; TypeScript commands run from the repo root.
> `pytest` is NOT installed — always use `python3 -m unittest discover -s tests` (full
> suite) or `python3 -m unittest tests.test_X -v` (targeted). **This machine's default
> `python3` is 3.9.6 and cannot run this codebase (PEP 604 annotations)** — run every
> Python command with `PYTHON_BIN=/opt/homebrew/bin/python3.14` (the runtime CLI honors
> `PYTHON_BIN` since the P0 fix; the test suite must be run with that interpreter).

**Goal:** Give the repair loop a perceptual verdict that distinguishes "antialiasing/font
noise" from "real localized change," and let it safely adopt byte-different-but-clean renders
as the new baseline. SSIM (structural similarity, pure-stdlib, downsample-first) gates the
per-pixel region output region-by-region; auto-refresh (opt-in, clean-verdict-only, bounded,
self-stabilizing via content-addressing) eliminates stale-baseline false mismatches. The IR
remains the immutable source of truth; the baseline remains a supplementary signal.

**Architecture:** One real implementation, two entry points (same as Part 12). All pixel math
stays in Python: new `core/ssim.py` (stdlib-only windowed SSIM with 2×2 downsample to ≤256px
long side, integral-image style sums); `core/diff_engine.py` `_diff_raster` gains regional
SSIM gating; `core/repair_loop.py` gains opt-in baseline refresh; `core/pixel_diff.py` CLI
emits `ssim`/`min_region_ssim`; TS `screenshot_compare.ts` parses them into the existing
`ScreenshotComparison` interface (optional fields, missing → null) and `cmdCompare` prints
SSIM. New knobs (`ssim_enabled`, `ssim_threshold`, `refresh_baseline`,
`max_baseline_refreshes_per_run`) live in both `RasterOptions` and `RepairConfig`, validated
in `__post_init__`, covered by `to_dict()` — same pattern as Part 12.

**Tech Stack:** Python 3 stdlib only (NO new dependencies — SSIM is hand-rolled), TypeScript
on Node.js stdlib, `unittest` for Python tests, the custom runtime test framework for TS,
git/gh for the branch → PR workflow (the final task creates the PR; it is NOT merged).

**Approved spec:** `docs/superpowers/specs/2026-08-15-ssim-baseline-refresh-design.md`

## Contract facts (verified against source at plan-writing time)

- Branch: `feat/part-13-perceptual-diff` (already checked out; contains the approved spec,
  committed `04c7aa4`).
- Baseline suite state at plan time (post-P0/P1/P2): Python `Ran 370 tests ... OK` with
  ZERO skips (via `PYTHON_BIN=/opt/homebrew/bin/python3.14`); TS `npx tsc` clean and
  `PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/tests/run_all.js` →
  `113 passing, 0 failing`. `claude plugin validate --strict plugin/figmaforge` passes.
  README/CLAUDE.md already carry the 370/113 counts.
- `core/ssim.py` does not exist; no image library exists (stdlib-only rule from Part 12).
- `core/pixel_diff.py`: `compare_images(a, b, threshold) -> (stats, mask)` and
  `detect_regions(mask, w, h, min_area)` exist; `main(argv)` prints ONE JSON line
  `{similarity, diffPixelCount, diffPercentage, totalPixels, width, height, identical,
  meanAbsoluteError:{r,g,b}}` (result dict built around pixel_diff.py:73).
- `core/diff_engine.py`: `RasterOptions` (validated dataclass: `color_threshold`,
  `noise_floor`, `min_region_area`, `pixel_weight`); `DiffEngine.diff(...,
  raster_options=None)` composes `(1-pixel_weight)*structural + pixel_weight*pixels_category`;
  `_diff_raster` (lines ~152–230): decode → size check → `compare_images` →
  `detect_regions` → `attribute_regions` → `raster_stats {mae, diff_percentage,
  region_count}` → returns `(mismatches, raster_stats, diff_ratio)`. Returns
  `([], None, 0.0)` on undecodable inputs; size mismatch returns one `pixel_mismatch`
  with `reason: size_mismatch` and `diff_ratio = 1.0`.
- `core/repair_loop.py`: `RepairConfig` (line 58) mirrors the Part 12 knobs and validates
  via `RasterOptions` in `__post_init__`; `to_dict()` includes them. `run()` loop:
  render → `diff_engine.diff(...)` → classify → plan → record `IterationRecord`
  (`diff_report.to_dict()`) → check stopping → approval → execute. `_raster_options()`
  (line ~245) builds `RasterOptions` from config. `_default_render` is a stub; tests use a
  fake `render_fn(plan, styles, document, iteration) -> (render_meta, screenshot_path)`.
- TS `runtime/src/core/screenshot_compare.ts`: `compareBuffers`/`compareFiles` shell out to
  `python3 -m core.pixel_diff` with the SHA-256 hash fast-path; `parsePixelDiffOutput`
  exported; `ScreenshotComparison` interface preserved. `runtime/src/cli/main.ts`:
  `cmdCompare` accepts `--baseline <path.png>`, prints similarity/diff-pixel stats.
- `core/asset_manager.py`: `AssetManager.ingest(raw_data, original_url, kind, extension)`
  → SHA-256 content-addressed storage + `manifest.json`; dedup by hash (re-ingest of
  identical bytes is a no-op change to the manifest).

## Task 1: `core/ssim.py` — stdlib SSIM (TDD)

**Test file:** `tests/test_ssim.py` (new).

Steps:

1. Write `tests/test_ssim.py` covering, using `png_codec.encode_png` to build fixtures at
   test time (deterministic patterns, NO randomness):
   - `test_identical_images_ssim_1_0` — two byte-identical RGBA images → `ssim(...) == 1.0`
     (exact equality — identical inputs must yield exactly 1.0).
   - `test_uniform_shift_high_ssim` — one image uniformly brightened by a constant offset →
     `ssim >= 0.95` (perceptually same content, luminance shift only).
   - `test_localized_change_low_regional_high_global` — copy of an image with a small
     solid-color block changed in the center → global SSIM ≥ 0.9 BUT the SSIM computed over
     the changed block's bbox < 0.5 (this is the regional-vs-global distinction the whole
     feature depends on; assert BOTH directions).
   - `test_structured_noise_low_ssim` — every-other-pixel checkerboard on one image →
     `ssim < 0.5` (structural difference is not "perceptual sameness").
   - `test_size_mismatch_raises` — different dimensions → `ValueError` (callers guard on
     size before calling; the function itself fails loudly rather than silently misalign).
   - `test_downsampled_large_input_bounded` — a large image (e.g. 1200×800) completes and
     yields the same verdict as the same content at small size (identical → 1.0).
2. Run `PYTHON_BIN=/opt/homebrew/bin/python3.14 python3 -m unittest tests.test_ssim -v`
   → **expect FAIL** (ImportError: no module `core.ssim`). Red confirmed.
3. Implement `core/ssim.py`: `ssim(a, b, window: int = 8) -> float` — convert RGBA to
   luminance (per-channel average), 2×2 average-downsample both until long side ≤ 256,
   integral-image style sums for local means/variances/covariance over sliding `window`
   squares, clamp to [-1, 1], return mean SSIM. `ValueError` on size mismatch after
   downsampling. Docstring documents the algorithm and the deterministic guarantee.
4. Run the test again → **expect PASS**. Run the full suite
   `PYTHON_BIN=/opt/homebrew/bin/python3.14 python3 -m unittest discover -s tests`
   → **expect `Ran 37X tests ... OK`, ZERO skips** (X = previous count + 6).
5. Commit: `git add core/ssim.py tests/test_ssim.py && git commit -m "feat: add stdlib SSIM (core/ssim.py) with downsample-first windowed computation"`.

## Task 2: Regional SSIM gating in `_diff_raster` + new knobs (TDD)

**Test file:** `tests/test_diff_engine_ssim.py` (new).

Steps:

1. Extend `core/diff_engine.py` `RasterOptions` with `ssim_enabled: bool = True` and
   `ssim_threshold: float = 0.95`; validate both (`0.0 <= ssim_threshold <= 1.0`).
2. Extend `core/repair_loop.py` `RepairConfig` with the same two knobs + `refresh_baseline:
   bool = False` and `max_baseline_refreshes_per_run: int = 3`; `__post_init__` passes the
   new raster knobs into `RasterOptions`; `to_dict()` includes all four.
3. Write `tests/test_diff_engine_ssim.py`:
   - `test_aa_noise_suppressed_by_ssim` — build a base image and a "noise" twin differing
     only by sub-threshold per-pixel jitter (deterministic pattern, e.g. +3 on alternate
     pixels of one channel — enough to exceed `color_threshold` where jitter accumulates
     but perceptually invisible); render_meta with one node covering the page; expect
     `diff()` with `ssim_enabled=True` to return `pixels == 1.0`, `raster_stats["ssim"]`
     present, and ZERO `pixel_mismatch` entries.
   - `test_real_localized_change_still_reported` — change a solid block (real color change
     like the smoke test) → `pixels < 1.0`, ≥ 1 `pixel_mismatch` attributed to the node
     whose bbox overlaps the block, `raster_stats["min_region_ssim"] < 0.5`.
   - `test_ssim_disabled_preserves_part12` — same noise twin with `ssim_enabled=False` →
     pixel mismatches ARE emitted exactly as Part 12 would (proves the fallback knob
     restores prior behavior).
   - `test_global_ssim_high_but_regional_low` — construct a case where a global-mean SSIM
     would pass but regional gating must not: large image, tiny but strong change →
     `raster_stats["ssim"] >= 0.9` yet `min_region_ssim < 0.5` and mismatches emitted.
   - `test_size_mismatch_unchanged` — different-sized screenshot/baseline → single
     `size_mismatch` pixel_mismatch, `diff_ratio == 1.0`, no SSIM keys (never computes SSIM
     on misaligned sizes).
   - `test_raster_stats_carries_ssim` — any raster run includes `"ssim"` (float) and,
     when regions exist, `"min_region_ssim"` (float) in `raster_stats`.
   - `test_knob_validation` — `RasterOptions(ssim_threshold=1.5)` raises `ValueError`;
     `RepairConfig(ssim_threshold=-1)` raises `ValueError`.
4. Run targeted → **expect FAIL** (knobs don't exist yet — AttributeError/TypeError). Red.
5. Implement: in `_diff_raster`, after `detect_regions`:
   - If `options.ssim_enabled` and sizes matched and `diff_ratio > options.noise_floor`:
     decode both images' pixel buffers (already decoded), compute global `ssim`; for each
     detected region compute `ssim` over its bbox; `min_region_ssim = min` (or global when
     zero regions). If `min_region_ssim >= options.ssim_threshold` → **clean verdict**:
     emit NO pixel mismatches, `pixel_score = 1.0` (return `([], stats, 0.0)`-style — but
     keep `diff_ratio` in stats for diagnostics; the returned ratio must be ≤ noise_floor
     so `diff()`'s `pixel_score = 1.0` branch holds — simplest: return the real
     `diff_ratio` and have `diff()` treat the clean flag via `raster_stats["ssim_clean"]`).
     Decide the mechanism in code: add `"ssim_clean": bool` to `raster_stats` and make
     `diff()` set `pixel_score = 1.0` and skip `raster_mismatches` when `ssim_clean` is
     True, regardless of `diff_ratio`.
   - Always add `"ssim"` (float) and `"min_region_ssim"` (float | None) to `raster_stats`.
   - `ssim_enabled=False` → exact Part 12 path, no SSIM keys.
6. Run targeted → **expect PASS**; full suite → **expect `Ran 37X tests ... OK`, zero skips**.
7. Commit: `git add core/diff_engine.py core/repair_loop.py tests/test_diff_engine_ssim.py && git commit -m "feat: regional SSIM gating in _diff_raster with ssim_enabled/ssim_threshold knobs"`.

## Task 3: Baseline auto-refresh in `repair_loop` (TDD)

**Test file:** `tests/test_repair_loop_refresh.py` (new).

Steps:

1. Write `tests/test_repair_loop_refresh.py` with a fake `render_fn` that returns a
   screenshot path whose bytes differ from the baseline by deterministic sub-threshold
   jitter (clean per SSIM) OR by a real block change (not clean), on a switch:
   - `test_refresh_disabled_by_default` — `RepairConfig(refresh_baseline=False)` (default),
     clean renders → `baseline_png` file bytes UNCHANGED after `run()`; iteration records
     contain no `baseline_refreshed` flag. (Preserves Part 12 semantics.)
   - `test_clean_render_adopts_new_baseline` — `refresh_baseline=True`, clean render →
     after `run()`, `baseline_png` bytes == the render's bytes; the iteration where
     adoption happened has `diff_report["raster_stats"]["baseline_refreshed"] == True`
     (or a top-level `baseline_refreshed` flag — decide and assert one); subsequent
     iterations' diffs run against the new baseline.
   - `test_regression_never_refreshed` — render differs by a real block change (low region
     SSIM) → baseline UNCHANGED even with `refresh_baseline=True`.
   - `test_refresh_bounded` — a render_fn whose output changes every iteration (still
     clean each time, e.g. jitter amplitude drifts) → refresh happens at most
     `max_baseline_refreshes_per_run` (default 3) times across the run.
   - `test_size_mismatch_never_refreshes` — screenshot with different dimensions → no
     refresh, normal `size_mismatch` handling.
   - `test_no_churn_on_identical_renders` — render bytes identical to baseline → hash
     fast-path: zero refreshes, zero manifest churn.
2. Run targeted → **expect FAIL** (knobs exist from Task 2 but refresh logic doesn't — the
   `baseline_refreshed` assertions fail). Red.
3. Implement in `repair_loop.py` `run()`:
   - Track `refreshes = 0` before the loop.
   - After `diff_engine.diff(...)` and before classify: if `self._config.refresh_baseline`
     and `refreshes < self._config.max_baseline_refreshes_per_run` and
     `screenshot_path` and `self._config.baseline_png` and
     `diff_report.raster_stats` is not None and
     `diff_report.raster_stats.get("ssim_clean") is True` → atomically replace
     `baseline_png` with the render bytes (`os.replace` via a temp file), `refreshes += 1`,
     set `diff_report.raster_stats["baseline_refreshed"] = True`.
   - Guard ordering matters: `ssim_clean` only exists on the clean path; a `size_mismatch`
     report has `raster_stats` but no `ssim_clean` key → never refreshes.
4. Run targeted → **expect PASS**; full suite → **expect `Ran 37X tests ... OK`, zero skips**.
5. Commit: `git add core/repair_loop.py tests/test_repair_loop_refresh.py && git commit -m "feat: opt-in baseline auto-refresh of clean renders in repair loop"`.

## Task 4: `pixel_diff` CLI + TS wiring (TDD)

**Test files:** extend `runtime/tests/test_all.ts`; `core/pixel_diff.py` gains args.

Steps:

1. Extend `core/pixel_diff.py` `main()` with `--ssim-threshold` (default 0.95) and add
   `"ssim": float | null` and `"min_region_ssim": float | null` to the output JSON
   (computed when both images decode and sizes match; null otherwise; use the same
   gating rule as `_diff_raster` — global when zero regions, else min over regions).
   Keep the one-JSON-line contract and the hash fast-path untouched.
2. Update `runtime/src/core/screenshot_compare.ts`: `ScreenshotComparison` gains optional
   `ssim?: number | null` and `minRegionSsim?: number | null`; `parsePixelDiffOutput`
   parses the two new keys (missing → null, backward compatible with old output).
3. Write TS tests in `runtime/tests/test_all.ts`:
   - comparator against real PNGs (existing shell-out helpers): identical → `ssim == 1`;
     a real localized change → `ssim >= 0.9`, `minRegionSsim < 0.95`, similarity
     reported as before.
   - parse tests: JSON with `ssim`/`min_region_ssim` keys → typed fields; JSON without
     them → `null` (old output parses).
   - `cmdCompare` on two real PNGs prints a line containing `SSIM` (via captured stdout
     of the CLI, following the existing `cmdCompare` test pattern).
4. Run `npx tsc` → **expect FAIL** (interface fields missing). Red.
5. Implement steps 1–2; run `npx tsc` → clean; run
   `PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/tests/run_all.js`
   → **expect `11X passing, 0 failing`** (X = previous + ~4). Also run the Python suite →
   `Ran 37X tests ... OK` (pixel_diff CLI tests updated for new keys if they assert exact
   JSON — check `tests/test_pixel_diff_cli.py`-style files and update assertions to include
   the new keys).
6. Commit: `git add core/pixel_diff.py runtime/src/core/screenshot_compare.ts runtime/tests/test_all.ts && git commit -m "feat: expose SSIM via pixel_diff CLI and TS comparator"`.

## Task 5: Docs — repair-loop.md, DEVELOPMENT_LOG.md, README/CLAUDE.md

Steps:

1. `docs/repair-loop.md`: add a "Perceptual diffing (SSIM)" subsection (regional gating
   explanation, knob table: `ssim_enabled`, `ssim_threshold`) and a "Baseline auto-refresh"
   subsection (opt-in via `refresh_baseline`, clean-verdict-only guard,
   `max_baseline_refreshes_per_run`, self-stabilizing via content-addressing; note the
   default keeps Part 12's manual/explicit behavior).
2. `docs/DEVELOPMENT_LOG.md`: append a Part 13 entry (with FILL markers for final counts)
   describing SSIM + auto-refresh; correct any earlier line that implies diffing is
   pixel-count-only if one exists.
3. `README.md`: move "baseline auto-refresh / perceptual metrics" from Next Steps into a
   delivered Part 13 status line; update the status header to `Parts 1–13 complete`.
4. `CLAUDE.md`: update diffing-related lines (test counts become the new totals from Task 6
   or keep 370/113 + note the new tests; do not claim counts before Task 6 runs).
5. Commit: `git add docs/repair-loop.md docs/DEVELOPMENT_LOG.md README.md CLAUDE.md && git commit -m "docs: document SSIM gating and baseline auto-refresh (Part 13)"`.

## Task 6: Final verification gate + PR (do NOT merge)

Steps:

1. Python full suite with `PYTHON_BIN=/opt/homebrew/bin/python3.14 python3 -m unittest
   discover -s tests` → **expect `Ran N tests ... OK`, ZERO skips** (N = 370 + new tests).
2. TS: `npx tsc` clean, then `PYTHON_BIN=/opt/homebrew/bin/python3.14 node
   dist/runtime/tests/run_all.js` → **expect `M passing, 0 failing`**.
3. `claude plugin validate --strict plugin/figmaforge` → **expect ✔ Validation passed**.
4. Fill the DEVELOPMENT_LOG Part 13 counts with the actual N/M from steps 1–2; amend the
   docs commit (`git add docs/DEVELOPMENT_LOG.md && git commit --amend --no-edit` after
   confirming the last commit is the docs commit — if not, make a follow-up commit
   `docs: fill Part 13 final test counts`).
5. Smoke: reuse the P0 pixel-diff smoke pattern — render golden, break a small block,
   compare with `--baseline`; confirm the output now prints SSIM and the broken case still
   reports a similarity drop. (Optional but recommended; uses only `/tmp` artifacts.)
6. `git status --short` → expect empty (or only pre-approved noise). Push the branch
   (`git push -u origin feat/part-13-perceptual-diff`) and create the PR
   (`gh pr create --base main --title "Part 13: perceptual diffing (SSIM) + baseline auto-refresh" --body "..."`).
   Do NOT merge — that is the user's decision, per repo convention.
