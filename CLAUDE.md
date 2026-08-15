# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 1. Project Overview

FigmaForge is a technology-agnostic, adaptive, full-lifecycle Claude Code engineering platform. It is implemented as a Claude Code plugin, NOT as a standalone application. It enables any software project type by detecting stack-specific signals and routing to the appropriate capabilities dynamically, without requiring per-repo authoring of agents, skills, or workflows.

This is a complete platform implementation (version 0.0.1-dev), containing a 100-role catalog, detection algorithms, a deterministic routing engine, a 10-phase lifecycle state machine, safety hooks, and MCP/LSP templates.

## 2. Technology Stack

- **Code:** Python 3 (standard library only, with one user-approved exception: `playwright` for browser rendering — Part 11) for detection, routing, lifecycle state, and hooks. TypeScript (Node.js stdlib only) for the orchestration runtime.
- **Data:** JSON (`.claude-plugin/plugin.json`, `catalog/roles.json`, `hooks/hooks.json`).
- **Interfaces:** Claude Code Plugin constraints and schema structures.
- **No Application Framework:** The repository does NOT use React, Webpack, Vite, FastAPI, etc. Python pipeline uses core Python. Runtime uses TypeScript with zero external runtime dependencies.
- **No Agent Frameworks:** No ADK, LangGraph, CrewAI, Temporal, or any orchestration framework.

## 3. Important Repository Structure

- `LICENSE` — MIT License (preserved exactly as 2026 Md Shagil Nizami).
- `CLAUDE.md` — This repository-wide configuration file.
- `.claude/settings.json` — Minimal configurations mapping for Claude Code (no secrets).
- `.mcp.json` — Contains a project-scoped mapping (`stdio` server `pinchtab`).
- `plugin/figmaforge/` — The primary source code and content:
  - `core/` (Python): The full Figma-to-Code pipeline:
    - **Ingestion:** `figma_client.py`, `figma_types.py`, `figma_errors.py`, `figma_fixtures.py`, `normalizer.py`
    - **IR Pipeline:** `ir_types.py`, `ir_builder.py`, `ir_validator.py` (stdlib-only JSON-Schema draft-07)
    - **Resolution:** `resolver.py`, `matcher.py`, `component_index.py`, `variant_resolver.py`, `token_resolver.py`, `library_types.py`
    - **Layout:** `layout_engine.py` (988 lines), `layout_types.py`, `layout_analyzer.py`, `constraint_model.py`, `breakpoint_model.py`
    - **Generators:** `react_generator.py` (wired to ResolutionReport), `css_generator.py` (all sizing modes), `generator_types.py`
    - **Backend Adapters (Part 10):** `backends/protocol.py` (BackendAdapter ABC, Feature vocabulary, FidelityLoss), `backends/registry.py` (discover/register/lookup), `backends/html_css/` (fully implemented), `backends/react_tailwind/`, `backends/vue/`, `backends/svelte/`, `backends/swiftui/`, `backends/flutter/` (stubs)
    - **Platform:** `detector.py`, `router.py` (trigger→phase + language→domain scoring), `catalog.py`, `state.py` (adjacent-only transitions)
    - **Assets & Diff:** `asset_handler.py`, `asset_manager.py` (content-addressed, SVG security), `figma_assets.py` (baseline download), `png_codec.py` + `pixel_diff.py` (stdlib pixel diffing + CLI + shared SSIM verdict), `ssim.py` (pure-stdlib perceptual SSIM, Part 13), `render_harness.py`, `diff_engine.py` (per-category scoring + capped pixel weight + regional SSIM gating)
    - **Repair Loop (Part 8):** `repair_classifier.py` (9 categories), `patch_planner.py` (strategy-ordered), `patch_executor.py` (with rollback), `repair_loop.py` (iteration controller), `repair_history.py` (manifest)
    - **Hooks:** `hooks/session_detector.py`, `hooks/external_mutation_gate.py` (regex matching), `hooks/post_edit_validator.py` (executes validators)
  - `catalog/`: `roles.json` (100 roles across 10 domains), `roles.json`.
  - `agents/`: `context-scout.md`, `lifecycle-planner.md`, `fresh-verifier.md`.
  - `skills/`: `route.md`, `lifecycle.md`, `doctor.md`, `mcp-template.md`, `lsp-template.md`, `demo.md`.
  - `hooks/`: `hooks.json` mapping.
  - `schemas/`: `design-ir.schema.json`, `layout-plan.schema.json`, `resolution-report.schema.json`, `detection.schema.json`, `router.schema.json`, `task-state.schema.json`.
  - `templates/`: Inert examples for MCP and LSP configurations.
  - `library/`: `components.json` (5 project components), `tokens.json` (12 design tokens).
  - `tests/` (33 test files, 395 tests): Unit, integration, snapshot, property-based, repair-loop, backend adapter, render-harness, pixel-diff, SSIM-gating, and baseline-refresh tests.
- `runtime/` (Part 9 — TypeScript Orchestration Runtime):
  - `src/core/` (15 modules): `types.ts` (composable `CodegenTarget = { framework, styling }`), `events.ts`, `checkpoint.ts`, `artifacts.ts`, `tools.ts`, `state.ts`, `budget.ts`, `retry.ts`, `security.ts`, `pipeline.ts`, `evaluation.ts`, `providers.ts`, `screenshot_compare.ts`, `render_handler.ts`, `index.ts`
  - `src/cli/main.ts`: CLI with 6 commands (run, inspect, render, compare, repair, replay) + `--target=<framework+styling>` flag
  - `tests/` (3 files, 117 tests): Comprehensive test suite with custom test framework
  - `evaluation/fixtures/golden/`: 3 golden fixtures (simple-button, login-screen, card-layout)
