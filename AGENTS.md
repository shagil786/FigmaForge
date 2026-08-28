# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 1. Project Overview

FigmaForge is a technology-agnostic, adaptive, full-lifecycle Codex engineering platform. It is implemented as a Codex plugin, NOT as a standalone application. It enables any software project type by detecting stack-specific signals and routing to the appropriate capabilities dynamically, without requiring per-repo authoring of agents, skills, or workflows. The adaptive preflight core is host-neutral; the Codex-specific layer is the plugin manifest, skills, agents, hooks, and `/figmaforge:*` command UX.

This is a complete platform implementation (version 0.0.1-dev), containing a 100-role catalog, detection algorithms, a deterministic routing engine, a 10-phase lifecycle state machine, safety hooks, and MCP/LSP templates.

## 2. Technology Stack

- **Code:** Python 3 (standard library only, with one user-approved exception: `playwright` for browser rendering — Part 11) for detection, routing, lifecycle state, and hooks. TypeScript (Node.js stdlib only) for the orchestration runtime.
- **Data:** JSON (`.Codex-plugin/plugin.json`, `catalog/roles.json`, `hooks/hooks.json`).
- **Interfaces:** Codex Plugin constraints and schema structures.
- **No Application Framework:** The repository does NOT use React, Webpack, Vite, FastAPI, etc. Python pipeline uses core Python. Runtime uses TypeScript with zero external runtime dependencies.
- **No Agent Frameworks:** No ADK, LangGraph, CrewAI, Temporal, or any orchestration framework.

## 3. Important Repository Structure

- `LICENSE` — MIT License (preserved exactly as 2026 Md Shagil Nizami).
- `AGENTS.md` — This repository-wide configuration file.
- `.Codex/settings.json` — Minimal configurations mapping for Codex (no secrets).
- `.mcp.json` — Contains a project-scoped mapping (`stdio` server `pinchtab`).
- `plugin/figmaforge/` — The primary source code and content:
  - `core/` (Python): The full Figma-to-Code pipeline:
    - **Ingestion:** `figma_client.py`, `figma_types.py`, `figma_errors.py`, `figma_fixtures.py`, `normalizer.py`
    - **IR Pipeline:** `ir_types.py`, `ir_builder.py`, `ir_validator.py` (stdlib-only JSON-Schema draft-07); `IRDocument.from_dict` JSON round-trip loader (Part 16)
    - **Resolution:** `resolver.py`, `matcher.py`, `component_index.py`, `variant_resolver.py`, `token_resolver.py`, `library_types.py`
    - **Layout:** `layout_engine.py` (988 lines), `layout_types.py`, `layout_analyzer.py`, `constraint_model.py`, `breakpoint_model.py`
    - **Generators:** `react_generator.py` (wired to ResolutionReport), `css_generator.py` (all sizing modes), `generator_types.py`
    - **Backend Adapters (Parts 10 + 14 + 18):** `backends/protocol.py` (BackendAdapter ABC, Feature vocabulary, FidelityLoss), `backends/registry.py` (discover/register/lookup), `backends/web_common.py` (shared VStyle/VNode + CssStyleGenerator + ScopedCssGenerator + extend_ir_style — Part 18: resolved image fills become real `background-image` via `options["assets"]`), and six implemented adapters: `backends/html_css/` (reference), `backends/react_tailwind/` (TSX + Tailwind config, `bg-[url(...)]`), `backends/vue/` (SFC), `backends/svelte/` (component), `backends/swiftui/` (modifier chains), `backends/flutter/` (widget trees)
    - **Platform:** `detector.py`, `router.py` (trigger→phase + language→domain scoring), `catalog.py`, `state.py` (adjacent-only transitions)
    - **Assets & Diff:** `asset_handler.py`, `asset_manager.py` (content-addressed, SVG security), `asset_collector.py` (Part 17: deterministic `AssetRef` collection), `figma_assets.py` (baseline download + public `default_transport`/`fetch_with_retry`), `png_codec.py` + `pixel_diff.py` (stdlib pixel diffing + CLI + shared SSIM verdict), `ssim.py` (pure-stdlib perceptual SSIM, Part 13), `render_harness.py`, `diff_engine.py`, `bundler_harness.py` (Part 21: deterministic Vite scaffold for react/vue/svelte — pinned deps, multi-page entries, asset copy + `url(...)` rewrite, ephemeral-port serve + screenshot — so bundler-required outputs render real screenshots) (per-category scoring + capped pixel weight + regional SSIM gating)
    - **Repair Loop (Part 8):** `repair_classifier.py` (9 categories), `patch_planner.py` (strategy-ordered), `patch_executor.py` (with rollback), `repair_loop.py` (iteration controller), `repair_history.py` (manifest)
    - **Hooks:** `hooks/session_detector.py`, `hooks/external_mutation_gate.py` (regex matching), `hooks/post_edit_validator.py` (executes validators)
    - **Pipeline CLI (Parts 15–22):** `scripts/pipeline.py` — `ingest`, `normalize`, `resolve`, `layout`, `assets`, `generate`, `render`, and `repair` subcommands (one-JSON-line contract, exit codes 2/3/4); `generate` supports staged mode (`--ir/--layout/[--resolution]` + `--assets`, byte-identical to `--file` recompute) and threads the asset manifest into generated code via `options["assets"]`; `assets` downloads + content-addresses IR image/SVG refs (SVG-validated, deterministic manifest); `render` has four modes (`--html` shot, `--ir --layout` reference baseline via the shared web lowering, `--baselines` live Figma download, `--bundle` scaffold + build + serve + screenshot of react/vue/svelte through `bundler_harness.py`); `repair` threads backend/resolution/assets and regenerates the measured web backend; consumed by the TS runtime
  - `catalog/`: `roles.json` (100 roles across 10 domains), `roles.json`.
  - `agents/`: `context-scout.md`, `lifecycle-planner.md`, `fresh-verifier.md`.
  - `skills/`: `route.md`, `lifecycle.md`, `doctor.md`, `mcp-template.md`, `lsp-template.md`, `demo.md`.
  - `hooks/`: `hooks.json` mapping.
  - `schemas/`: `design-ir.schema.json`, `layout-plan.schema.json`, `resolution-report.schema.json`, `detection.schema.json`, `router.schema.json`, `task-state.schema.json`.
  - `templates/`: Inert examples for MCP and LSP configurations.
  - `library/`: `components.json` (5 project components), `tokens.json` (12 design tokens).
  - `tests/` (51 test files): Unit, integration, snapshot, property-based, repair-loop, backend adapter (six real generators + 5 golden snapshots), capability-vs-output honesty audit, render-harness, pixel-diff, SSIM-gating, baseline-refresh, pipeline-CLI, IR/layout round-trip, asset-collector, assets-CLI, assets-into-code, render-CLI, repair-planner, repair-CLI, component-fallback, bundler-harness, and bundler buildability/smoke tests.
