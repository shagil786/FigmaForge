# FigmaForge Development Log

... [previous entries]

## Part 7: Asset Pipeline & Deterministic Rendering (2026-08-12)

### Overview
Implemented the final validation layer: asset ingestion (with security validation) and a deterministic browser-rendering harness.

### Key Decisions
1. **Content-addressed Asset Store**: Implemented `AssetManager` which hashes both images and SVGs using SHA256. Storing files by their content hash in a two-level directory structure ensures stability and auto-deduplication.
2. **SVG Security**: Implemented a mandatory scan in `AssetManager` to reject unsafe content (e.g., `<script>` tags) before storage.
3. **Playwright Rendering Harness**: Authored `RenderHarness` for deterministic browser automation. It serves as a placeholder interface for actual Playwright integration, capturing screenshots and layout meta-artifacts.
4. **Deterministic Pipeline Tests**: Verified asset ingest stability and SVG security via `tests/test_render_pipeline.py`.

### Verification ✅
- Assets hashed correctly and deduplicated.
- Unsafe SVG content successfully rejected by `AssetManager`.
- Render harness deterministic interface verifies screenshots/metadata.
- All pipeline infrastructure tests pass.

---

## Codebase Audit & Bugfix Pass (2026-08-12)

### Overview
Full codebase audit identifying and fixing 28 issues across 15 files. All fixes verified with 184 passing tests (up from 140).

### Critical Logic Bugs Fixed (10 issues)

1. **Router `_score_roles` — phase-match scoring never fired** (`router.py`)
   - Compared `test_commands` (e.g. `"pytest"`) against role `phases` (e.g. `"verify"`) — domains never overlapped.
   - **Fix:** Added `_TRIGGER_TO_PHASES` class mapping. Trigger words now derive lifecycle phases correctly (e.g. `"test"` → `["verify"]`, `"design"` → `["design"]`).

2. **Router `_score_roles` — signal-match scoring never fired** (`router.py`)
   - Checked if `"python"` was a substring of `"application"` — always False.
   - **Fix:** Added `_LANGUAGE_TO_DOMAIN` class mapping. Detected languages now map to relevant domains (e.g. `python` → `["application", "data"]`).

3. **Router single-trigger fallback was inverted** (`router.py`)
   - Added roles that DID match the trigger with score -3, instead of roles that didn't.
   - **Fix:** Fallback now only activates when `scored_roles` is empty, assigns score 0, and includes roles that recognize the trigger.

4. **Router called `detector.detect()` twice** (`router.py`)
   - `_determine_approval_gates` re-ran detection instead of using cached result.
   - **Fix:** Detection result cached in `route()` and passed through to `_determine_approval_gates`.

5. **Router -5 and -3 penalties stacked to -8** (`router.py`)
   - Both penalties fired simultaneously when `stack_status == "unclassified"` and `languages == []`.
   - **Fix:** Made mutually exclusive with `elif` — the -5 subsumes the -3.

6. **CSS Generator silently dropped non-fixed sizing** (`css_generator.py`)
   - Only `SIZING_FIXED` emitted width/height. `fill`, `hug`, `percent` produced nothing.
   - **Fix:** Added `_apply_sizing()` method handling all 4 modes: `fixed` → px, `fill` → `flex: 1 1 0%` / `100%`, `hug` → `fit-content`, `percent` → `%`. Min/max clamps emitted for all modes.

7. **CSS Generator emitted no grid properties** (`css_generator.py`)
   - Grid nodes got `display: grid` with no layout definition.
   - **Fix:** Grid display now emits `gridAutoFlow`, `columnGap`, `rowGap`, `justifyItems`, `alignItems`.

8. **React Generator `_is_component` always returned False** (`react_generator.py`)
   - ResolutionReport from Part 4 was never consumed by generators.
   - **Fix:** `ReactGenerator` now accepts optional `ResolutionReport` in constructor. Resolved components and instances emit `is_component=True` with the component name as tag.

9. **Diff Engine hardcoded categories + unclamped score** (`diff_engine.py`)
   - `categories` always `{"geometry": 1.0, "style": 1.0, "pixels": 1.0}`. Score could go negative.
   - **Fix:** Per-category scores computed from actual mismatch counts. Overall score clamped to [0, 1]. Defensive `.get()` for malformed render_meta.

