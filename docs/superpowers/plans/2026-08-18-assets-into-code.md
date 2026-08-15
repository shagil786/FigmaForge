# Threading Resolved Assets into Generated Code (Part 18) — Implementation Plan

Branch: `feat/part-18-assets-into-code` (from `main` @ `40269c2`).
Spec: `docs/superpowers/specs/2026-08-18-assets-into-code-design.md`.
Conventions: Python stdlib-only + `python3 -m unittest`; TS minimal framework
(run via `PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/tests/run_all.js`);
deterministic; `claude plugin validate --strict` green; commits per task; final
gate at Task 7.

Current baseline: Python **515** tests OK (43 files), TS **131** passing, tsc clean.

---

## Task 1 — Pipeline plumbing + html_css reference emission (Python, test-first)

**Tests** (extend `tests/test_pipeline_cli.py` + `tests/test_backends.py`; red first):
1. `test_generate_assets_flag_rejects_invalid_manifest` — `generate --assets <bad.json>`
   exits 4 with a clear message (both `--file` and staged modes).
2. `test_generate_assets_emits_image_url` — build an IR with an image-fill node +
   an asset manifest entry for it (a `file://` path), run `generate --file --assets`;
   html_css output contains `background-image: url(<path>)` and **no**
   `fills_image approximated` marker; without `--assets` the same IR keeps the
   solid fallback + marker.
3. Staged-mode variant of (2) — `generate --ir/--layout --assets` behaves identically
   (byte-identical output to `--file --assets`).

**Implement**:
- `scripts/pipeline.py` — `_load_asset_manifest(path)` (exit 4 on invalid), builds
  `{node_id: {"path": local_path, "kind": kind}}` from `downloaded` entries;
  `generate` gains `--assets` (both modes) and passes
  `options={"assets": map}` when present.
- `backends/web_common.py` — `extend_ir_style(style, node, assets=None)`: visible
  `image` fill with `node.id in assets` → `background-image: url(<path>)` +
  `background-size: cover` + `background-position: center`; else unchanged fallback.
- `backends/html_css/__init__.py` — thread `assets` from `options` into
  `extend_ir_style` and the emitter; marker only when an image fill is unresolved.
  `image_fill_ids` splits into `resolved`/`unresolved` sets.

**Acceptance**: new tests green; full Python suite green (515 + N); commit
`feat(assets): thread resolved asset paths into html_css output (generate --assets)`.

---

## Task 2 — react_tailwind real image classes (Python, test-first)

**Tests** (extend `tests/test_react_tailwind_backend.py`; red first):
1. `test_image_fill_resolved_emits_url_class` — IR with image-fill node +
   `options={"assets": {node_id: {"path": "assets/photo.png", "kind": "image"}}}` →
   TSX contains `bg-[url(assets/photo.png)] bg-cover bg-center` and no fidelity marker.
2. `test_image_fill_unresolved_keeps_marker` — no assets in options → existing
   marker preserved.

**Implement**: react_tailwind's fill mapper gains an `assets` lookup — image fill +
resolved → `bg-[url(<path>)] bg-cover bg-center`; unresolved → current marker.

**Acceptance**: new tests green; full Python suite green; commit
`feat(assets): emit real tailwind image classes for resolved image fills`.

---

## Task 3 — vue + svelte scoped-CSS image lowering (Python, test-first)

**Tests** (extend `tests/test_vue_backend.py` + `tests/test_svelte_backend.py`; red first):
1. `test_image_fill_resolved_scoped_css` — vue and svelte with `options["assets"]`
   emit `background-image: url(<path>)` (+ cover/center) in their scoped style
   blocks and no marker.
2. `test_image_fill_unresolved_keeps_marker` — no assets → existing marker.

**Implement**: thread `assets` through both backends into `extend_ir_style` /
scoped CSS path; flip both emitters' marker condition to "unresolved only".

**Acceptance**: new tests green; full Python suite green; commit
`feat(assets): emit real scoped-css image references for vue + svelte`.

---

## Task 4 — Capability lift + honesty audit lock (Python, test-first)

**Tests** (extend `tests/test_backend_honesty_audit.py`; red first):
1. Canonical fixture gains an image-fill node; `EXERCISED` gains `FILLS_IMAGE`;
   `audit_backends()` passes `options={"assets": {img:1: {path, kind}}}`; SIGNALS
   entries for the four web backends (`url(assets/photo.png)` /
   `bg-[url(assets/photo.png)]`); `test_audit_detects_a_silent_drop` coverage
   guard switches to `Feature.SVG_ASSETS`.
2. `test_web_backends_declare_fills_image_supported` — the four web backends'
   `capabilities.supports(Feature.FILLS_IMAGE) == "supported"`; native unchanged
   (flutter partial, swiftui unsupported).

**Implement**: move `FILLS_IMAGE` from the four partial sets into supported;
docstring notes the resolved-asset requirement + degraded fallback; update the
audit fixture/signals/options.

**Acceptance**: audit + all Python tests green; commit
`feat(backends): lift fills_image to supported for the web backends + audit lock`.

---

## Task 5 — TS wiring (TS, test-first)

**Tests** (extend `runtime/tests/backend_codegen.test.ts`; red first):
1. `test_staged_generate_with_assets_emits_image` — build an IR with an
   image-fill node + normalize it, stage the layout, call
   `invokeBackendGeneratorFromStages(..., { assetsManifest })` for
   `html+css`; output contains `background-image: url(` and no marker.
2. `test_generate_stage_handler_threads_asset_manifest` — six-stage run where
   the IR carries a doc-level asset (`file://`) that the assets stage resolves:
   the generated html_css file contains the resolved asset's path.

**Implement**:
- `backend_codegen.ts` — `invokeBackendGeneratorFromStages` gains
  `assetsManifest?` (stage manifest to temp, `--assets`); `invokeBackendGenerator`
  gains the same optional param.
- `createGenerateStageHandler` — passes `ctx.shared["assetManifest"]` as
  `--assets` in staged mode (legacy `--file` fallback unchanged).

**Acceptance**: TS **131 + 2** passing, tsc clean; Python suite still green;
commit `feat(runtime): thread the asset manifest into staged generate`.

---

## Task 6 — Docs

1. `docs/real-figma-demo.md` — assets → code section: `generate` now emits real
   image references (per-backend examples), the `--assets` flag, portability note.
2. `docs/DEVELOPMENT_LOG.md` — Part 18 entry (seam, four backends, audit lock,
   TS wiring, counts).
3. `README.md` — status header Parts 1–18 + counts + Part 18 checklist lines;
   Next Steps drop the assets-into-code item if present.
4. `CLAUDE.md` — module lines + counts (43+ test files, 515+N / 131+2).
5. `docs/architecture.md` — `options["assets"]` seam in the backend adapter /
   pipeline bullets + status paragraph.

**Commit**: `docs: document Part 18 assets-into-code`.

---

## Task 7 — Final gate + PR (do NOT merge)

1. Python full suite → 515 + N OK, zero skips.
2. TS: `npx tsc` clean; run_all → 131 + 2 passing.
3. `claude plugin validate --strict plugin/figmaforge` → ✔.
4. Smoke: `figmaforge run` with an IR carrying a resolved `file://` asset →
   html_css artifact contains the asset URL; deterministic second run.
5. Fill DEVELOPMENT_LOG counts with actual N (amend or follow-up commit if
   Task 6's numbers differ).
6. `git status --short` empty; `git push -u origin feat/part-18-assets-into-code`;
   `gh pr create --base main --title "feat: Part 18 assets-into-code — real image references in web backend output"`.
   Do NOT merge (repo convention — user's call).
