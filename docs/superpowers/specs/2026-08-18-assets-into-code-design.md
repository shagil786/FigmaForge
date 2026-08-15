# Threading Resolved Assets into Generated Code (Part 18) — Design Spec

Branch: `feat/part-18-assets-into-code` (from `main` @ `40269c2`).
Predecessor: Part 17 wired the `assets` stage — IR image/SVG refs are downloaded
and content-addressed into a run-scoped store, and the asset manifest
(`node_id → {status, content_hash, local_path, kind}`) is a real stage artifact.
**But generated code never references those files**: the four web backends
(html_css, react_tailwind, vue, svelte) declare `FILLS_IMAGE`/`IMAGE_ASSETS`
*partial* and degrade image fills to a solid fallback + an inline
`fidelity: fills_image approximated (solid fallback)` marker.

Part 18 closes the loop: the resolved local asset paths are threaded into the
generated code for the **web backends**, `FILLS_IMAGE` is lifted from partial →
**supported** for those four, and the honesty audit is extended to lock the new
behavior against silent regression.

## Verified current state (not assumed)

- `BackendAdapter.generate(..., options: Optional[Dict])` already exists;
  `html_css` and `react_tailwind` already read `opts = options or {}` — the
  `options` dict is the clean injection seam (no key collisions).
- All four web backends declare `Feature.FILLS_IMAGE` and `Feature.IMAGE_ASSETS`
  partial and emit the solid-fallback + marker today:
  - html_css: `_HTML_CSS_PARTIAL` (line ~125), marker emitted in `_render_node`
    for nodes in `image_fill_ids`.
  - react_tailwind: `_REACT_TW_PARTIAL`, own mapper emits a
    `fidelity: fills_image approximated (omitted)` marker (line ~447).
  - vue / svelte: `_VUE_PARTIAL` / svelte partial, marker in the markup
    emitter for nodes whose `ir.style.fills` has a visible image fill.
- The shared web lowering is `web_common.extend_ir_style(style, node)`:
  solid → `background: #hex`; gradient → `linear-gradient(...)`; anything else
  (image/none) → `background: #f0f0f0` (the fallback). **This is the single
  seam for html_css / vue / svelte** (all three lower through it).
- The audit (`tests/test_backend_honesty_audit.py`) renders ONE canonical rich
  fixture per backend with `generate(document, plan, resolution)` — **no
  options** — and requires every declared-supported + exercised feature to
  appear in the output. `FILLS_IMAGE` is **not** in `EXERCISED`; the
  `test_audit_detects_a_silent_drop` coverage guard deliberately uses
  `FILLS_IMAGE` as the example of an *unexercised* feature.
- The Part 17 asset manifest shape: `{"assets": [ {node_id, url, image_ref,
  kind, status, content_hash, local_path}, ...], "counts": {...},
  "assets_dir": ...}` — `assets` sorted by node_id; `local_path` is the
  absolute content-addressed store path.
- Flutter emits `color: Color(0xFFF0F0F0)` for image fills (`_FLUTTER_PARTIAL`);
  SwiftUI declares `FILLS_IMAGE` **unsupported**. **Native stays out of scope.**

## Design

### 1. The seam: `options["assets"]`

`generate(..., options)` gains one documented key:

```python
options["assets"] = {
    node_id: {"path": "<content-addressed local path>", "kind": "image" | "svg"},
    ...
}
```

Built by `pipeline.py generate --assets <asset-manifest.json>` from the Part 17
manifest's `downloaded` entries (node_id-sorted → deterministic). The path is
the manifest's `local_path` verbatim (absolute, content-addressed) — the same
path the manifest already reports, so the artifact chain stays self-consistent.

### 2. CLI: `generate --assets`

`pipeline.py generate` (both `--file` recompute and `--ir/--layout` staged
modes) accepts an optional `--assets <manifest.json>`: loads + validates the
manifest (invalid JSON / missing `assets` list → exit 4), builds the
`node_id → {path, kind}` map from `downloaded` entries only, and passes it as
`options["assets"]`. No `--assets` → `options` omitted → backends behave
exactly as today (honest fallback). Unresolved entries (`status: "unresolved"`)
are excluded — a node whose asset never resolved keeps the fallback + marker.

