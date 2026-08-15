# Perceptual Diffing (SSIM) + Baseline Auto-Refresh (Part 13) — Design Spec

## Context

- Part 12 (merged, PR #13) delivered real raster diffing: `png_codec.py` (stdlib decode/encode), `figma_assets.py` (baseline download into the content-addressed `AssetManager`), a real `DiffEngine._diff_raster` with per-channel `color_threshold` (default 16), contiguous diff-region detection + node attribution, `noise_floor` (diffRatio ≤ 0.01 → `pixels` = 1.0), capped `pixel_weight=0.15`, deterministic capture (fixed viewport, `deviceScaleFactor=1`, `document.fonts.ready`, animations killed), `pixel_mismatch` classifier registration, and TS `screenshot_compare.ts` shell-out with a SHA-256 hash fast-path and `cmdCompare --baseline`.
- `DiffEngine.diff(plan, render_meta, render_screenshot=None, baseline_png=None, raster_options=None)` composes the score as `(1 - pixel_weight) * structural + pixel_weight * pixels_category`; `RasterOptions` (diff_engine.py) validates the knobs (`color_threshold`, `noise_floor`, `min_region_area`, `pixel_weight`) and `RepairConfig` (repair_loop.py:58) mirrors them with defaults and `to_dict()` coverage.
- Two known limitations remain (both were Part 12 non-goals):
  1. **Pixel-count diffing is not perceptual.** A render that differs only by antialiasing/font-hinting jitter still produces diff regions and lowers `pixels` even when a human sees no change. Part 12's `color_threshold` and 1% noise floor blunt this but cannot distinguish "perceptual noise" from "real localized change" — a genuine 0.4% button-color change (the smoke-test case) and a 0.4% AA-jitter case score identically.
  2. **Baselines are static.** Part 12's design spec flagged "Figma exports not byte-stable over time → baseline refresh is manual and explicit" as a risk. Once a Figma design is edited (or the local font stack changes), every subsequent render diffs against a stale baseline and reports permanent false mismatches until a human re-downloads.
- SSIM (structural similarity) is the standard perceptual metric: a windowed comparison of luminance means/variances/covariances that yields ~1.0 for perceptually identical images and drops only on structurally meaningful change. A naive global-mean SSIM would MISS a small localized change (the button case stays ~0.99) — so the design must be **regional**: evaluate SSIM per detected diff-region bbox.
- Auto-refresh and SSIM are complementary: SSIM provides a trustworthy "visually identical" verdict, and auto-refresh may then safely adopt clean renders as the new baseline — SSIM guards B from absorbing real regressions.

## Decisions (user-approved)

- **Scope: A (SSIM) + B (baseline auto-refresh).** Diff heatmap (C), native TS pixel diffing, image resampling beyond the fixed SSIM downsample, and extended PNG formats remain non-goals.
- SSIM is computed in **pure-Python stdlib** (no numpy — the repo's stdlib-only convention), downsample-first for bounded cost.
- SSIM is a **gating signal alongside** the per-pixel diff, not a replacement: per-pixel diffing still drives region detection/attribution (repair targeting needs exact pixels); SSIM provides the perceptual verdict that decides whether those regions are real mismatches or noise.
- Auto-refresh is **opt-in** (`RepairConfig.refresh_baseline: bool = False` default) — Part 12's "manual and explicit" semantics remain the default; it adopts a render as the new baseline **only** when the SSIM verdict is clean.
- New knobs live in both `RasterOptions` and `RepairConfig` (validated in `__post_init__`, covered by `to_dict()`), same pattern as Part 12.

## Design

1. **SSIM module** — new `plugin/figmaforge/core/ssim.py`, stdlib only. `ssim(a: PngImage, b: PngImage, window: int = 8) -> float` operating on the luminance plane (per-channel average), after downsampling both images by 2×2 averaging until the long side ≤ 256px (deterministic, no sampling jitter). Mean/variance/covariance computed with a sliding-window sum (integral-image style) so cost is O(N) per window size, not O(N·w²). Identical inputs → exactly 1.0; uniform luminance shift → high; structured change → low. No image libraries; works directly on the pixel buffers from `png_codec.decode_png`.
2. **Regional gating in `_diff_raster`** — after the existing per-pixel pass:
   - diffRatio ≤ `noise_floor` → clean (unchanged, SSIM not needed).
   - Else, for each detected diff region compute `ssim` over that region's bbox (full-resolution within the bbox — bboxes are small, so no downsample needed locally). `min_region_ssim = min` over regions.
   - **Clean verdict** if `min_region_ssim ≥ ssim_threshold` (default 0.95), or — when zero regions exist (scattered sub-`min_region_area` noise) — if the global SSIM ≥ threshold. A clean verdict suppresses pixel-mismatch emission and sets `pixels = 1.0`.
   - Otherwise emit the attributed `pixel_mismatch` regions exactly as Part 12 does, `pixels = 1 - diffRatio`.
   - Global mean SSIM is **always** recorded in `raster_stats["ssim"]` (and `min_region_ssim` when regions exist) for diagnostics, whether or not it gates — data-driven tuning without code changes.
   - `RasterOptions`/`RepairConfig` gain `ssim_enabled: bool = True` and `ssim_threshold: float = 0.95`; `ssim_enabled=False` restores exact Part 12 behavior.
3. **Baseline auto-refresh** — `RepairConfig` gains `refresh_baseline: bool = False` and `max_baseline_refreshes_per_run: int = 3`. In `repair_loop.py`, after a diff whose verdict is **clean** (SSIM verdict clean AND sizes matched — i.e. byte-different but perceptually identical), and only if `refresh_baseline` is set and the per-run budget is not exhausted: atomically replace the baseline at `baseline_png` with the render (`os.replace`), and record `"baseline_refreshed": true` in that iteration's result so adoption is observable. Refreshes are **bounded** per run; adoption is **self-stabilizing** because the store is content-addressed and capture is deterministic: once adopted, a subsequent clean render is byte-identical to the new baseline and the TS/Python hash fast-path short-circuits, so no churn. Never refresh on size mismatch, on a non-clean verdict, or when `refresh_baseline=False` (default — Part 12 behavior preserved).
4. **Python CLI + TS wiring** — `python3 -m core.pixel_diff` output JSON gains `"ssim": float | null` and `"min_region_ssim": float | null` plus a `--ssim-threshold` flag (default 0.95). `screenshot_compare.ts` parses the new keys into the existing `ScreenshotComparison` interface (optional `ssim`/`minRegionSsim` fields; missing keys → null, so old output remains parseable); `cmdCompare` prints SSIM when present. Hash fast-path unchanged.
5. **Testing** — no binary fixtures; deterministic patterns generated at test time via `png_codec.encode_png`:
   - `ssim` unit tests: identical → 1.0; uniform luminance shift → high; a small localized change → **global SSIM stays high while regional SSIM over the changed bbox drops below threshold** (the key test proving regional gating catches the button-color case that global mean SSIM would miss); downsampled cost bound (large inputs complete quickly).
   - `_diff_raster` gating: AA-style noise (deterministic sub-threshold jitter pattern) → clean verdict, no mismatches, `pixels` = 1.0, SSIM in `raster_stats`; real localized change → mismatches still emitted, `pixels = 1 - diffRatio`; `ssim_enabled=False` → exact Part 12 behavior; size mismatch → unchanged size-mismatch path.
   - Auto-refresh: `repair_loop` with a fake `render_fn` producing byte-different-but-clean renders → baseline adopted (event recorded), subsequent iterations diff against the new baseline, refreshes bounded at `max_baseline_refreshes_per_run`; a real regression (low region SSIM) → never refreshed; `refresh_baseline=False` → never refreshed; content-addressed dedup → repeated identical renders do not churn the manifest.
   - TS: pixel_diff parsing with and without `ssim` keys; `cmdCompare` output includes SSIM; existing comparator suite stays green.
6. **Docs** — `docs/repair-loop.md`: new SSIM section (regional gating, knob table: `ssim_enabled`, `ssim_threshold`, `refresh_baseline`, `max_baseline_refreshes_per_run`) and an auto-refresh section (opt-in, clean-verdict guard, bounded, self-stabilizing). `docs/DEVELOPMENT_LOG.md`: Part 13 entry with final test counts. README/CLAUDE.md: move "baseline auto-refresh / perceptual metrics" from Next Steps into delivered; update diffing lines only.

## Noise-risk mitigations

1. **Regional SSIM gating** — AA/font jitter scores high SSIM per region → suppressed; a genuine localized change scores low SSIM in its own bbox → still reported. Global mean SSIM alone would miss small real changes; regional evaluation is the guard.
2. **SSIM always diagnostic** — global mean + min-region SSIM land in `raster_stats` on every raster diff, so thresholds can be tuned from real data without code changes.
3. **Opt-in auto-refresh, clean-verdict only, bounded** — `refresh_baseline` defaults to False; adoption requires the SSIM clean verdict and consumes a per-run budget, so a slow real regression SSIM misjudges can be absorbed at most `max_baseline_refreshes_per_run` times per run and is recorded per iteration.
4. **Self-stabilizing adoption** — content-addressed storage + deterministic capture mean an adopted baseline converges (next clean render hits the hash fast-path); no unbounded churn.
5. **Pure-stdlib, cost-bounded** — downsample-to-256 + integral-image window sums keep SSIM cheap; regional SSIM computes only inside detected bboxes.
6. **Fallback knobs** — `ssim_enabled=False` restores exact Part 12 behavior; `ssim_threshold` is tunable; `require_approval` remains available for conservative workflows.

## Non-goals

- Diff heatmap image output (C — deferred; reuses the same region data, cheap to add later).
- Native pixel diffing in the TS runtime (shell-out only, per Part 12).
- Image resampling beyond the fixed 2×2 downsample used for SSIM.
- Grayscale/palette/16-bit PNG support.
- Scheduled/cron baseline refresh — auto-refresh is scoped to the repair loop, not a background job.
- Backend implementations (unchanged from Part 12).

## Risks

- **SSIM cost on very large images** → mitigated by the 256px downsample bound and integral-image windows; regional computation is confined to bboxes.
- **Regional gating masking a genuinely tiny change** → threshold defaults to 0.95 and is tunable; SSIM is always reported; any region below threshold still emits a mismatch.
- **Auto-refresh absorbing a misjudged regression** → bounded budget, clean-verdict-only adoption, per-iteration event recording, and the existing `require_approval`/`auto_rollback_on_regression` guards all remain in force.
- **Pure-python SSIM correctness** → deterministic fixtures (identical → 1.0, uniform shift → high, structured noise → low) plus the regional-vs-global test pin the exact behavior.
- **Baseline drift from real Figma edits** → unchanged from Part 12: auto-refresh only adopts *renders the loop itself produced*; re-downloading from Figma remains the explicit way to sync a design change.
