# Real-Figma End-to-End Demo — Six Backends Through the TS Runtime (Part 15) — Design Spec

## Context

- **Python side is complete**: `core/figma_client.py` (Part 12) already fetches real Figma files — `FigmaClient.get_file(file_key) -> FigmaFile`, credentials from the `FIGMA_TOKEN` env var with a clear `require_token()` error — and the pipeline `FigmaFile -> IRBuilder -> LayoutAnalyzer -> Resolver -> backends` is fully implemented and fixture-tested. Six backend adapters are real (Part 14): `html_css` (reference), `react_tailwind`, `vue`, `svelte`, `swiftui`, `flutter`, each `generate(document, layout_plan, resolution, viewport)` returning `GeneratedOutput(files, fidelity_losses, metadata)`.
- **TS runtime is scaffolding without stage handlers**: `runtime/src/core/pipeline.ts` defines `PIPELINE_STAGES` (ingest → normalize → resolve → layout → generate → assets → render → compare → repair → verify), a state machine with checkpoints/artifacts/budget, and `onStage(stage, handler)` — but **nothing ever calls `onStage`**. `cmdRun` in `main.ts` therefore completes with every stage skipped and empty results. The only generation in TS is `generateSimpleHtml(vnode, viewport)` — a fallback used by the `render` command path, NOT the Python backends.
- **The wiring surface already exists**: `createPythonTool(name, stage, script, desc)` (`runtime/src/core/tools.ts`) spawns `pythonBin script args...` with optional JSON stdin; `RuntimeConfig.pythonBin` (default `python3`, honored via `PYTHON_BIN`); `PRESET_TARGETS` + `CodegenTarget { framework, styling }` + `parseTargetKey`/`targetKey` (`types.ts`) already enumerate all six backends (`html+css`, `react+tailwind`, `vue+scoped_css`, `svelte+scoped_css`, `swiftui+swiftui_modifiers`, `flutter+flutter_widgets`); `render_handler.ts` (Part 11) already shells out to Python via stdin for browser rendering.
- **There is no Python CLI entry point** for the backends: no `plugin/figmaforge/scripts/` directory; adapters are only invoked from tests. This is the missing bridge.
- Repo rules: stdlib-only Python, deterministic output, `python3 -m unittest` (no pytest), golden/snapshot convention (`REWRITE_SNAPSHOTS=1`), TS minimal test framework (`runtime/tests/test_framework.ts`, `run_all.js`, 117 tests), `claude plugin validate --strict` green, no new dependencies.

## Decisions (proposed scope)

- **"Wire the six backends through the TS runtime" = give the pipeline its first real stage handlers.** Part 15 registers `ingest` and `generate` handlers that shell out to a new Python CLI, so `figmaforge run --file-key=... --target=flutter` actually produces backend code for the first time. The remaining stages (assets/render/compare/repair/verify) stay Python-side / out of scope.
- **One Python CLI with subcommands** — `plugin/figmaforge/scripts/pipeline.py`:
  - `pipeline.py ingest --file-key=<key> [--file <local.json>] [--out <path>]` → fetch (or read local) a Figma file, normalize, print the file JSON to stdout (one-JSON-line contract, like `pixel_diff.py`), optionally write it.
  - `pipeline.py generate --file <figmafile.json> --backend <name> [--resolution <resolution.json>] [--viewport <w>] [--out-dir <dir>]` → build IR → layout → resolution → backend `generate()`, write the emitted files under `--out-dir/<backend>/`, print a manifest JSON to stdout (`backend`, `files: [{path, language, node_ids}]`, `fidelity_losses`, `metadata`).
  - Errors: unknown backend → exit 2 with the valid list; `--file-key` without `FIGMA_TOKEN` → exit 3 with the documented token message; missing/invalid file → exit 4.
