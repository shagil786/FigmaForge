# Assets Stage Wiring — download + content-address image/SVG assets (Part 17) — Implementation Plan

Branch: `feat/part-17-assets-stage` (from `main` @ `da84f09`).
Spec: `docs/superpowers/specs/2026-08-17-assets-stage-design.md`.
Conventions: Python stdlib-only + `python3 -m unittest`; TS minimal framework (run via
`PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/tests/run_all.js`);
deterministic; `claude plugin validate --strict` green; commits per task; final gate at Task 5.

Current baseline: Python **499** tests OK (42 files), TS **128** passing, tsc clean.

---

## Task 1 — asset-reference collector + asset fetch reuse (Python, test-first)

**Tests** (new `tests/test_assets_collector.py` + additions where natural; red first):
1. `test_collect_from_asset_refs` — an IR whose node has `IRAssetRef(node_id, url, image_ref)` collects `(node_id, url, "image")`.
2. `test_collect_from_document_assets` — `IRDocument.assets = {"1:1": url}` with no per-node ref collects the node id → url entry (asset kind "image").
3. `test_collect_svg_kind` — an `image_ref`/url with an `.svg` extension (or `svg:` prefix) collects kind `"svg"`.
4. `test_collect_image_fill_without_url` — a node with an image fill but no resolved URL collects with `url=None` (drives the token/`get_images` path).
5. `test_collect_empty_document` — no nodes → empty list.

**Implement** `core/asset_collector.py` (or in `scripts/pipeline.py` if it stays small —
prefer a `core/` module so it is unit-testable without the CLI):
- `AssetRef` dataclass (`node_id`, `url: Optional[str]`, `image_ref: Optional[str]`, `kind`).
- `collect_asset_refs(doc: IRDocument) -> List[AssetRef]` — walk `doc.all_nodes()`;
  from `node.asset` (IRAssetRef), `doc.assets.get(node.id)`, and image fills
  (`node.style.fills` kind "image"); kind detection from `image_ref`/url.
- Re-export the `figma_assets` fetch machinery (`_default_transport`, `_fetch_with_retry`)
  as public names (`default_transport`, `fetch_with_retry`) with no behavior change.

**Acceptance**: Task 1 tests green; full Python suite still **499** OK; commit
`feat(core): asset-reference collector + fetch reuse`.

---

## Task 2 — `pipeline.py assets` subcommand (Python, test-first)

**Tests** (extend `tests/test_pipeline_cli.py`; red first):
1. `test_assets_empty_manifest_deterministic` — `assets --ir <normalize output>` exits 0,
   emits one JSON line with `{"assets": {}, "counts": {...}}`; two runs byte-identical.
2. `test_assets_downloads_file_url` — build an IR with `assets = {"1:1": "file://<tmp>/img.png"}`
   (write real PNG bytes), run `assets --ir <that> --assets-dir <tmp>/store`; manifest has
   node `1:1` with a `content_hash`, a `local_path`, kind `image`; the hashed file exists
   under the store; a second run into a fresh store is byte-identical (content-addressed).
3. `test_assets_svg_validation` — a `file://` `.svg` URL with a valid SVG body ingests as
   kind `svg`; with an invalid/scripted SVG body → the AssetManager SVG rejection surfaces
   as a CLI error (exit 1, no traceback).
4. `test_assets_invalid_ir` — `assets --ir <not-ir.json>` exits 4.
5. `test_assets_missing_token_with_unresolved_refs` — an IR with an image-fill node and no
   URL, `FIGMA_TOKEN` unset → exit 3, stderr mentions `FIGMA_TOKEN`.

**Implement** (`scripts/pipeline.py`):
- `assets` subcommand: `--ir` (required), `--file-key` (optional), `--assets-dir` (default
  `generated-assets`), `--out` (optional).
