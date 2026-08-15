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
provenance-preserving, self-stabilizing via determinism) eliminates stale-baseline false
mismatches. The IR remains the immutable source of truth; the baseline remains a
supplementary signal.

**Architecture:** One real implementation, two entry points (same as Part 12). All pixel math
stays in Python: new `core/ssim.py` (stdlib-only windowed SSIM with 2×2 downsample to ≤256px
long side, integral-image style sums, pinned C1/C2 constants, crop-scoped variant);
`core/diff_engine.py` `_diff_raster` gains regional SSIM gating and returns an explicit
`clean` verdict; `core/repair_loop.py` gains opt-in baseline refresh writing versioned
sibling files; `core/pixel_diff.py` CLI emits `ssim`/`min_region_ssim`/`ssim_clean`; TS
`screenshot_compare.ts` parses them into the existing `ScreenshotComparison` interface
(optional fields, missing → null) and `cmdCompare` prints the perceptual verdict. New knobs
(`ssim_enabled`, `ssim_threshold`, `refresh_baseline`, `max_baseline_refreshes_per_run`) live
in both `RasterOptions` and `RepairConfig`, validated in `__post_init__`, covered by
`to_dict()` — same pattern as Part 12.

**Tech Stack:** Python 3 stdlib only (NO new dependencies — SSIM is hand-rolled), TypeScript
on Node.js stdlib, `unittest` for Python tests, the custom runtime test framework for TS,
git/gh for the branch → PR workflow (the final task creates the PR; it is NOT merged).

**Approved spec:** `docs/superpowers/specs/2026-08-15-ssim-baseline-refresh-design.md`
(review-hardened: fixes F1–F8 applied — small-region window handling, provenance-safe
refresh, explicit verdict return, fail-fast knob combo, pinned C1/C2, crop-scoped regional
SSIM, CLI `ssim_clean`, always-compute diagnostics).

## Contract facts (verified against source at plan-writing time)

- Branch: `feat/part-13-perceptual-diff` (already checked out; contains the approved spec
  `04c7aa4` and this plan `80122e5`).
- Baseline suite state at plan time (post-P0/P1/P2): Python `Ran 370 tests ... OK` with
  ZERO skips (via `PYTHON_BIN=/opt/homebrew/bin/python3.14`); TS `npx tsc` clean and
  `PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/tests/run_all.js` →
  `113 passing, 0 failing`. `claude plugin validate --strict plugin/figmaforge` passes.
  README/CLAUDE.md already carry the 370/113 counts.
- `core/ssim.py` does not exist; no image library exists (stdlib-only rule from Part 12).
- `core/pixel_diff.py`: `compare_images(a, b, threshold) -> (stats, mask)` and
  `detect_regions(mask, w, h, min_area) -> List[{"x","y","width","height","area"}]`
  (tight flood-fill bboxes; regions ≥ `min_region_area`=8 can be as small as 2×4) and
  `attribute_regions(regions, render_meta, root_node_id)` exist; `main(argv)` prints ONE
  JSON line `{similarity, diffPixelCount, diffPercentage, totalPixels, width, height,
  identical, meanAbsoluteError:{r,g,b}}` via `to_cli_dict()` (pixel_diff.py:73).
- `core/diff_engine.py`: `RasterOptions` (validated dataclass: `color_threshold`,
  `noise_floor`, `min_region_area`, `pixel_weight`); `DiffEngine.diff(...,
  raster_options=None)` composes `(1-pixel_weight)*structural + pixel_weight*pixels_category`;
  `_diff_raster` (lines ~152–230): decode → size check → `compare_images` →
  `detect_regions` → `attribute_regions` → `raster_stats {mae, diff_percentage,
  region_count}` → returns `(mismatches, raster_stats, diff_ratio)`. Returns
  `([], None, 0.0)` on undecodable inputs; size mismatch returns one `pixel_mismatch`
  with `reason: size_mismatch` and `diff_ratio = 1.0`. `_diff_raster` is private with a
  single caller (`diff()`) — its return arity may change (F3: becomes a 4-tuple with an
  explicit `clean` verdict).