- `docs/architecture.md` — In-depth architectural blueprint.
- `docs/DEVELOPMENT_LOG.md` — Part-by-part development log with decisions and verification.
- `docs/design-ir.md`, `docs/layout.md`, `docs/resolution.md`, `docs/repair-loop.md` — Module-specific design docs.
- `docs/runtime-architecture.md` — Runtime architecture and module reference.
- `docs/runtime-troubleshooting.md` — Runtime troubleshooting guide.

Nested `CLAUDE.md` files should NOT be created. The structure is global to the plugin domain.

## 4. Runtime Architecture and Data Flow

1. **User Request:** Arrives in natural language.
2. **SessionStart / Lifecycle Hooks:** Validate and analyze context silently.
3. **Detector:** Analyzes repository artifacts (manifests, languages, tools) strictly through signatures.
4. **Router:** Deterministically outputs phases, roles, and execution execution_mode based on signals and catalog roles, generating an actionable pipeline.
5. **Phase Router:** Integrates with the 10-phase lifecycle state machine (`.figmaforge/runs/<run-id>/state.json`). Transitions are evidence-based, recorded as localized append-only events.
6. **Delivery:** The chosen roles provide actionable inputs for Claude Code to execute safely.

## 5. Verified Development Commands

> **Environment note (Python version):** this repository requires **Python 3.10+**
> (the code uses PEP 604 union annotations such as `str | Path`). On this machine
> the default `python3` on PATH is 3.9.6 and fails with
> `TypeError: unsupported operand type(s) for |` — use the Homebrew interpreter:
> `export PYTHON_BIN=/opt/homebrew/bin/python3.14` (or call `python3.14` directly)
> before running the suites below. The TypeScript runtime honors `PYTHON_BIN` for all its
> Python shell-outs (`render_handler.ts`, `screenshot_compare.ts`, and the CLI via
> `buildConfig`), e.g. `PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/tests/run_all.js`
> or `PYTHON_BIN=/opt/homebrew/bin/python3.14 node dist/runtime/src/cli/main.js compare ...`.

* **Run all tests (395 tests):**
  `cd plugin/figmaforge && python3 -m unittest discover -s tests -v`
* **Run runtime tests (117 tests):**
  `npx tsc && node dist/runtime/tests/run_all.js`
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
  `claude plugin validate --strict plugin/figmaforge`
* **Load plugin:**
  `claude --plugin-dir ./plugin/figmaforge`

## 6. Coding Conventions

- **Reuse Constraints:** No new libraries or dependencies (e.g. LangGraph, CrewAI, ADK). Rely on Python stdlib and structured Claude prompts natively.
- **Architecture Stability:** Keep the 10-domain 100-role catalog format static unless an architectural RFC is approved. 
- **Evidence Over Inference:** Rely solely on explicit JSON/Manifest signals via `detector.py`.
- **Atomic Operations:** File outputs for states must remain atomic (`LifecycleState`).

## 7. Testing Requirements

- All 395 Python tests across 33 test files must pass (`python3 -m unittest discover -s tests`); browser-render tests skip cleanly without chromium.
- All 117 TypeScript runtime tests must pass (`npx tsc && node dist/runtime/tests/run_all.js`).
- Test categories: unit tests, integration tests, golden-file snapshot tests, property-based tests, perceptual (SSIM) gating tests.
- Snapshot tests use `REWRITE_SNAPSHOTS=1` to regenerate golden files after intentional output changes.
- Adding a new module requires corresponding test coverage.
- Adding a new role requires verifying the schema matches (`claude plugin validate --strict`).

## 8. Safety Rules

- **PreToolUse external-mutation gate**: Prevents unintended or malicious outbound/external/infrastructure state changes. It flags shell patterns like `git push`, `terraform apply`, and `kubectl delete`.
- **Inert Templates ONLY:** Ensure `templates/mcp/*` remain purely inert structures without exposed tokens or destructive pathways. 
- Never expose or copy credentials.
- No deploying, committing, rotating secrets, or destructive actions without express, structured gates.

## 9. Change Workflow

1. Discuss architecture impact (review `docs/architecture.md`).
2. Run full test suite (`python3 -m unittest discover -s tests` from `plugin/figmaforge/`).
3. Make atomic, minimal coherent changes explicitly matching schemas.
4. Verify all 395 tests pass and regenerate snapshots if output changed intentionally.
5. Update `docs/DEVELOPMENT_LOG.md` with the change entry.
6. Only document verified, executable routines.

## 10. Definition of Done

- Scope is strictly adhered to (no speculative integrations).
- All changes maintain schema constraints (run `claude plugin validate --strict`).
- Full test suite passes: `python3 -m unittest discover -s tests` (395 tests, 33 files).
- Changes align exactly with architectural constraints in `docs/architecture.md`.
- `docs/DEVELOPMENT_LOG.md` updated with the change entry.
- No exposed credentials, secrets, or unintentional active `.lsp.json`/`.mcp.json` templates exist.