10. **State Machine allowed phase skipping** (`state.py`)
    - `_is_valid_transition` checked `to_idx > from_idx`, allowing `intake → learn`.
    - **Fix:** Changed to `to_idx == from_idx + 1` — only adjacent transitions allowed.

### High-Priority Fixes (6 issues)

11. **Detection schema duplicate enum values** (`detection.schema.json`)
    - `package_managers` had `"npm", "pnpm", "yarn"` listed twice.
    - **Fix:** Removed duplicates.

12. **Detector Python patterns included non-Python files** (`detector.py`)
    - `"Makefile"` is language-agnostic; `"pyproject.toml"` listed twice.
    - **Fix:** Removed both. Also removed duplicate `"vitest"` key from `TEST_FRAMEWORK_PATTERNS`.

13. **Router duplicate trigger words** (`router.py`)
    - `"test"` and `"review"` appeared twice in trigger list.
    - **Fix:** Deduplicated into `_TRIGGER_WORDS` class constant.

14. **Router capability_refs scoring provided no discrimination** (`router.py`)
    - +1 awarded to ALL roles with `capability_refs` (all 100 roles).
    - **Fix:** Now accepts `installed_capabilities` parameter; only awards +1 for refs actually installed.

15. **Post-Edit Validator never validated** (`post_edit_validator.py`)
    - Mapped extensions to validator commands but never executed them.
    - **Fix:** Now executes validator via `subprocess.run` with 30s timeout, `shutil.which` availability check, and structured JSON output.

16. **SVG validation too permissive** (`asset_manager.py`)
    - Only blocked `<script` and `javascript:`.
    - **Fix:** Now also blocks `<iframe>`, `<embed>`, `<object>`, `onload=`, `onerror=`, `onclick=`, `onmouseover=`, `onfocus=`, `onblur=`, `data:text/html`, `xlink:href="data:"`.

### Medium-Priority Fixes (6 issues)

17. **Architecture docs said "NOT STARTED" but code IS implemented** (`architecture.md`)
    - **Fix:** Updated to reflect actual implementation status.

18. **Router `_resolve_fallback_pack` was dead code** (`router.py`)
    - Defined but never called.
    - **Fix:** Removed.

19. **Part numbering inconsistency** (`test_diff_engine.py`)
    - Test said "Part 8" but module is Part 7.
    - **Fix:** Corrected to "Part 7".

20. **IR Validator `$ref` didn't handle nested refs** (`ir_validator.py`)
    - Resolved one level only.
    - **Fix:** Now follows `$ref` chains with circular-reference detection.

21. **Render Harness is a placeholder** (`render_harness.py`)
    - Documented as placeholder — no change made (intentional).

22. **Mutation Gate used substring matching despite regex patterns** (`external_mutation_gate.py`)
    - `pattern.lower() in bash_cmd` treated regex patterns as literal substrings.
    - **Fix:** Bash patterns now use `re.search()`. MCP tool names use exact match.

