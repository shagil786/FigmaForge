# Assets Stage Wiring — download + content-address image/SVG assets through the TS runtime (Part 17) — Design Spec

## Context

- **The TS pipeline's `assets` stage is still scaffolding.** Parts 15–16 wired
  `ingest`/`normalize`/`resolve`/`layout`/`generate`; `assets` (and
  render/compare/repair/verify) have no handler, so `figmaforge run` skips them.
  `stageToArtifactKind("assets")` already maps to `"asset_manifest"`.
- **The Python asset machinery is complete and tested** — but has no CLI entry,
  so the TS runtime cannot reach it:
  - `core/asset_manager.py` — `AssetManager(storage_dir)`: SHA-256 content-addressed
    storage (2-level prefix), `ingest(raw, original_url, kind, extension) → hash`,
    SVG validation, on-disk `manifest.json` (`AssetManifest`/`AssetMetadata.to_dict`).
  - `core/figma_assets.py` — `download_baselines(...)`: `client.get_images` →
    render URLs, retry fetch with content-length cap, content-addressed ingest,
    dedup flagging. Thoroughly covered by `test_figma_assets.py` (retries, expiry,
    dedup, caps) — Part 17 must **reuse** this, not duplicate it.
  - `core/asset_handler.py` — `AssetHandler`: per-run node_id → url registry with
    downloaded/local_path/checksum state (`test_asset_handler.py` covers it).
- **Where assets live in the IR:** `IRDocument.assets: {node_id: url}` (from the
  `/v1/images` endpoint via `IRBuilder(images=...)`) and per-node `IRAssetRef`
  (`node_id`, `url`, `image_ref`, `local_path`). Image fills are `IRFill(kind="image")`
  with `image_ref`. The checked-in fixtures have **no** image fills/assets, so the
  offline path produces a deterministic empty manifest; a `file://` URL in a test IR
  exercises the full download → content-address → manifest path without a token.
- Repo rules: stdlib-only Python, deterministic output, `python3 -m unittest` (no
  pytest), TS minimal framework, `claude plugin validate --strict` green, no new deps.
- Current baseline: Python **499** tests OK (42 files), TS **128** passing, tsc clean.

## Decisions (proposed scope)

1. **One new `assets` subcommand** (`scripts/pipeline.py`), same one-JSON-line
   contract as the other subcommands:
   `pipeline.py assets --ir <ir.json> [--file-key <key>] [--assets-dir <dir>] [--out <manifest.json>]`
   - Load + schema-validate the IR (existing `_load_ir`).
   - Collect asset references: walk IR nodes for `node.asset` (`IRAssetRef`) and
     image fills; resolve each to `(node_id, url, kind)`. `kind` = `svg` when the
     url/image_ref is an SVG (`image_ref` prefix or `.svg` extension), else `image`.
   - Missing URLs: if any ref lacks a resolved URL and there is no `FIGMA_TOKEN` →
     exit 3 (documented message); with a token, `FigmaClient.get_images(file_key,
     ids)` fills them (reusing the existing client + `download_baselines` fetch
     machinery — the retry/expiry/cap behavior stays in `figma_assets`).
   - Download + content-address via `AssetManager` into `--assets-dir`
     (default `generated-assets`): fetch through the retry transport, `ingest` each,
     record `{node_id, url, content_hash, local_path (relative), kind, extension,
     deduped}`.
   - Emit a deterministic manifest: `{"assets": {node_id: {...}}, "counts": {total,
     downloaded, deduped, svg}, "assets_dir": "<abs path>"}`, refs sorted by node_id.
2. **TS stage handler** (`backend_codegen.ts`): `invokeAssets(cfg, irJson, assetsDir)`
   (spawn `assets --ir <tmp> --assets-dir <dir>`, parse the manifest line) +
   `createAssetsStageHandler` (reads `irJson` from shared state, runs the CLI with a
   run-scoped `--assets-dir`, stores the manifest in `ctx.shared` and returns it —
   the coordinator auto-stores it as the `asset_manifest` artifact). `cmdRun`
   registers it after `generate` → six real stages.
3. **Verification stays structural + offline:** no network in tests — `file://` URLs
   exercise the real download/ingest path; the fixture IR exercises the deterministic
   empty-manifest path. The live `/v1/images` fetch is implemented + documented,
   verified only structurally (no token in this environment).

## Design

```
figmaforge run --file=<fixture> --target=flutter+flutter_widgets
  ingest    → figma_raw        (existing)
  normalize → design_ir        (existing)
  resolve   → resolution_report (existing)
  layout    → layout_plan      (existing)
  generate  → generated_code   (existing)
  assets    → asset_manifest   (new: pipeline.py assets, content-addressed store)
```

- **`_collect_asset_refs(doc)`** — pure, testable, no I/O: returns
  `List[AssetRef{node_id, url?, image_ref?, kind}]` by walking `doc.all_nodes()`.
- **Fetch + ingest** — reuse `figma_assets`'s `_fetch_with_retry` + cap machinery
  (exported as needed) and `AssetManager.ingest`; `download_baselines` itself stays
  for the compare-stage baseline path (not reused here — assets stage targets
  IR-referenced assets, not baselines).
- **Determinism:** manifest refs sorted by node_id; content addressing makes repeated
  runs byte-identical for the same inputs; the fixture path yields `{assets: {}}`
  deterministically.
- **Testing:** Python — collector unit test (image/svg/asset-ref shapes); CLI
  determinism (fixture IR → empty manifest, byte-identical runs); a `file://`-URL IR
  → downloaded, hashed, stored, manifest correct, byte-identical second run; invalid
  IR → exit 4; missing token with unresolved refs → exit 3. TS — six-stage pipeline
  run → `asset_manifest` artifact present; a direct `invokeAssets` call with a
  `file://` IR → manifest has the hash + the local file exists; handler missing
  `irJson` → clear stage error.

## Risk mitigations

1. **Network dependence** — all tests are offline (`file://` URLs + empty fixture
   manifests); the only network call (`/v1/images` for unresolved refs) requires an
   explicit token and fails cleanly (exit 3) without one.
2. **Duplicating download logic** — the assets subcommand reuses `figma_assets`'s
   retry/cap transport and `AssetManager.ingest`; no new fetch code.
3. **Manifest drift** — deterministic ordering (sorted node_ids) + byte-identical
   two-run tests, same convention as every other subcommand.
4. **Scope creep** — local asset paths are **not** threaded into generated code
   (the FILLS_IMAGE/IMAGE_ASSETS lift is a per-backend follow-up); render/compare/
   repair/verify stay unwired.

## Non-goals

- Threading downloaded asset paths into generated output (per-backend FILLS_IMAGE /
  IMAGE_ASSETS lift — deferred follow-up, backends keep their honest fallback +
  fidelity marker).
- Wiring render/compare/repair/verify into the TS runtime.
- SVG → component conversion; asset serving.
- Compiling or executing generated code.