- `core/repair_loop.py`: `RepairConfig` (line 58) mirrors the Part 12 knobs and validates
  via `RasterOptions` in `__post_init__`; `to_dict()` includes them. `run()` loop:
  render → `diff_engine.diff(...)` (passes `baseline_png=self._config.baseline_png`) →
  classify → plan → record `IterationRecord` (`diff_report.to_dict()`) → check stopping →
  approval → execute. `_raster_options()` (line ~245) builds `RasterOptions` from config.
  `_default_render` is a stub; tests use a fake `render_fn(plan, styles, document,
  iteration) -> (render_meta, screenshot_path)`.
- TS `runtime/src/core/screenshot_compare.ts`: `compareBuffers`/`compareFiles` shell out to
  `python3 -m core.pixel_diff` with the SHA-256 hash fast-path; `parsePixelDiffOutput`
  exported; `ScreenshotComparison` interface preserved. `runtime/src/cli/main.ts`:
  `cmdCompare` accepts `--baseline <path.png>`, prints similarity/diff-pixel stats.
- `core/asset_manager.py`: `AssetManager.ingest(raw_data, original_url, kind, extension)`
  → SHA-256 content-addressed storage + `manifest.json`; dedup by hash (re-ingest of
  identical bytes is a no-op change to the manifest). The refresh mechanism writes plain
  sibling files next to `baseline_png` — it does NOT need the AssetManager and must NOT
  overwrite the original baseline file.

## Task 1: `core/ssim.py` — stdlib SSIM (TDD)

**Test file:** `tests/test_ssim.py` (new).

Steps:

1. Write `tests/test_ssim.py` covering, using `png_codec.encode_png` to build fixtures at
   test time (deterministic patterns, NO randomness):
   - `test_identical_images_ssim_1_0` — two byte-identical RGBA images → `ssim(...) == 1.0`
     (exact equality — identical inputs must yield exactly 1.0).
   - `test_modest_uniform_shift_high_ssim` — one image uniformly brightened by a **modest**
     constant offset (+20 on a mid-gray base; verified by hand: scores ≈0.99 at gray 128,
     while +100 would score ≈0.85 and must NOT be asserted ≥0.95 — F5) → `ssim >= 0.95`.
   - `test_shift_scores_above_structural_change` — **relative ordering**: a uniform shift
     and a same-pixel-count structural change (checkerboard) → `ssim(shift) >
     ssim(structural)` by a wide margin (the perceptual property SSIM exists for).
   - `test_localized_change_low_regional_high_global` — copy of an image with a small
     solid-color block changed in the center → global SSIM ≥ 0.9 BUT the SSIM computed over
     the changed block's bbox < 0.5 (the regional-vs-global distinction the whole feature
     depends on; assert BOTH directions).
   - `test_structured_noise_low_ssim` — every-other-pixel checkerboard on one image →
     `ssim < 0.5`.
   - `test_size_mismatch_raises` — different dimensions → `ValueError`.
   - `test_sub_window_image_raises` — an image with a dimension < `window` (e.g. 4×400)
     → `ValueError` (callers must treat unmeasurable regions as real, never clean).
   - `test_crop_scoped_ssim` — the crop variant computed on a region bbox equals the
     full-image call on a same-size image (proves the crop path uses the same math).
   - `test_downsampled_large_input_bounded` — a large image (1200×800) completes and
     yields the same verdict as the same content at small size (identical → 1.0).
2. Run `PYTHON_BIN=/opt/homebrew/bin/python3.14 python3 -m unittest tests.test_ssim -v`
   → **expect FAIL** (ImportError: no module `core.ssim`). Red confirmed.
3. Implement `core/ssim.py`: `ssim(a, b, window: int = 8) -> float` — convert RGBA to
   luminance (per-channel average), 2×2 average-downsample both until long side ≤ 256,
   integral-image style sums for local means/variances/covariance over sliding `window`
   squares, `C1 = (0.01 * 255) ** 2`, `C2 = (0.03 * 255) ** 2`, clamp to [-1, 1], return
   mean SSIM. `ValueError` on size mismatch after downsampling AND on any dimension <
   `window`. Also export a crop-scoped helper (accepts already-decoded pixel buffers plus
   a bbox `(x, y, w, h)` → applies the same window math to that region; pads/parses only
   the crop so integral-image cost is O(bbox)). Docstring documents the algorithm, the
   constants, and the deterministic guarantee.