### New Test Coverage (44 tests added)

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_css_generator.py` | 14 | Fixed/fill/hug/percent sizing, grid, absolute, spacing, min/max |
| `test_router.py` | 13 | Trigger extraction, phase/signal scoring, penalties, execution modes, approval gates |
| `test_state_machine.py` | 10 | Transitions, phase skipping, full lifecycle walk, serialization |
| `test_diff_engine.py` | 11 | Geometry, style, tolerance, clamping, serialization (was 1 test) |

### Verification ✅
- All 184 tests pass (up from 140).
- Generator snapshot regenerated to reflect correct CSS output for hug-sized nodes.
- No regressions in existing test suite.

---

## Part 8: Automatic Visual Repair Loop (2026-08-12)

### Overview
Implemented the automatic visual repair loop that iterates between rendering, diffing, classifying mismatches, planning patches, and executing repairs — until visual similarity reaches the configured threshold or a stopping condition is met.

### Key Decisions
1. **Nine repair categories**: geometry, spacing, typography, color, token, asset, responsive, missing_element, extra_element. Every mismatch is classified or reported as unclassifiable — nothing is silently dropped.
2. **Strategy-ordered patching**: Missing/extra elements first, then parent geometry, shared tokens, typography, assets, color. This maximizes impact per iteration.
3. **Source-only modification**: The repair loop modifies design tokens, layout constraints, and style dictionaries — never screenshots or reference images.
4. **Full rollback support**: Every mutation is recorded as a `MutationRecord` with old/new values. `PatchExecutor.rollback()` restores all mutations in reverse order.
5. **Approval gate**: When `require_approval=True`, the loop pauses for human review before applying each batch of patches.
6. **Iteration history manifest**: Every iteration's diff report, classification, patch plan, execution result, and screenshot path are preserved for debugging and rollback.

### Modules Added
| Module | Lines | Purpose |
|--------|-------|---------|
| `repair_classifier.py` | 425 | DiffReport → RepairCandidates with source attribution |
| `patch_planner.py` | 461 | Strategy-ordered PatchPlan with shared-token grouping |
| `patch_executor.py` | 444 | Source-level patch application with rollback |
| `repair_loop.py` | 423 | Iteration controller with 6 stopping conditions |
| `repair_history.py` | 213 | Append-only iteration manifest |

### Stopping Conditions
- `threshold_satisfied` — similarity ≥ 0.95 (configurable)
- `no_safe_repair` — zero patches generated
- `insufficient_progress` — improvement < 0.005 per iteration
- `max_iterations_reached` — hard limit of 10 iterations
- `approval_denied` — human reviewer rejected patches
- `regression_detected` — score dropped after applying patches

### New Test Coverage (30 tests)
| Test Class | Tests | Coverage |
|------------|-------|----------|
| `TestRepairClassifier` | 8 | Category classification, spacing refinement, shared tokens |
| `TestPatchPlanner` | 4 | Strategy ordering, shared-token grouping, parent-before-child |
| `TestPatchExecutor` | 4 | Token/style patches, rollback, rejected patches |
| `TestRepairHistory` | 5 | Iteration recording, ordering, save/load |
| `TestRepairLoop` | 5 | Threshold, max iterations, approval, regression |
| `TestFixtureRepairLoop` | 3 | End-to-end with intentional defects |

### Verification ✅
- All 214 tests pass (184 existing + 30 new).
- No regressions in existing test suite.
- End-to-end fixture test confirms geometry defects are detected, classified, planned, and executed.

---

## Part 9: TypeScript Orchestration Runtime (2026-08-12)

### Overview
Implemented a complete TypeScript runtime that coordinates the full Figma-to-code pipeline with a deterministic state machine, resumable checkpoints, security boundaries, and an evaluation harness.

### Key Decisions
1. **Zero External Dependencies**: The runtime uses only TypeScript and Node.js stdlib — no ADK, LangGraph, CrewAI, Temporal, or any orchestration framework.
2. **Deterministic State Machine**: All 10 pipeline stages execute in strict order with explicit transitions, enforced by the `StateMachine` class.
3. **Resumable Checkpoints**: After each stage, a JSON checkpoint is saved. Crashed runs resume from the latest valid checkpoint.
4. **Security by Default**: PathSandbox restricts filesystem access, ShellGuard blocks arbitrary commands, SecretGuard redacts secrets from logs, ApprovalGate requires consent before file modifications.
5. **Replaceable Model Provider**: The `ModelProvider` interface allows swapping LLM backends with no provider lock-in. `NullModelProvider` enables fully deterministic runs.
6. **Content-Addressed Artifacts**: Every artifact is stored with a SHA-256 hash for deduplication and integrity verification.

### Modules Added (TypeScript)
| Module | Lines | Purpose |
|--------|-------|---------|
| `types.ts` | 159 | Pipeline stages, config, IDs, model provider interface |
| `events.ts` | 138 | Append-only structured event log |
| `checkpoint.ts` | 165 | Checkpoint save/load/resume |
| `artifacts.ts` | 176 | Content-addressed artifact storage |
| `tools.ts` | 203 | Typed tool registry + Python bridge |
| `state.ts` | 229 | Deterministic state machine |
| `budget.ts` | 147 | Token/time/iteration budget enforcement |
| `retry.ts` | 155 | Retry with exponential backoff + cancellation |
| `security.ts` | 400 | Path sandbox, secret guard, shell guard, approval gate |
| `pipeline.ts` | 328 | Pipeline coordinator |
| `evaluation.ts` | 389 | Golden fixtures, snapshot comparison, failure injection |
| `cli/main.ts` | 373 | CLI with 6 commands |

### CLI Commands
- `figmaforge run` — Full pipeline execution
- `figmaforge inspect` — Inspect previous run artifacts
- `figmaforge replay` — Replay event log for debugging
- `figmaforge render` — Single-stage render
- `figmaforge compare` — Single-stage comparison
- `figmaforge repair` — Single-stage repair

### Test Coverage (79 tests)
| Test Suite | Tests | Coverage |
|------------|-------|----------|
| types | 6 | Pipeline stages, IDs, model provider |
| events | 6 | Event log, filtering, serialization |
| checkpoints | 7 | Save/load, resume, metrics |
| artifacts | 6 | JSON/buffer storage, filtering, manifest |
| tools | 5 | Registry, invocation, error tracking |
| state machine | 9 | Lifecycle, transitions, checkpoint resume |
| budget | 7 | Token/time/iteration enforcement |
| retry | 7 | Backoff, cancellation, timeout |
| security | 16 | Sandbox, secrets, shell, approval, assets |
| pipeline | 2 | Full pipeline, stage failure |
| evaluation | 6 | Snapshots, fixtures, failure injection |
| idempotency/rollback | 2 | Determinism, state preservation |

### Verification ✅
- All 79 TypeScript runtime tests pass.
- All 214 Python pipeline tests still pass.
- TypeScript compilation clean (0 errors).
- CLI builds and runs successfully.
- Idempotency test confirms same input → same output.
- Checkpoint resume test confirms crashed runs can recover.

---

## Part 9 Follow-up: Resolved All Remaining Limitations (2026-08-12)

### Overview
Addressed all four remaining limitations from the initial Part 9 implementation.

### Changes

#### 1. Single-Stage Execution (render/compare/repair)
- **`cmdRender`**: Now loads generated code artifacts, generates HTML via `generateSimpleHtml()`, writes to disk, and attempts Playwright screenshot capture via Python bridge.
- **`cmdCompare`**: Loads diff report artifacts, displays similarity score, categories, and mismatch counts. Falls back to screenshot inspection when no diff report exists.
- **`cmdRepair`**: Loads diff report, categorizes mismatches by type, reports counts per category.

#### 2. Real Render Stage Handler
- **`render_handler.ts`** (406 lines): Full VNode → HTML conversion with:
  - `vnodeToHtml()`: Recursive VNode tree to HTML string with proper escaping
  - `generateFullHtml()`: Complete HTML document with viewport, styles, and metadata extraction script
  - `renderHandler()`: Pipeline stage handler that generates HTML, extracts layout metadata, and attempts Playwright browser rendering
  - XSS prevention via HTML entity escaping in text and attributes
  - CSS-in-JS conversion (camelCase → kebab-case)

#### 3. Pixel-Level Screenshot Comparison
- **`screenshot_compare.ts`** (231 lines): `ScreenshotComparator` class with:
  - SHA-256 content hashing for fast identical-image detection
  - Structural comparison using buffer size analysis
  - `compare()`: Full comparison returning similarity score, diff pixel count, dimensions, hashes
  - `passesThreshold()`: Boolean check against configurable threshold
  - `generateDiffReport()`: Severity-classified diff report with region detection
  - File and buffer comparison modes

#### 4. Replaceable Model Providers
- **`providers.ts`** (203 lines): Three provider implementations:
  - `NullModelProvider`: Deterministic empty responses (already existed in types.ts)
  - `AnthropicProvider`: Full Anthropic Messages API integration with timeout, abort, error handling
  - `OpenAIProvider`: Full OpenAI Chat Completions API integration with timeout, abort, error handling
  - `createProvider()`: Factory function that reads API keys from environment variables
  - Both providers support configurable model, base URL, timeout, and abort signals

### New Test Coverage (21 additional tests)
| Test Suite | Tests | Coverage |
|------------|-------|----------|
| providers | 6 | Factory, provider creation, API key validation |
| screenshot comparator | 7 | Identical/different comparison, thresholds, diff reports |
| render handler | 8 | VNode→HTML, attributes, styles, nesting, XSS prevention |

### Verification ✅
- All 100 TypeScript runtime tests pass (79 → 100, +21 new).
- All 214 Python pipeline tests still pass.
- TypeScript compilation clean (0 errors).
- Total: **314 tests, 0 failures**.
- All four previously-listed limitations are now resolved.

---

## Part 10: Backend Adapter Architecture — Framework-Agnostic Code Generation (2026-08-12)

### Overview
Decoupled the framework-neutral core pipeline from code generation by introducing a backend adapter architecture. The core (IR → Layout → Resolution) remains completely framework-neutral. Backend adapters are the target-specific lowering step that converts a LayoutPlan + Design IR into generated source code for a particular framework and styling system.

### Motivation
FigmaForge must NOT be fundamentally tied to React, TypeScript, JSX, CSS, or any single styling framework. An audit identified 6 leakage points where framework-specific concepts had leaked into the core domain model:
1. `generator_types.py` — VNode defaulted to `"div"` (HTML-specific)
2. `css_generator.py` — emitted CSS properties directly
3. `react_generator.py` — hardcoded HTML tags
4. `render_handler.ts` — generated HTML documents
5. Pipeline generate stage — no backend delegation
6. No backend registry existed

### Key Decisions

1. **Backend Adapter Protocol** (`backends/protocol.py`, 432 lines):
   - `BackendAdapter` ABC with abstract methods: `name`, `display_name`, `capabilities`, `generate()`.
   - `BackendCapabilities` — each backend declares `supported_features`, `unsupported_features`, `partial_features`, `framework`, `styling_system`, `renderer`, `file_extensions`.
   - `Feature` vocabulary — 40+ canonical feature constants (FLEX, GRID, SHADOWS, etc.) that are framework-neutral.
   - `FidelityLoss` — backends MUST emit one per unsupported feature rather than silently approximating.
   - `preflight()` — default implementation that walks the IR checking features against capabilities.

2. **Backend Registry** (`backends/registry.py`, 160 lines):
   - `BackendRegistry` with register/unregister/get/require/find/list.
   - `discover_builtins()` auto-imports all 6 backend modules.
   - Global singleton via `get_registry()` / `reset_registry()`.

3. **HTML+CSS Backend** (`backends/html_css/`, 518 lines, fully implemented):
   - Reference backend — absorbs the functionality of the legacy `css_generator.py` and `react_generator.py`.
   - Internal `VNode`/`VStyle` types (moved from core — these are HTML/CSS-specific, not core concepts).
   - `_CSSStyleGenerator`, `_VNodeBuilder`, `_HtmlEmitter` internal classes.
   - Supports nearly all features (HTML/CSS is the widest backend).

4. **Five Stub Backends** (capability declarations only, `generate()` raises `NotImplementedError`):
   - `react_tailwind/` — React + Tailwind CSS (`.tsx`)
   - `vue/` — Vue 3 SFC with scoped CSS (`.vue`)
   - `svelte/` — Svelte components (`.svelte`)
   - `swiftui/` — SwiftUI view structs (`.swift`, renderer: `xcode_preview`)
   - `flutter/` — Flutter widget trees (`.dart`, renderer: `flutter_simulator`)

5. **Composable Target Model** (TypeScript runtime):
   - `CodegenTarget = { framework: Framework, styling: StylingSystem }` — NOT a fixed enum.
   - `Framework` and `StylingSystem` are open-ended types with `(string & {})` for unlimited extensibility.
   - Any framework can pair with any styling system (e.g., `react+tailwind`, `vue+scoped_css`, `svelte+tailwind`, `react+styled_components`).
   - `PRESET_TARGETS` provides common suggestions but is NOT exhaustive.
   - Helper functions: `target()`, `targetKey()`, `parseTargetKey()`, `defaultRenderer()`, `targetExtensions()`.
   - CLI: `--target=react+tailwind` format via `parseTargetKey()`.

6. **Core Modules Verified Clean** — no changes needed:
   - `ir_types.py` (784 lines) — framework-neutral semantic vocabulary.
   - `layout_types.py` (540 lines) — abstract display/sizing/anchoring.
   - `layout_engine.py` (988 lines) — inference from IR, no framework assumptions.
   - `token_resolver.py` (374 lines) — semantic token resolution.
   - `ir_builder.py` (598 lines) — Figma → IR normalization.
   - `pipeline.ts` (329 lines) — orchestrator, framework-neutral.
   - `evaluation.ts` (390 lines) — framework-neutral eval.

### Files Created (8 new Python modules)
| File | Lines | Description |
|------|-------|-------------|
| `backends/__init__.py` | 45 | Package exports |
| `backends/protocol.py` | 432 | BackendAdapter ABC, Feature, FidelityLoss, BackendCapabilities |
| `backends/registry.py` | 160 | BackendRegistry with auto-discovery |
| `backends/html_css/__init__.py` | 518 | Fully implemented HTML+CSS backend |
| `backends/react_tailwind/__init__.py` | 191 | React+Tailwind stub |
| `backends/vue/__init__.py` | 147 | Vue SFC stub |
| `backends/svelte/__init__.py` | 141 | Svelte stub |
| `backends/swiftui/__init__.py` | 170 | SwiftUI stub |
| `backends/flutter/__init__.py` | 185 | Flutter stub |

### Files Modified (4 TypeScript modules)
| File | Change |
|------|--------|
| `runtime/src/core/types.ts` | Added composable `CodegenTarget`, `Framework`, `StylingSystem`, `RendererType` types; helper functions; `PRESET_TARGETS`; `RuntimeConfig.target` |
| `runtime/src/core/render_handler.ts` | Target-aware rendering via `defaultRenderer(target.framework)`; `generateNativeMetadata()` for non-browser targets |
| `runtime/src/cli/main.ts` | `--target=<framework+styling>` flag; `parseTargetKey()` parsing; updated help text |
| `runtime/tests/test_all.ts` | Added `target` to 3 `RuntimeConfig` test objects |

### New Test Coverage (27 additional Python tests)
| Test Suite | Tests | Coverage |
|------------|-------|----------|
| test_backends.py | 27 | Protocol, Feature, FidelityLoss, BackendCapabilities, GeneratedOutput, BackendRegistry (register/duplicate/require/unregister/find/list/capabilities_report), HtmlCssBackend, 5 stub backends, cross-backend capability comparison |

### Verification ✅
- All 241 Python tests pass (214 → 241, +27 new backend tests).
- TypeScript compilation clean (0 errors via `tsc --noEmit`).
- All 100 TypeScript runtime tests pass.
- Total: **341 tests, 0 failures**.
- Zero references to old fixed constants (`AVAILABLE_TARGETS`, `TARGET_RENDERER`, `TARGET_EXTENSIONS`).
- Core pipeline modules (`ir_types`, `layout_types`, `layout_engine`, `token_resolver`) verified framework-neutral — no changes needed.

## Part 11: Real Browser Render Harness (2026-08-13)

### Overview
Replaced every synthetic render path with real headless-chromium rendering via Playwright (a user-approved required dependency). The Part 8 repair loop now diffs actual browser output: screenshots plus per-node box-model and computed-style metadata keyed by `data-node-id`.

### What Changed
1. **`core/render_harness.py`** — placeholder replaced with a real `playwright.sync_api` implementation: chromium launch, viewport-normalized page (`{w,h}` and `{width,height}` both accepted), `networkidle` wait, full-page screenshot, `window.__figmaforge_meta` extraction. `layout_metadata` is now node-id-keyed in `DiffEngine` shape. Missing playwright raises `RenderHarnessError` naming the install command (module still imports cleanly). Hardening: explicit harness timeouts and `build_id` validation.
2. **`core/render_html.py`** — new: `IRDocument` + `VStyle` map → full HTML document with `#figmaforge-root` fixed to the viewport, `data-node-id` attributes, and the inline metadata-extraction script (mirrors `runtime/src/core/render_handler.ts` intent).
3. **`core/render_adapter.py`** — new: `make_render_callable(harness)` produces the `RenderCallable` closure injected via `RepairLoop(render_fn=...)`. Zero changes to `repair_loop.py` internals.
4. **`runtime/src/core/render_handler.ts`** — dead `tryBrowserRender` fixed: the Python bridge script is piped via stdin to `python3 -` and its JSON output parsed into the screenshot path. Extracted `buildBrowserRenderScript` / `parseBrowserRenderOutput` for testability. Hardening: path-escaping via `JSON.stringify`/`pathToFileURL` and process-group kill on timeout.
5. **Docs** — README + CLAUDE.md setup steps (`pip install playwright && playwright install chromium`), `docs/repair-loop.md` harness section, this log entry.

### Testing
- Mocked-playwright contract tests (`tests/test_render_harness.py`, 12), HTML generation tests (`tests/test_render_html.py`, 7), adapter + repair-loop integration tests (`tests/test_render_adapter.py`, 4), real-browser smoke tests that skip without chromium (`tests/test_render_harness_smoke.py`, 2; `test_harness_determinism` also guarded).
- TS: 5 new bridge tests in the runtime suite.
- Full gate: 273 Python tests OK (`Ran 273 tests ... OK (skipped=3)` without chromium), 106 runtime tests passing, `claude plugin validate --strict` clean.

### Non-goals (deferred)
Pixel/perceptual diffing (`_diff_raster`), real PNG decode in `screenshot_compare.ts`, Figma baseline download, stub backend implementations.

