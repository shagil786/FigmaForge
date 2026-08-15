# Front-Half Stage Wiring — normalize / resolve / layout through the TS runtime (Part 16) — Implementation Plan

Branch: `feat/part-16-front-half-stages` (from `main` @ `31d0962`).
Spec: `docs/superpowers/specs/2026-08-15-front-half-stages-design.md`.
Conventions: Python stdlib-only + `python3 -m unittest`; TS minimal framework (`runtime/tests`, run via `PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/tests/run_all.js`); deterministic; `claude plugin validate --strict` green; commits per task; final gate at Task 5.

Current baseline: Python **481** tests OK (40 files), TS **124** passing, tsc clean.

---

## Task 1 — JSON round-trip loaders: `IRDocument.from_dict` + `LayoutPlan.from_dict` (Python, test-first)

**Tests** (new `tests/test_ir_roundtrip.py` + `tests/test_layout_roundtrip.py`, red first):
1. `test_ir_roundtrip_fixture_desktop` — build IR from `layout_desktop` (and `variants`, `layout_mobile`, `layout_nested`) → `IRDocument.from_dict(doc.to_dict())` equals `doc` (dataclass equality) **and** `to_dict()` of the reloaded doc deep-equals the original JSON.
2. `test_ir_roundtrip_rich_audit_ir` — the honesty-audit rich IR (gradients, shadows, blur, per-corner radius, overflow, breakpoints, instances, tokens) round-trips.
3. `test_ir_roundtrip_empty_document` — a minimal IRDocument (no root, empty maps) round-trips.
4. `test_layout_roundtrip_fixture_desktop` — plan from `layout_desktop` → `LayoutPlan.from_dict(plan.to_dict())` equals `plan`; `to_dict` of reloaded plan deep-equals original.
5. `test_layout_roundtrip_mobile` — a second fixture (different breakpoints/sizing) round-trips.
6. `test_layout_roundtrip_single_node` — a minimal one-node plan (no box, no text) round-trips.

**Implement:**
- `core/ir_types.py`: add a loader section — `IRDocument.from_dict(data)` plus per-dataclass loaders (IRSource, IRPosition, IRLayout, IRStyle + IRFill/IRShadow/IRBlur/IRBorder/IRGradientStop/IRCornerRadii, IRTypography, IRTextContent, IRLink, IRInstance, IRTokenRef/IRTokens, IResponsive, IRInteraction/IRPrototype, IRAnnotations, IRComponent, IRToken, IRNode by kind — building the node tree recursively from `children`, splitting `pages` vs root via `is_page`). Pure, deterministic, `_compact`-faithful (missing keys → defaults).
- `core/layout_types.py`: add `LayoutPlan.from_dict(data)` with loaders for LayoutNodePlan (recursive `children`), Box, SizingSpec, SpacingSpec, AlignmentSpec, Anchoring, TextModel, OverflowSpec, BreakpointChange, ConstraintReport, Diagnostic, BreakpointPlan, and the top-level counts/confidence/diagnostics.
- Round-trip identity is the contract; no behavioral changes to existing producers.

**Acceptance**: Task 1 tests green; full Python suite still **481** OK; commit `feat(core): IR + LayoutPlan JSON round-trip loaders`.

---

## Task 2 — pipeline.py front-half subcommands + generate staged mode (Python, test-first)

**Tests** (extend `tests/test_pipeline_cli.py`; red first):
1. `test_normalize_deterministic_and_valid` — `normalize --file <layout_desktop.json>` prints one JSON line that `IRDocument.from_dict` accepts; two runs byte-identical.
2. `test_normalize_invalid_file` — `normalize --file <bad.json>` exits 4.
3. `test_resolve_consumes_normalize_output` — `resolve --file <(normalize output)>` exits 0, prints a `ResolutionReport`-shaped JSON (has `counts`), two runs byte-identical.
4. `test_layout_consumes_normalize_output` — `layout --file <(normalize output)>` exits 0, prints a `LayoutPlan`-shaped JSON (has `screens`), two runs byte-identical.
5. `test_layout_invalid_ir` — `layout --file <non-ir json>` exits 4 (loader failure surfaced as a user error).
6. `test_generate_staged_equals_file_mode` — for `react_tailwind` (and spot-check `flutter`): `generate --file X` vs `generate --ir <(normalize X) --layout <(layout IR) --resolution <(resolve IR)>` produce **byte-identical manifests and file bytes**.
7. `test_generate_staged_requires_both` — `--ir` without `--layout` (or neither) exits 2 with a clear message; `--file` together with `--ir` exits 2.

