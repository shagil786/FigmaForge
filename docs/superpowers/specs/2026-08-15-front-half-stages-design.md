# Front-Half Stage Wiring — normalize / resolve / layout through the TS runtime (Part 16) — Design Spec

## Context

- **Part 15 gave the TS pipeline its first real stages**: `ingest` (fetch/read a Figma file, `scripts/pipeline.py ingest`) and `generate` (lower through a backend, `scripts/pipeline.py generate --file …`). The other eight stages still have no handlers, so `figmaforge run` skips normalize/resolve/layout/assets/render/compare/repair/verify.
- **The Python front half is complete and fixture-tested**: `IRBuilder().build(FigmaFile)` → `IRDocument`; `Resolver(document, library).resolve()` → `ResolutionReport` (has `to_dict`/`report_to_json`, and the CLI already loads saved reports back via `_load_resolution`); `LayoutAnalyzer().analyze(document, library=…)` → `LayoutPlan` (has `to_dict`/`plan_to_json`). Backend `generate()` consumes `(document, layout_plan, resolution, viewport)`.
- **The missing piece is serialization round-trip.** `IRDocument` and `LayoutPlan` have `to_dict` but **no `from_dict`** — their JSON artifacts cannot be fed back into the next stage. Today `generate --file` recomputes IR → layout inside one process, so the front half is exercised only inside a single Python invocation.
- Repo rules: stdlib-only Python, deterministic output, `python3 -m unittest` (no pytest), golden/snapshot convention, TS minimal framework (124 tests), `claude plugin validate --strict` green, no new dependencies.
- Current baseline: Python **481** tests OK (40 files), TS **124** passing, tsc clean.

## Decisions (proposed scope)

1. **Each front-half stage becomes a real, composable step: its artifact is a first-class JSON document the next stage consumes.** This requires JSON round-trip loaders for the two artifacts that lack them:
   - `IRDocument.from_dict` (reconstruct the full IR tree: IRNode by kind, style fills/effects/typography, layout/position, instance/prototype/tokens/responsive/annotations, components/styles/variables maps, assets, unknown).
   - `LayoutPlan.from_dict` (LayoutNodePlan tree + Box/SizingSpec/SpacingSpec/AlignmentSpec/Anchoring/TextModel/OverflowSpec/BreakpointChange/ConstraintReport/Diagnostic, BreakpointPlan, counts/confidence/diagnostics).
   Loaders live as `from_dict` classmethods/section in `core/ir_types.py` and `core/layout_types.py` (matching the `figma_types` convention). Round-trip identity is locked by tests: `from_dict(x.to_dict()) == x` for fixture IR and plans, plus a JSON-deep-equal guard.
2. **`scripts/pipeline.py` gains three subcommands** (same one-JSON-line contract, deterministic):
   - `normalize --file <figma.json> [--out]` → `IRBuilder().build(FigmaFile.from_dict(…))` → `IRDocument.to_dict`, validated by `ir_validator.validate_ir`/`ensure_valid` before emission.
   - `resolve --file <ir.json> [--out]` → load IR via the new loader → `Resolver(document).resolve()` → `ResolutionReport.to_dict`.
   - `layout --file <ir.json> [--viewport <w>] [--out]` → load IR → `LayoutAnalyzer().analyze(document, library=LibraryLoader().load())` → `LayoutPlan.to_dict`.
   Exit codes match the existing contract (2 bad invocation, 3 missing token, 4 unreadable/invalid file, 1 unexpected).
