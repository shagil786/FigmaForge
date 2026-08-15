# Real-Figma End-to-End Demo — Six Backends Through the TS Runtime (Part 15) — Implementation Plan

Branch: `feat/part-15-real-figma-demo` (from `main` @ `76b70d4`).
Spec: `docs/superpowers/specs/2026-08-15-real-figma-demo-design.md`.
Conventions: Python stdlib-only + `python3 -m unittest`; TS minimal framework (`runtime/tests`, run via `PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/tests/run_all.js`); deterministic; `claude plugin validate --strict` green; commits per task; final gate at Task 5.

Current baseline: Python **471** tests OK (39 files), TS **117** passing, tsc clean.

---

## Task 1 — `plugin/figmaforge/scripts/pipeline.py` CLI (Python, test-first)

**Tests** (`tests/test_pipeline_cli.py`, run red against a missing script):
1. `test_ingest_local_file_deterministic` — `pipeline.py ingest --file fixtures/figma/layout_desktop.json` prints one JSON line parseable as `FigmaFile` (has `file_key`, `name`, `pages`); two runs → byte-identical stdout.
2. `test_ingest_missing_token_error` — `ingest --file-key=abc` with `FIGMA_TOKEN` unset → exit 3, stderr contains "FIGMA_TOKEN".
3. `test_generate_all_six_backends` — for each backend name in the registry (`html_css`, `react_tailwind`, `vue`, `svelte`, `swiftui`, `flutter`): `generate --file <fixture json> --backend <name> --out-dir <tmp>` exits 0, writes ≥1 file, prints a manifest JSON line with `backend`, `files` (each `path`/`language`/`node_ids`), `fidelity_losses`, `metadata`.
4. `test_generate_manifest_deterministic` — two runs → identical manifest line + identical file bytes.
5. `test_generate_unknown_backend` — exit 2, stderr lists the valid backend names.
6. `test_generate_missing_file` — exit 4 with a clear message.
7. `test_generate_node_coverage` — for `react_tailwind`, the manifest `node_ids` of the screen file cover the plan's screen node ids (fixture pipeline, same assertion style as `test_html_css_emit_smoke`).

