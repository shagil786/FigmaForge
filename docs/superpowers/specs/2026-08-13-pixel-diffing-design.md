# Pixel Diffing + Figma Baseline Download (Part 12) — Design Spec

## Context

- Part 11 (merged, PR #11) delivered real Playwright rendering: `plugin/figmaforge/core/render_harness.py` produces screenshots at `{output_dir}/{build_id}.png` plus node-id-keyed layout metadata; `render_adapter.py` injects the harness via `RepairLoop(render_fn=...)`.
- `DiffEngine._diff_raster` (`core/diff_engine.py:59-62`) is a placeholder returning `[]`; `DiffReport` already has a `pixels` category (currently always 1.0). Overall score is count-based: `1 - mismatches/nodes`. `RepairConfig.similarity_threshold=0.95`.
- TS `runtime/src/core/screenshot_compare.ts` is fake (hash + buffer-size heuristic); `cmdCompare` (`runtime/src/cli/main.ts`) prints "No reference image to compare against."
- `FigmaClient.get_images(file_key, node_ids, fmt, scale)` exists (`core/figma_client.py`) and returns presigned URLs only; its docstring references a `figma_assets` module that does not exist yet. Injectable transport pattern + `FIGMA_TOKEN` env + typed errors are the established conventions.
- `AssetManager.ingest(raw_data, original_url, kind, extension)` provides content-addressed SHA-256 storage (`storage_dir/{hash[:2]}/{hash}`); `AssetHandler.mark_downloaded(node_id, local_path, checksum)` is the bookkeeping hook.
- No image library exists anywhere in the repo (Python is stdlib-only except the user-approved required Playwright; the TS runtime has zero runtime dependencies). PNG decode/encode is feasible with stdlib `zlib` + `struct`.
- No PNG fixtures exist in the repo; the test convention generates data at test time.
- `repair_classifier._MISMATCH_TYPE_TO_CATEGORY` must register any new mismatch type or it lands in `unclassifiable` (repair-inert).
- Docs: `docs/repair-loop.md` names `_diff_raster` as a placeholder and states the IR is the immutable source of truth; `docs/DEVELOPMENT_LOG.md` Part 10 overstates `screenshot_compare` capabilities (claims structural similarity — false).

## Decisions (user-approved)

- Decoder: pure-Python stdlib `zlib` + `struct` PNG codec. NO new dependency.
- Comparison lives in Python; the TS runtime shells out to it (one real implementation, two entry points).
- The Figma baseline PNG is a SUPPLEMENTARY signal feeding the `pixels` category; IR/geometry/style remain the primary repair drivers.
- Alignment: fixed-viewport screenshots (`full_page=False`) vs Figma frame export at `scale=1.0` — same canvas on both sides.

## Design

1. **PNG codec** — new `plugin/figmaforge/core/png_codec.py`, stdlib only (`zlib`, `struct`). `decode_png(bytes) -> PngImage(width, height, channels, pixels)` supporting 8-bit RGB/RGBA, color types 2/6, non-interlaced, all 5 scanline filters (none/sub/up/average/paeth); typed `PngError` for interlaced, 16-bit, palette/grayscale-alpha, and truncated/corrupt data. `encode_png(image) -> bytes` as a minimal filter-0 writer. Deterministic, no floating point in the hot path.
2. **Baseline download** — new `plugin/figmaforge/core/figma_assets.py` (name already promised by the `figma_client` docstring): `download_baselines(client, file_key, node_ids, asset_manager, asset_handler=None, scale=1.0, fmt="png")` → `get_images()` → urllib fetch of presigned URLs (injectable transport for tests, bounded retries, timeouts) → `AssetManager.ingest(kind="image", extension="png")` → optional `AssetHandler.mark_downloaded`. Typed `FigmaAssetError` hierarchy (auth not needed for presigned URLs; expiry → retry-once then raise). Content-addressed dedup gives natural caching.
3. **Real `_diff_raster`** — decode baseline + rendered screenshot; if sizes differ → one `pixel_mismatch` describing the size mismatch (never raise into the loop). Else per-pixel compare with `color_threshold` (max per-channel delta, default 16); compute diffRatio and MAE. Region detection: contiguous diff runs ≥ `min_region_area` (default 8px) become candidate regions; each region is attributed to a node by bbox intersection against `render_meta` (largest-overlap wins; unattributed regions get the `node_id` of the root screen node). Each attributed region emits a mismatch dict `{"node_id", "type": "pixel_mismatch", "expected": {"region": {...}, "baseline_mae": ...}, "actual": {"diff_percentage": ...}}` — same shape family as the geometry/style siblings. `pixels` category score = 1.0 when diffRatio ≤ `noise_floor` (default 0.01), else `1 - diffRatio`.
4. **Extended DiffEngine API** — `DiffEngine.diff(plan, render_meta, render_screenshot: Optional[Path|str]=None, baseline_png: Optional[Path|str]=None)` — fully backward compatible: both omitted → today's behavior, pixels category 1.0. `DiffReport` gains a `raster_stats` dict (`mae`, `diff_percentage`, `region_count`) when a raster diff ran. `repair_loop.py` passes the screenshot path it already receives from `render_fn` plus a configured baseline path — `RepairConfig` gains `baseline_png: Optional[str]`, `color_threshold=16`, `noise_floor=0.01`, `min_region_area=8`, `pixel_weight=0.15`.
5. **Capped pixel weight** — overall similarity composes as `(1 - pixel_weight) * structural_score + pixel_weight * pixels_category_score` when a raster diff ran (structural = today's count-based score). With the default 0.15, worst-case raster noise can move the gate by at most 0.15 — the repair loop cannot be hijacked by pixel noise; geometry/style remain the primary repair drivers.
6. **Deterministic capture** — `RenderHarness.render(..., full_page: bool = True)` optional param (default preserves Part 11 behavior); the repair adapter passes `full_page=False`. The harness additionally sets `device_scale_factor=1` on the Playwright page and waits for `document.fonts.ready` before screenshot; `render_html.py` injects `* { animation: none !important; transition: none !important; caret-color: transparent; }`.
7. **Classifier registration** — add `pixel_mismatch` to `_MISMATCH_TYPE_TO_CATEGORY` (category: visual/pixel — follow the file's existing mapping conventions; repair candidates for it may map to the style/layout patch heuristics already present; if none fit, it classifies but the repair planner treats it as low priority — never silently dropped).
8. **TS wiring** — new Python CLI entry `python3 -m core.pixel_diff --a <path> --b <path> [--threshold N]` emitting one JSON line `{similarity, diffPixelCount, diffPercentage, totalPixels, width, height, identical, meanAbsoluteError:{r,g,b}}`; `screenshot_compare.ts` `compareBuffers`/`compareFiles` shell out to it (spawn `python3`, parse last-line JSON, existing `ScreenshotComparison` interface preserved; keep the hash fast-path). `cmdCompare` resolves the baseline via the asset manifest path passed as flag/arg instead of printing "No reference image" (missing baseline remains a clean, documented error).
9. **Testing** — no binary files in the repo: tests generate PNGs at runtime via `png_codec.encode_png`. Coverage: decoder filter matrix (5 filters), corrupt/interlaced rejection, encode→decode roundtrip; `figma_assets` with injected transport (success, retry, expiry, dedup); `_diff_raster` unit tests (identical → 1.0, noise below floor → 1.0, real region → attributed mismatch, size mismatch); weight-capping math; classifier registration; `repair_loop` integration with fake `render_fn` + generated baseline; TS tests for shell-out parsing (garbage/missing python → clean error, existing fake-buffer tests updated); one chromium-gated E2E (skip-guarded like `test_render_harness_smoke.py`): render fixture → diff against generated baseline.
10. **Docs** — amend `docs/repair-loop.md`: baseline PNG = supplementary reference, IR remains source of truth; document the config knobs. Correct the DEVELOPMENT_LOG Part 10 overclaim about `screenshot_compare`. Add a Part 12 entry with final counts. Update README/CLAUDE.md only where they mention diffing/screenshots.

## Noise-risk mitigations

Defense-in-depth against antialiasing/font noise inflating mismatches:

1. **Deterministic capture** — `deviceScaleFactor=1`, `document.fonts.ready` wait, animations killed, fixed viewport, `scale=1.0` export.
2. **Per-channel `color_threshold=16`** — ignores sub-threshold jitter.
3. **Noise floor** — diffRatio ≤ 1% counts as clean.
4. **Region filtering** — only contiguous regions ≥ 8px are attributed; scattered AA noise is ignored.
5. **Capped `pixel_weight=0.15`** — raster can move overall similarity by at most 0.15.
6. **MAE + diffPercentage carried in `DiffReport`** — enables data-driven tuning.
7. **Every knob lives in `RepairConfig` with defaults** — tunable without code changes.

## Non-goals

- SSIM/perceptual metrics, image resize/resampling, diff heatmap image output, baseline auto-refresh/cron, native pixel diffing in the TS runtime (shell-out only), grayscale/palette/16-bit PNG support, backend implementations, and a doc sweep beyond the three named docs.

## Risks

- Figma exports not byte-stable over time → content-addressed store + on-demand download; baseline refresh is manual and explicit.
- Figma fonts differ from local rendering → mitigations above; the pixel category is supplementary by design.
- Presigned URL expiry mid-download → retry-once then typed error; the loop falls back to geometry/style-only diffing (baseline is optional).
- PNG decoder correctness → filter-matrix tests + roundtrip tests; unsupported formats raise typed `PngError` rather than produce wrong pixels.