4. Run the test again → **expect PASS**. Run the full suite
   `PYTHON_BIN=/opt/homebrew/bin/python3.14 python3 -m unittest discover -s tests`
   → **expect `Ran 37X tests ... OK`, ZERO skips** (X = previous count + 9).
5. Commit: `git add core/ssim.py tests/test_ssim.py && git commit -m "feat: add stdlib SSIM (core/ssim.py) with downsample-first windowed computation"`.

## Task 2: Regional SSIM gating in `_diff_raster` + new knobs (TDD)

**Test file:** `tests/test_diff_engine_ssim.py` (new).

Steps:

1. Extend `core/diff_engine.py` `RasterOptions` with `ssim_enabled: bool = True` and
   `ssim_threshold: float = 0.95`; validate both (`0.0 <= ssim_threshold <= 1.0`).
2. Extend `core/repair_loop.py` `RepairConfig` with the same two knobs + `refresh_baseline:
   bool = False` and `max_baseline_refreshes_per_run: int = 3`; `__post_init__` passes the
   new raster knobs into `RasterOptions` AND rejects `refresh_baseline=True` with
   `ssim_enabled=False` (`ValueError` — F4 fail-fast); `to_dict()` includes all four.
3. Change `_diff_raster` to return `(mismatches, raster_stats, diff_ratio, clean)` — the
   explicit verdict (F3). `diff()` branches on `clean` for `pixel_score` instead of
   sniffing the stats dict; the size-mismatch and undecodable paths return `clean=False`.
4. Write `tests/test_diff_engine_ssim.py`:
   - `test_aa_noise_suppressed_by_ssim` — base image and a "noise" twin differing only by
     sub-threshold per-pixel jitter (deterministic pattern, e.g. +3 on alternate pixels of
     one channel — enough to exceed `color_threshold` where jitter accumulates but
     perceptually invisible); render_meta with one node covering the page; expect
     `diff()` with `ssim_enabled=True` → `pixels == 1.0`, `raster_stats["ssim"]` present,
     `raster_stats["ssim_clean"] is True`, and ZERO `pixel_mismatch` entries.
   - `test_real_localized_change_still_reported` — change a solid block (real color change
     like the smoke test) → `pixels < 1.0`, ≥ 1 `pixel_mismatch` attributed to the node
     whose bbox overlaps the block, `raster_stats["min_region_ssim"] < 0.5`,
     `ssim_clean is False`.
   - `test_global_ssim_high_but_regional_low` — large image, tiny but strong change →
     `raster_stats["ssim"] >= 0.9` yet `min_region_ssim < 0.5` and mismatches emitted
     (the case a global-only design would miss).
   - `test_tiny_region_never_suppressed` — a 2×4 diff region (≥ min_region_area but
     unmeasurable by an 8×8 window; place it away from edges) → treated as real:
     mismatch emitted even though surrounding content is identical (F1 conservative rule).
   - `test_edge_region_never_suppressed` — a diff region at the image edge where clamping
     cannot fit the window → same conservative rule: mismatch emitted.
   - `test_sub_floor_diff_still_records_ssim` — a diff below `noise_floor` (tiny change) →
     clean, `pixels == 1.0`, but `raster_stats["ssim"]` IS present (F8 always-compute).
   - `test_ssim_disabled_preserves_part12` — same noise twin with `ssim_enabled=False` →
     pixel mismatches ARE emitted exactly as Part 12 would; no `ssim` keys in
     `raster_stats` (fallback knob restores prior behavior).
   - `test_size_mismatch_unchanged` — different-sized screenshot/baseline → single
     `size_mismatch` pixel_mismatch, `diff_ratio == 1.0`, `clean is False`, no SSIM keys.
   - `test_raster_stats_carries_ssim` — any enabled raster run includes `"ssim"` (float)
     and, when regions exist, `"min_region_ssim"` (float) and `"ssim_clean"` (bool).
   - `test_knob_validation` — `RasterOptions(ssim_threshold=1.5)` raises `ValueError`;
     `RepairConfig(ssim_threshold=-1)` raises `ValueError`;
     `RepairConfig(refresh_baseline=True, ssim_enabled=False)` raises `ValueError` (F4).
5. Run targeted → **expect FAIL** (knobs don't exist yet — AttributeError/TypeError; the
   4-tuple return is also missing). Red.