- Pipeline: `_load_ir` → `collect_asset_refs` → for refs missing a URL: if no
  `FIGMA_TOKEN` → `_CliError(3, …)`; else `FigmaClient().get_images(file_key, ids)` merged
  in → fetch each via `fetch_with_retry(default_transport, url, timeout, retries)` →
  `AssetManager(assets_dir).ingest(raw, url, kind, ext)` → manifest entries (relative
  `local_path` = `<hash[:2]>/<hash>`, `deduped` flag from pre-existing manifest) sorted by
  node_id → emit `{"assets", "counts", "assets_dir"}`.
- Reuse the existing exit-code table (2/3/4/1) and `_emit_with_out`.

**Acceptance**: Task 2 tests green (pipeline CLI suite grows); full Python suite green
(499 + N); commit `feat(scripts): pipeline assets subcommand (content-addressed)`.

---

## Task 3 — TS assets stage handler + run wiring (TS, test-first)

**Tests** (extend `runtime/tests/backend_codegen.test.ts`; red first):
1. `test_six_stage_run_produces_asset_manifest` — register all six handlers
   (ingest…generate + assets) with `target = flutter` and `setShared("filePath", FIXTURE)`;
   run completes; an `asset_manifest` artifact exists whose manifest is the deterministic
   empty one (`assets: {}`); two runs → identical manifests.
2. `test_invoke_assets_downloads_file_url` — `invokeAssets(cfg, irJson-with-file://-url,
   tmpStore)` returns a manifest with the node's `content_hash` + `local_path`, and the
   hashed file exists on disk.
3. `test_assets_without_ir_fails_cleanly` — register assets without normalize (no irJson
   in shared) → stage fails with a clear "no irJson" message.

**Implement** (`runtime/src/core/backend_codegen.ts` + `runtime/src/cli/main.ts`):
- `invokeAssets(cfg, irJson, assetsDir)` — stage `ir.json` to a temp file, spawn
  `pipeline.py assets --ir <tmp> --assets-dir <assetsDir>`, parse the manifest line
  (reuse `parseJsonLine`), return it.
- `createAssetsStageHandler()` — reads `ctx.shared["irJson"]` (clear error if absent),
  runs `invokeAssets` with `path.join(ctx.config.outputDir, ctx.config.runId, "assets")`,
  stores the manifest in `ctx.shared["assetManifest"]`, returns it.
- `cmdRun`: register `pipeline.onStage("assets", createAssetsStageHandler())` after
  generate → six real stages. Help text: note the assets stage.

**Acceptance**: TS suite **128 + 3** passing, tsc clean; `figmaforge run --file=<fixture>`
produces an `asset_manifest` artifact; commit
`feat(runtime): wire assets stage to the python asset pipeline`.

---

## Task 4 — Docs

1. `docs/real-figma-demo.md` — assets stage in the artifact layout (`asset_manifest`,
   content-addressed store under `<run>/assets/`), note that fixture runs produce an empty
   manifest and live runs need `FIGMA_TOKEN` for unresolved image URLs.
2. `docs/DEVELOPMENT_LOG.md` — Part 17 entry (collector, assets subcommand, TS handler,
   counts).
3. `README.md` — status header Parts 1–17 + counts; Part 17 checklist lines; Next Steps
   drop the assets-stage wiring item if present.
4. `CLAUDE.md` — module lines (`core/asset_collector.py`, `assets` subcommand,
   `backend_codegen.ts` stage count) + test counts.
5. `docs/architecture.md` — components (assets subcommand + handler) and status paragraph
   (six real stages).

**Commit**: `docs: document Part 17 assets stage`.

---

## Task 5 — Final gate + PR (do NOT merge)

1. Python full suite → expect **499 + ~10** OK, zero skips.
2. TS: `npx tsc` clean; run_all → expect **128 + 3** passing.
3. `claude plugin validate --strict plugin/figmaforge` → ✔.
4. Fill the DEVELOPMENT_LOG Part 17 counts with actual N; amend or follow-up commit.
5. `figmaforge run` smoke once more (six stages, `asset_manifest` artifact present,
   deterministic second run).
6. `git status --short` empty; push (`git push -u origin feat/part-17-assets-stage`);
   `gh pr create --base main --title "feat: Part 17 assets stage — content-addressed image/SVG assets through the TS runtime"`.
   Do NOT merge (repo convention — user's call).