3. **`generate` gains a staged mode** — `generate --ir <ir.json> --layout <layout.json> [--resolution <report.json>] --backend <name> --out-dir <dir>` — loading all three via the round-trip loaders (reusing the existing `_load_resolution`) and calling `backend.generate(document, plan, resolution, viewport)`. The existing `--file` mode (recompute) stays for compatibility and is tested to produce **byte-identical output** to the staged path (same pipeline inside, consistency guard).
4. **TS side** (`backend_codegen.ts`): `invokeNormalize`/`invokeResolve`/`invokeLayout` spawn helpers (mirroring `invokeIngest`), stage-handler factories `createNormalizeStageHandler`/`createResolveStageHandler`/`createLayoutStageHandler` that read the previous stage's JSON from `ctx.shared` (fileJson → irJson → resolutionJson/layoutJson), and `createGenerateStageHandler` prefers the staged path (`--ir --layout [--resolution]`) when the front-half artifacts are present, falling back to the legacy `--file` path otherwise. `main.ts` registers all five handlers in `cmdRun`, so `figmaforge run` exercises ingest → normalize → resolve → layout → generate for the first time. Artifact kinds already exist in `stageToArtifactKind` (normalize→design_ir, resolve→resolution_report, layout→layout_plan) — no pipeline changes needed beyond `setShared` usage.
5. **Verification stays structural** (repo rule): no compilers, no generated-app execution. Consistency is proven by byte-identical artifact/manifest comparisons, not by rendering.

## Design

```
figmaforge run --file=<fixture> --target=flutter+flutter_widgets
  ingest    → FigmaFile raw JSON            (pipeline.py ingest)          [existing]
  normalize → IRDocument JSON               (pipeline.py normalize)        [new]
  resolve   → ResolutionReport JSON         (pipeline.py resolve)          [new]
  layout    → LayoutPlan JSON               (pipeline.py layout)           [new]
  generate  → manifest + files              (pipeline.py generate --ir --layout [--resolution])
```

- **Python pipeline.py:** subcommand parser mirrors `ingest`/`generate`; each front-half subcommand prints one JSON line (`sort_keys=True`) and optionally writes `--out`. `normalize` runs `ir_validator.ensure_valid` on the built IR before emission (validation failure → exit 4 with the schema errors). `generate` staged mode validates that `--ir`/`--layout` are given together (exit 2 otherwise); `--file` and `--ir`/`--layout` are mutually exclusive (exit 2 if both or neither).
- **Round-trip loaders:** pure, deterministic, no I/O. `_compact`-style reconstruction (missing/None → defaults). Lists rebuilt in document order. Round-trip tests use the checked-in fixtures (`layout_desktop`, `variants`, `layout_mobile`, …) and the honesty-audit rich IR, not just one fixture.
- **TS handlers:** each spawns `pipeline.py <subcommand> --file <tmp>` (JSON staged to a temp file, cleaned up in `finally`, same pattern as `invokeBackendGenerator`), parses the single JSON line, stores it in `ctx.shared` under a stable key, and returns it (the coordinator auto-stores the stage artifact). `createGenerateStageHandler` inspects shared state: `irJson`+`layoutJson` → staged invocation; else legacy `--file`.
- **Testing:** Python — round-trip identity (IR + plan) across fixtures; each subcommand deterministic (two runs byte-identical); `resolve`/`layout` accept `normalize` output; `generate --ir --layout [--resolution]` manifest/files **byte-identical** to `generate --file`; error exits (missing file → 4, bad combination → 2). TS — a full pipeline run with all five handlers produces five artifact kinds (figma_raw, design_ir, resolution_report, layout_plan, generated_code); a stage-chain consistency test byte-compares the generate manifest against the Part-15 demo path; legacy fallback still works when only ingest+generate are registered.

## Risk mitigations

1. **Loader drift** — to_dict/from_dict diverging over time is the main risk. Mitigation: round-trip identity tests across every fixture + the rich audit IR, committed with the loaders; the repo-wide honesty audit already fails on any silent output change.
2. **Staged vs file mode drift** — if generate's two input modes ever diverge, the consistency test (byte-identical manifests + files) fails. Kept as a first-class test, not a one-off.
3. **TS shared-state coupling** — stage ordering is enforced by the coordinator's `PIPELINE_STAGES` order; handlers read only their immediate predecessor's key and fail with a clear message if it's absent (no silent recompute fallback inside the handler itself — only the generate handler has the documented legacy fallback).
4. **Scope creep** — assets/render/compare/repair/verify stages remain unwired; the repair loop stays Python-side. Demo command unchanged (it intentionally drives backends directly).

## Non-goals

- Wiring assets/render/compare/repair/verify into the TS runtime.
- Compiling or executing generated SwiftUI/Flutter/TSX code.
- Changing the `figmaforge demo` command's backend-direct path.
- Schema-version migration for old IR/layout JSON artifacts (loaders target the current `to_dict` shape only).