**Implement** (`scripts/pipeline.py`):
- Add `normalize` subcommand: `--file` (exit 4 unreadable/invalid), `--out` optional; `IRBuilder().build(FigmaFile.from_dict(file_key, raw))` → `ir_validator.ensure_valid(doc)` → emit `doc.to_dict()`.
- Add `resolve` subcommand: `--file` (IR JSON), `--out`; load IR via `IRDocument.from_dict` → `Resolver(document).resolve()` → emit `report.to_dict()`.
- Add `layout` subcommand: `--file` (IR JSON), `--viewport` float default 1440.0, `--out`; load IR → `LayoutAnalyzer().analyze(document, library=LibraryLoader().load())` → emit `plan.to_dict()`.
- Rework `generate`: staged mode `--ir <ir.json> --layout <layout.json> [--resolution <report.json>]` (loaders + existing `_load_resolution`) vs legacy `--file` (recompute); mutually exclusive (exit 2 on both/neither); shared manifest emission.
- Keep the one-JSON-line stdout contract and exit-code table for every subcommand.

**Acceptance**: Task 2 tests green (pipeline CLI suite grows to ~20); full Python suite green (481 + N); commit `feat(scripts): pipeline normalize/resolve/layout subcommands + staged generate`.

---

## Task 3 — TS stage handlers for normalize/resolve/layout + run wiring (TS, test-first)

**Tests** (extend `runtime/tests/backend_codegen.test.ts`; red first):
1. `test_front_half_stages_chain` — register all five handlers (ingest, normalize, resolve, layout, generate) on a pipeline with `target = flutter` and `setShared("filePath", FIXTURE)`; run completes; artifacts exist for all five kinds (figma_raw, design_ir, resolution_report, layout_plan, generated_code); the generated-code manifest names the flutter file.
2. `test_staged_generate_matches_legacy` — capture the generated-code artifact JSON from the five-handler run; byte-compare its manifest against `invokeBackendGenerator` (file mode) on the same fixture → identical manifests.
3. `test_missing_ir_fails_normalize_chain` — register normalize/resolve/layout/generate WITHOUT ingest: normalize fails with a clear "no fileJson" message (stage error surfaces).
4. `test_legacy_generate_fallback` — register only ingest+generate (Part-15 shape): still completes (generate falls back to `--file`).

**Implement** (`runtime/src/core/backend_codegen.ts` + `runtime/src/cli/main.ts`):
- `invokeNormalize`/`invokeResolve`/`invokeLayout` (spawn helpers parsing the single JSON line, temp-file staging, `finally` cleanup) and handler factories `createNormalizeStageHandler`/`createResolveStageHandler`/`createLayoutStageHandler` reading `ctx.shared`: fileJson → irJson; irJson → resolutionJson; irJson → layoutJson.
- `invokeBackendGeneratorFromStages(cfg, target, { irJson, layoutJson, resolutionJson? }, outDir, options)` — spawns `generate --ir … --layout … [--resolution …]`.
- `createGenerateStageHandler` prefers the staged path when `irJson`+`layoutJson` are present; else legacy `--file`.
- `main.ts cmdRun`: register all five handlers (ingest, normalize, resolve, layout, generate). Help text: note that run exercises the full front half.

**Acceptance**: TS suite **124 + 4** passing, tsc clean; `figmaforge run --file=<fixture> --target=flutter+flutter_widgets` produces design_ir + resolution_report + layout_plan + generated_code artifacts; commit `feat(runtime): wire normalize/resolve/layout stages to the python pipeline`.

---

## Task 4 — Docs

1. `docs/real-figma-demo.md` — note that `run` now exercises the full front half (five real stages) and where each artifact lands.
2. `docs/DEVELOPMENT_LOG.md` — Part 16 entry (round-trip loaders, three subcommands, staged generate, five-stage run, counts).
3. `README.md` — status header Parts 1–16 + counts; Part 16 checklist lines; Next Steps drop the "wire remaining pipeline stages" item (front half done; assets/render/compare/repair/verify remain).
4. `CLAUDE.md` — module line (`ir round-trip loaders`, `backend_codegen.ts` stage count) + test counts.
5. `docs/architecture.md` — status paragraph: TS runtime drives the full front half via stage handlers.

**Commit**: `docs: document Part 16 front-half stage wiring`.

---

## Task 5 — Final gate + PR (do NOT merge)

1. Python full suite → expect **481 + ~17** OK, zero skips.
2. TS: `npx tsc` clean; run_all → expect **124 + 4** passing.
3. `claude plugin validate --strict plugin/figmaforge` → ✔.
4. Fill the DEVELOPMENT_LOG Part 16 counts with actual N; amend or follow-up commit.
5. `figmaforge run` smoke once more (all five stages, artifact kinds present, deterministic second run).
6. `git status --short` empty; push (`git push -u origin feat/part-16-front-half-stages`); `gh pr create --base main --title "feat: Part 16 front-half stage wiring — normalize/resolve/layout through the TS runtime"`. Do NOT merge (repo convention — user's call).