### 3. Shared web lowering (html_css / vue / svelte)

`web_common.extend_ir_style` gains an optional `assets` map. For a visible
`image` fill:

- **resolved** (node id in `assets`) → `background-image: url(<path>)`,
  `background-size: cover`, `background-position: center` (Figma's default
  image-fill fit/alignment; SVG files are valid CSS backgrounds too, so
  `kind == "svg"` is emitted identically).
- **unresolved** → unchanged `background: #f0f0f0` fallback.

Each emitter's marker logic flips from "image fill present → marker" to
"image fill present **and unresolved** → marker"; a resolved image fill emits
the real reference with **no** fidelity marker. This keeps the honesty rule:
an approximation is always marked, a real lowering is not.

### 4. react_tailwind (own mapper)

The mapper's fill branch: image fill + resolved asset → arbitrary-value classes
`bg-[url(<path>)] bg-cover bg-center` (Tailwind arbitrary values allow
parentheses/commas; the absolute path has no spaces); unresolved → existing
marker unchanged.

### 5. Capability declarations

- **`FILLS_IMAGE`: partial → supported** for html_css, react_tailwind, vue,
  svelte. Justification: with the assets stage (the normal run path) the
  backend genuinely emits the image reference; the no-asset fallback is a
  documented *degraded input condition* (assets stage skipped / URL
  unresolved), always marked — never a silent approximation. Docstrings state
  the resolved-asset requirement.
- **`IMAGE_ASSETS` / `SVG_ASSETS` stay partial** — for web output the URL
  reference *is* the asset representation; there is no asset-bundle/catalog
  concept to lift. Declarations unchanged.
- **Native unchanged**: flutter `FILLS_IMAGE` partial, swiftui unsupported
  (documented non-goal).

### 6. Honesty audit lock

- The canonical fixture gains one image-fill node (e.g. `img:1`, an
  `IRFill(kind="image", image_ref="asset://photo")` on a small frame) — the
  plan node and the screen tree both carry it.
- `audit_backends()` calls `generate(..., options={"assets": {"img:1":
  {"path": "assets/photo.png", "kind": "image"}}})` so every web backend
  resolves the image and emits the real reference.
- `EXERCISED` gains `Feature.FILLS_IMAGE`; SIGNALS gains per-web-backend
  entries (`url(assets/photo.png)` for html_css/vue/svelte; `bg-[url(` for
  react_tailwind).
- The `test_audit_detects_a_silent_drop` coverage guard switches its example
  feature from `FILLS_IMAGE` (now exercised) to a feature that remains
  unexercised (`Feature.SVG_ASSETS`).

### 7. TS runtime

`invokeBackendGeneratorFromStages` gains an optional staged `assetsManifest`
(file staged to temp, `--assets` passed); `createGenerateStageHandler` passes
`ctx.shared["assetManifest"]` (already stored by the Part 17 assets stage) so a
full `figmaforge run` threads assets into code automatically. The legacy
`--file` fallback (ingest+generate only, no assets stage) stays without
`--assets` — unchanged behavior. `invokeBackendGenerator` (file mode) gains the
same optional param for the demo/standalone path.

### 8. Determinism & verification

- Same inputs + same run → byte-identical output (options map is
  node_id-sorted; asset paths are content-hash-derived).
- Render smoke: an html_css output with a resolved `file://` asset renders via
  the existing Playwright harness (CSS `url(<abs path>)` resolves under
  `file://`).
- Portability note (documented, out of scope): generated code references
  machine/run-local content-addressed paths; packaging assets with output is a
  deployment concern.

## Non-goals (documented)

- Native backends (flutter/swiftui) image-fill lift — declared statuses stay.
- SVG-specific handling beyond CSS `url()` (no `<svg>` inline embedding).
- `IMAGE_ASSETS`/`SVG_ASSETS` capability lift (no asset-bundle concept for web).
- Asset path rewriting for portability / bundling (webpack/vite asset imports).
- Touch to render/compare/repair/verify stages (still unwired).