- `runtime/` (Part 9 — TypeScript Orchestration Runtime):
  - `src/core/` (16 modules): `types.ts` (composable `CodegenTarget = { framework, styling }`), `events.ts`, `checkpoint.ts`, `artifacts.ts`, `tools.ts`, `state.ts`, `budget.ts`, `retry.ts`, `security.ts`, `pipeline.ts`, `evaluation.ts`, `providers.ts`, `screenshot_compare.ts`, `render_handler.ts`, `backend_codegen.ts` (Parts 15–22: target→backend map, Python CLI invocation, ingest/normalize/resolve/layout/assets/generate/render/compare/repair/verify stage handlers + staged generate + asset-manifest threading + backend-aware repair regeneration + the `ctx.updateMetrics` metrics seam + shared compare-baseline for repair/verify + the bundler render path — `invokeBundleRender` for react/vue/svelte with a `--no-bundle` escape), `index.ts`
  - `src/cli/main.ts`: CLI with 7 commands (run, inspect, render, compare, repair, replay, demo) + `--target=<framework+styling>` and `--file=<path>` flags
  - `tests/` (3 files): Custom test framework with fast runtime coverage plus explicit integration coverage (backend-codegen stage, demo, six-stage front-half + assets-into-code, eight-stage render/compare, ten-stage repair/verify, and bundler-rendered measurement CLI tests)
  - `evaluation/fixtures/golden/`: 3 golden fixtures (simple-button, login-screen, card-layout)
- `docs/architecture.md` — In-depth architectural blueprint.
- `docs/DEVELOPMENT_LOG.md` — Part-by-part development log with decisions and verification.
- `docs/design-ir.md`, `docs/layout.md`, `docs/resolution.md`, `docs/repair-loop.md` — Module-specific design docs.
- `docs/runtime-architecture.md` — Runtime architecture and module reference.
- `docs/runtime-troubleshooting.md` — Runtime troubleshooting guide.

Nested `AGENTS.md` files should NOT be created. The structure is global to the plugin domain.

## 4. Runtime Architecture and Data Flow

1. **User Request:** Arrives in natural language.
2. **SessionStart / Lifecycle Hooks:** Validate and analyze context silently.
3. **Detector:** Analyzes repository artifacts (manifests, languages, tools) strictly through signatures.
4. **Router:** Deterministically outputs phases, roles, and execution execution_mode based on signals and catalog roles, generating an actionable pipeline.
5. **Phase Router:** Integrates with the 10-phase lifecycle state machine (`.figmaforge/runs/<run-id>/state.json`). Transitions are evidence-based, recorded as localized append-only events.
6. **Delivery:** The chosen roles provide actionable inputs for Codex to execute safely.

`figmaforge run` can also opt into an adaptive detector/router preflight with `--adaptive` or `--adaptive-request=<text>`. The runtime stores an `adaptive_plan` artifact and emits an `adaptive_plan_created` event before the visual pipeline starts. `--adaptive` uses the deterministic default request, `--adaptive-request` implies adaptive mode, and an `unclassified` plan is still recorded and allowed to continue.