**Implement** `scripts/pipeline.py`:
- argparse subcommands `ingest` / `generate`.
- `ingest`: `--file-key` (needs `FIGMA_TOKEN` → `FigmaClient().require_token()`; exit 3 on missing) or `--file <local.json>`; `--out` optional; validate the payload with `FigmaFile.from_dict` (the fixtures' loader shape — `FigmaFile` has no `to_dict`, its `raw` field holds the untouched response `from_dict` accepts), print one JSON line of that raw payload with deterministic key ordering.
- `generate`: `--file` (exit 4 if unreadable/invalid), `--backend` (validate against `get_registry().names()`; exit 2 + list), `--resolution <json>` optional, `--viewport` float default 1440.0, `--out-dir` default `generated`; pipeline: `IRBuilder().build(FigmaFile.from_dict(...))` → `LayoutAnalyzer().analyze(doc, library=LibraryLoader().load())` → optional resolution load → `backend.generate(...)` → write each `file.path` under `out-dir/<backend>/` → print manifest (deterministic: backend, sorted file paths, losses in backend order, metadata).
- Everything through the same fixture pipeline as the smoke tests; no new deps.

**Acceptance**: Task 1 tests green; full Python suite still **471** OK; commit `feat(scripts): python backend pipeline CLI (ingest/generate)`.

---

## Task 2 — TS runtime wiring: target map + generate stage handler (TS, test-first)

**Tests** (new `runtime/tests/backend_codegen.test.ts` or extend `test_all.ts`; red first):
1. `test_target_backend_map_covers_presets` — every `PRESET_TARGETS` key maps to a real Python backend name (`html_css`, `react_tailwind`, `vue`, `svelte`, `swiftui`, `flutter`).
2. `test_unknown_target_rejected` — `backendForTarget("vue+css")` throws a typed error.
3. `test_generate_stage_produces_artifacts` — register the `generate` handler and run the pipeline against a local fixture file (the checked-in `layout_desktop.json`), `target = flutter`: pipeline completes, `generated_code` artifacts exist, the manifest parses and names the flutter file, fidelity losses are present in the manifest.
4. `test_generate_stage_unknown_backend_error` — target mapping to a missing backend name surfaces a non-zero exit as a stage error.

**Implement**:
- `runtime/src/core/backend_codegen.ts`: `TARGET_BACKENDS` (key → backend), `backendForTarget(target)`, `invokeBackendGenerator(config, target, filePath | fileJson, outDir)` reusing the spawn bridge (mirror `createPythonTool`'s spawn mechanics with `ctx.pythonBin`), parsing the manifest JSON line; returns `{ manifest, filesDir }`.
- `runtime/src/cli/main.ts`: register `onStage("ingest", ...)` (spawn `scripts/pipeline.py ingest --file-key`, store the JSON as the stage output/artifact) and `onStage("generate", ...)` (use `config.target` + ingest output → `invokeBackendGenerator`, store manifest + copy files into the artifact store). `run` resolves `--target` through `backendForTarget`; keep the non-preset note but make it meaningful (it already resolves).

**Acceptance**: TS suite **117 + N** passing, tsc clean; `figmaforge run --file-key=<fixture key> --target=flutter --plugin-dir plugin/figmaforge --no-approval` completes with a generated-code artifact (verified locally with the fixture path); commit `feat(runtime): wire ingest+generate stages to the python backends`.

---

## Task 3 — `demo` command + offline/live walkthrough (Python+TS)

**Tests**:
1. TS: `test_demo_command_offline_fixture` — `figmaforge demo --file <layout_desktop.json> --out <tmp>` (no token) exits 0 and writes one output directory per backend (six), plus a summary table with per-backend file counts and loss counts.
2. Python: reuse Task 1 tests; add `test_all_six_generated_in_demo_layout` if the demo has its own orchestrator module — otherwise the demo is CLI glue over `pipeline.py` (keep glue thin, no new Python module).

**Implement**:
- `runtime/src/cli/main.ts` `demo` command: ingest once (local file or `--file-key`), then for each of the six backends call `invokeBackendGenerator`, collect manifests, print a deterministic table (backend | files | losses | node coverage), `--render` best-effort (web backends only, via the existing render handler when Playwright is present; failures degrade to a note, never a hard error).
- Offline fixture path when no `FIGMA_TOKEN` and no `--file` is given: use `plugin/figmaforge/fixtures/figma/layout_desktop.json` with an explicit message.

**Acceptance**: `demo` dry-run green on the fixture; TS suite +Python suite green; commit `feat(runtime): figmaforge demo command (six backends)`.

---

## Task 4 — Docs

1. `docs/real-figma-demo.md` — walkthrough: token setup (`export FIGMA_TOKEN=...`), live path (`figmaforge demo --file-key=<key> --render`), offline fixture path, expected per-backend table + sample output, troubleshooting (exit codes 2/3/4).
2. `docs/DEVELOPMENT_LOG.md` — Part 15 entry (pipeline.py CLI, first real TS stage handlers, demo command, counts).
3. `README.md` — status header Parts 1–15 + new counts; checklist lines (Part 15); Next Steps drop the real-Figma demo item.
4. `CLAUDE.md` — module line (`scripts/pipeline.py`, `backend_codegen.ts`) + test counts.
5. `docs/architecture.md` — status paragraph: TS runtime now drives the Python backends via stage handlers.

**Commit**: `docs: document Part 15 real-Figma demo`.

---

## Task 5 — Final gate + PR (do NOT merge)

1. Python full suite: `PYTHON_BIN=/opt/homebrew/bin/python3.14 python3.14 -m unittest discover -s tests` → **expect 471 + ~7 OK, zero skips**.
2. TS: `npx tsc` clean; `PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/tests/run_all.js` → **expect 117 + ~4 passing**.
3. `claude plugin validate --strict plugin/figmaforge` → ✔.
4. Fill the DEVELOPMENT_LOG Part 15 counts with actual N; amend or follow-up commit.
5. `demo` smoke once more (offline path, all six backends, no crashes, deterministic second run).
6. `git status --short` empty; push (`git push -u origin feat/part-15-real-figma-demo`); `gh pr create --base main --title "feat: Part 15 real-Figma demo — six backends through the TS runtime"`. Do NOT merge (repo convention — user's call).