6. Implement: in `_diff_raster`, after `detect_regions`:
   - If `options.ssim_enabled` and sizes matched: compute global `ssim` (downsampled) —
     ALWAYS, even sub-floor — record in `raster_stats["ssim"]`.
   - If `diff_ratio > options.noise_floor` and `ssim_enabled`: for each region, grow the
     bbox to `window × window` (clamped to image bounds) and compute SSIM over the grown
     bbox at full res via the crop helper. If clamping cannot fit the window → no verdict
     for that region → `clean = False`. `min_region_ssim = min` over regions WITH verdicts.
     `clean = True` iff every region has a verdict and `min_region_ssim >=
     options.ssim_threshold`; zero regions → `clean = global_ssim >= options.ssim_threshold`.
   - Clean → emit NO raster mismatches, return `([], stats, diff_ratio, True)` — `diff()`
     sets `pixel_score = 1.0` when `clean`, regardless of `diff_ratio`.
   - Not clean → emit attributed mismatches exactly as Part 12, return
     `(mismatches, stats, diff_ratio, False)`.
   - `raster_stats` gains `"ssim"`, `"min_region_ssim"` (float | None), `"ssim_clean"`
     (bool). `ssim_enabled=False` → exact Part 12 path, no SSIM keys, `clean` = whether
     `diff_ratio <= noise_floor` (so the always-clean sub-floor case still returns True).
7. Run targeted → **expect PASS**; full suite → **expect `Ran 37X tests ... OK`, zero skips**.
8. Commit: `git add core/diff_engine.py core/repair_loop.py tests/test_diff_engine_ssim.py && git commit -m "feat: regional SSIM gating in _diff_raster with ssim_enabled/ssim_threshold knobs"`.

## Task 3: Baseline auto-refresh in `repair_loop` (TDD)

**Test file:** `tests/test_repair_loop_refresh.py` (new).

Steps:

1. Write `tests/test_repair_loop_refresh.py` with a fake `render_fn` that returns a
   screenshot path whose bytes differ from the baseline by deterministic sub-threshold
   jitter (clean per SSIM) OR by a real block change (not clean), on a switch:
   - `test_refresh_disabled_by_default` — `RepairConfig(refresh_baseline=False)` (default),
     clean renders → NO `.refreshed.*` files appear; the original `baseline_png` bytes are
     UNCHANGED after `run()`; iteration records contain no `baseline_refreshed` flag.
     (Preserves Part 12 semantics.)
   - `test_clean_render_adopts_new_baseline` — `refresh_baseline=True`, clean render →
     after `run()`: a versioned sibling `<stem>.refreshed.0.png` exists with the render's
     bytes; `config.baseline_png` now points at it; **the original baseline file is
     byte-identical before and after** (F2 provenance); the adoption iteration records
     `baseline_refreshed: true` (and the new path); subsequent iterations' diffs run
     against the new baseline.
   - `test_regression_never_refreshed` — render differs by a real block change (low region
     SSIM) → no `.refreshed.*` files, original baseline unchanged, even with
     `refresh_baseline=True`.
   - `test_refresh_bounded_and_versioned` — a render_fn whose output changes every
     iteration (still clean each time, e.g. jitter amplitude drifts) → refresh happens at
     most `max_baseline_refreshes_per_run` (default 3) times; files are versioned
     `.refreshed.0/1/2.png` with distinct content; `config.baseline_png` ends at the
     newest.
   - `test_size_mismatch_never_refreshes` — screenshot with different dimensions → no
     refresh, normal `size_mismatch` handling.
   - `test_no_churn_on_identical_renders` — render bytes identical to baseline → zero
     refreshes, zero files.
2. Run targeted → **expect FAIL** (knobs exist from Task 2 but refresh logic doesn't — the
   `.refreshed.*` and `baseline_refreshed` assertions fail). Red.