Native backend acceptance is available through
`python3 plugin/figmaforge/scripts/native_acceptance.py --fixture <path>`;
it validates SwiftUI and Flutter manifests and runs only the native syntax
checks available on the host.

## 5. Verified Development Commands

> **Environment note (Python version):** this repository requires **Python 3.10+**
> (the code uses PEP 604 union annotations such as `str | Path`). On this machine
> the default `python3` on PATH is 3.9.6 and fails with
> `TypeError: unsupported operand type(s) for |` — use the Homebrew interpreter:
> `export PYTHON_BIN=/opt/homebrew/bin/python3.14` (or call `python3.14` directly)
> before running the suites below. The TypeScript runtime honors `PYTHON_BIN` for all its
> Python shell-outs (`render_handler.ts`, `screenshot_compare.ts`, and the CLI via
> `buildConfig`), e.g. `PYTHON_BIN=/opt/homebrew/bin/python3.14 node runtime/dist/tests/run_all.js`
> or `PYTHON_BIN=/opt/homebrew/bin/python3.14 node runtime/dist/src/cli/main.js compare ...`.

* **Run the fast Python tests:**
  `python3 -m unittest discover -s plugin/figmaforge/tests -p 'test*.py' -v` (real-toolchain bundler tests may require a configured environment)
* **Run the fast runtime tests (122 tests):**
  `cd runtime && npm run build && npm test`
* **Run Python/Chromium/Vite integration tests:**
  `cd runtime && npm run test:integration`
* **Install browser rendering dependencies (required for the render stage):**
  `pip install playwright && playwright install chromium`
* **Run a specific test module:**
  `python3 -m unittest tests.test_router -v`
  `python3 -m unittest tests.test_css_generator -v`
  `python3 -m unittest tests.test_state_machine -v`
  `python3 -m unittest tests.test_diff_engine -v`
  `python3 -m unittest tests.test_repair_loop -v`
* **Regenerate golden-file snapshots:**
  `REWRITE_SNAPSHOTS=1 python3 -m unittest tests.test_ir_snapshot tests.test_layout_snapshot tests.test_resolution_snapshot tests.test_generator_snapshot`
* **Plugin validation:**
  `Codex plugin validate --strict plugin/figmaforge`
* **Load plugin:**
  `Codex --plugin-dir ./plugin/figmaforge`

## 6. Coding Conventions

- **Reuse Constraints:** No new libraries or dependencies (e.g. LangGraph, CrewAI, ADK). Rely on Python stdlib and structured Codex prompts natively.
- **Architecture Stability:** Keep the 10-domain 100-role catalog format static unless an architectural RFC is approved. 
- **Evidence Over Inference:** Rely solely on explicit JSON/Manifest signals via `detector.py`.
- **Atomic Operations:** File outputs for states must remain atomic (`LifecycleState`).

## 7. Testing Requirements

- The fast Python suite must pass; real-toolchain bundler tests run in the explicit integration tier.
- The fast TypeScript runtime tier must pass (`cd runtime && npm run build && npm test`).
- Test categories: unit tests, integration tests, golden-file snapshot tests, property-based tests, perceptual (SSIM) gating tests.
- Snapshot tests use `REWRITE_SNAPSHOTS=1` to regenerate golden files after intentional output changes.
- Adding a new module requires corresponding test coverage.
- Adding a new role requires verifying the schema matches (`Codex plugin validate --strict`).

## 8. Safety Rules

- **PreToolUse external-mutation gate**: Prevents unintended or malicious outbound/external/infrastructure state changes. It flags shell patterns like `git push`, `terraform apply`, and `kubectl delete`.
- **Inert Templates ONLY:** Ensure `templates/mcp/*` remain purely inert structures without exposed tokens or destructive pathways. 
- Never expose or copy credentials.
- No deploying, committing, rotating secrets, or destructive actions without express, structured gates.

## 9. Change Workflow

1. Discuss architecture impact (review `docs/architecture.md`).
2. Run full test suite (`python3 -m unittest discover -s tests` from `plugin/figmaforge/`).
3. Make atomic, minimal coherent changes explicitly matching schemas.
4. Verify the fast tiers pass and run the integration tier when browser/Vite dependencies are available.
5. Update `docs/DEVELOPMENT_LOG.md` with the change entry.
6. Only document verified, executable routines.

## 10. Definition of Done

- Scope is strictly adhered to (no speculative integrations).
- All changes maintain schema constraints (run `Codex plugin validate --strict`).
- Fast test tiers pass; integration status is reported separately when toolchain-dependent tests are run.
- Changes align exactly with architectural constraints in `docs/architecture.md`.
- `docs/DEVELOPMENT_LOG.md` updated with the change entry.
- No exposed credentials, secrets, or unintentional active `.lsp.json`/`.mcp.json` templates exist.