- **TS `generate` stage handler** (`main.ts`, via `onStage`): resolves `CodegenTarget → backend name` from a small explicit map in a new `runtime/src/core/backend_codegen.ts`, invokes the Python CLI with the target backend, stores the manifest + copied files as `generated_code` artifacts. The `ingest` handler fetches the file by key and stores it as the stage input. Keep the existing fallback paths untouched.
- **Demo + walkthrough**: a `figmaforge demo --file-key=...` command (or documented script) that ingests a real file and generates ALL SIX backends into `demo-out/`, printing a comparison table (files, fidelity losses per backend). With no `FIGMA_TOKEN`, it runs the identical flow against the checked-in `layout_desktop` fixture (offline path) and says so explicitly. A `docs/real-figma-demo.md` walkthrough documents both paths and the expected output.
- **Verification stays structural** (repo rule): no compilers (swiftc/dart/tsc) and no rendering of generated apps; web backends may render via the existing Part 11 harness when Playwright is available, as a best-effort demo step.

## Design

1. **`plugin/figmaforge/scripts/pipeline.py`** — stdlib-only CLI. `ingest`: `FigmaClient().get_file(key)` when a token is present, else `FigmaFile.from_dict(json.load(open(file)))` for the local path; emits normalized JSON (sorted keys, deterministic). `generate`: `IRBuilder` → `LayoutAnalyzer().analyze(doc, library=LibraryLoader().load())` → `resolver.resolve(...)` when `--resolution` is given (else the backend's own fallback) → `backend.generate(document, plan, resolution, viewport)` → write `file.path` contents under out-dir, emit the manifest. Manifest ordering is deterministic (backend name, sorted file paths, loss order from the backend).
2. **`runtime/src/core/backend_codegen.ts`** — `TARGET_BACKENDS: Record<string, string>` mapping every `PRESET_TARGETS` key to its Python backend name; `backendForTarget(target): string`; `invokeBackendGenerator(config, target, fileJson, outDir): Promise<{manifest, files}>` wrapping the spawn bridge (reuses `createPythonTool`'s mechanics — spawn `pythonBin scripts/pipeline.py generate ...`, pipe file JSON via stdin or temp file, parse the manifest line). Unknown target → typed error.
3. **Stage wiring in `main.ts`** — `pipeline.onStage("ingest", ...)` (fetch via Python, store `raw_file` artifact) and `pipeline.onStage("generate", ...)` (read the ingest output, call the backend, store `generated_code` artifacts + manifest). `run --target=<key>` resolves through `backendForTarget`; the existing "not a preset — will use backend registry to resolve" note becomes real.
4. **`demo` command** — `figmaforge demo [--file-key=...] [--file <local.json>] [--out demo-out] [--render]`: ingest once, generate all six backends, print the per-backend table (files, fidelity losses, node coverage), best-effort render of web backends when `--render` and Playwright are available. Exit codes mirror `pipeline.py`.
5. **Docs** — `docs/real-figma-demo.md` (walkthrough with both live-token and offline-fixture paths, sample output), DEVELOPMENT_LOG Part 15 entry, README status Parts 1–15 + counts, CLAUDE.md module line + counts, architecture.md status paragraph, Next Steps update (drop the real-Figma demo item).
6. **Testing** — Python: unittest for `pipeline.py` (each subcommand: ingest local file deterministic; generate each of the six backends → files + manifest + node coverage + losses present when the fixture has them; unknown backend; missing token; invalid file). TS: `backendForTarget` mapping covers all six keys and rejects unknowns; a generate-stage test runs the handler against the checked-in fixture file and asserts `generated_code` artifacts + manifest; a `demo` dry-run smoke against the fixture. Golden/snapshot convention only where output changes; otherwise assertion-based.

## Risk mitigations

1. **No Figma token in this environment** — the demo's live path is implemented and documented but verified locally through the offline fixture path (identical code path, only the fetch differs); the walkthrough shows the exact one-line token setup.
2. **TS pipeline has no handlers today** — the ingest/generate wiring is greenfield; keep handlers thin (spawn → parse → store) and cover with the TS test harness so the run command provably produces artifacts.
3. **Determinism** — sorted keys in emitted JSON, deterministic manifest ordering, stable file names; a determinism test (run twice → identical bytes) guards it.
4. **Scope creep** — assets/render/compare/repair/verify stages and the repair loop remain Python-side; no compilers, no CI, no Figma OAuth (token-only), no generated-app execution.

## Non-goals

- Wiring the remaining pipeline stages (assets/render/compare/repair/verify) into the TS runtime.
- Compiling or executing generated SwiftUI/Flutter/TSX code.
- Figma OAuth login flow (token-only, env-based).
- CI for the demo.