3. Implement in `repair_loop.py` `run()`:
   - Track `refreshes = 0` before the loop.
   - After `diff_engine.diff(...)` and before classify: if `self._config.refresh_baseline`
     and `refreshes < self._config.max_baseline_refreshes_per_run` and
     `screenshot_path` and `self._config.baseline_png` and
     `diff_report.raster_stats is not None` and
     `diff_report.raster_stats.get("ssim_clean") is True` → write the render's bytes to
     `Path(baseline_png).with_name(f"{stem}.refreshed.{refreshes}{suffix}")`, set
     `self._config.baseline_png = str(new_path)`, `refreshes += 1`, set
     `diff_report.raster_stats["baseline_refreshed"] = True` and
     `diff_report.raster_stats["baseline_new_path"] = str(new_path)`. The original
     baseline file is NEVER written to (F2).
   - Guard ordering: `ssim_clean` is True only on the clean path; the `size_mismatch`
     report returns `clean=False` and no `ssim_clean` key → never refreshes.
4. Run targeted → **expect PASS**; full suite → **expect `Ran 37X tests ... OK`, zero skips**.
5. Commit: `git add core/repair_loop.py tests/test_repair_loop_refresh.py && git commit -m "feat: opt-in baseline auto-refresh of clean renders in repair loop"`.

## Task 4: `pixel_diff` CLI + TS wiring (TDD)

**Test files:** extend `runtime/tests/test_all.ts`; `core/pixel_diff.py` gains args.

Steps:

1. Extend `core/pixel_diff.py` `main()` with `--ssim-threshold` (default 0.95) and add
   `"ssim": float | null`, `"min_region_ssim": float | null`, and `"ssim_clean": bool |
   null` to `to_cli_dict()` output (computed when both images decode and sizes match; null
   otherwise; reuse the same gating rule as `_diff_raster` — global when zero regions,
   else min over grown bboxes, F1 rule included). Keep the one-JSON-line contract and the
   hash fast-path untouched.
2. Update `runtime/src/core/screenshot_compare.ts`: `ScreenshotComparison` gains optional
   `ssim?: number | null`, `minRegionSsim?: number | null`, `ssimClean?: boolean | null`;
   `parsePixelDiffOutput` parses the new keys (missing → null, backward compatible with
   old output).
3. Write TS tests in `runtime/tests/test_all.ts`:
   - comparator against real PNGs (existing shell-out helpers): identical → `ssim == 1`,
     `ssimClean == true`; a real localized change → `ssim >= 0.9`,
     `minRegionSsim < 0.95`, `ssimClean == false`, similarity reported as before.
   - parse tests: JSON with the new keys → typed fields; JSON without them → `null`
     (old output parses).
   - `cmdCompare` on two real PNGs prints a line containing the SSIM verdict (e.g.
     `Perceptually identical` / `SSIM`) via captured stdout, following the existing
     `cmdCompare` test pattern (F7).
4. Run `npx tsc` → **expect FAIL** (interface fields missing). Red.
5. Implement steps 1–2; run `npx tsc` → clean; run
   `PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/tests/run_all.js`
   → **expect `11X passing, 0 failing`** (X = previous + ~5). Also run the Python suite →
   `Ran 37X tests ... OK` (update any pixel_diff CLI test that asserts the exact JSON
   shape to include the new keys).
6. Commit: `git add core/pixel_diff.py runtime/src/core/screenshot_compare.ts runtime/tests/test_all.ts && git commit -m "feat: expose SSIM and clean verdict via pixel_diff CLI and TS comparator"`.

## Task 5: Docs — repair-loop.md, DEVELOPMENT_LOG.md, README/CLAUDE.md

Steps:

1. `docs/repair-loop.md`: add a "Perceptual diffing (SSIM)" subsection (regional gating
   explanation incl. the small-region conservative rule and the always-computed diagnostic,
   knob table: `ssim_enabled`, `ssim_threshold`) and a "Baseline auto-refresh" subsection
   (opt-in via `refresh_baseline`, clean-verdict-only guard, `max_baseline_refreshes_per_run`,
   versioned sibling files + original-baseline provenance, self-stabilizing via determinism;
   note the default keeps Part 12's manual/explicit behavior).
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
   compare with `--baseline`; confirm the output now prints the SSIM verdict and the
   broken case still reports a similarity drop. (Optional but recommended; uses only
   `/tmp` artifacts.)
6. `git status --short` → expect empty (or only pre-approved noise). Push the branch
   (`git push -u origin feat/part-13-perceptual-diff`) and create the PR
   (`gh pr create --base main --title "Part 13: perceptual diffing (SSIM) + baseline auto-refresh" --body "..."`).
   Do NOT merge — that is the user's decision, per repo convention.
