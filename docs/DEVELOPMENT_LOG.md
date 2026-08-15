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

#### 3. Screenshot Comparison (scaffold)
- **`screenshot_compare.ts`** (231 lines): `ScreenshotComparator` class with:
  - SHA-256 content hashing for fast identical-image detection
  - A buffer-size heuristic standing in for real comparison (superseded by the
    real pixel diff in Part 12 — this Part 10 version did NOT decode pixels)
  - `compare()`: Comparison returning similarity score, diff pixel count, dimensions, hashes
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
- Mocked-playwright contract tests (`tests/test_render_harness.py`, 14), HTML generation tests (`tests/test_render_html.py`, 10), adapter + repair-loop integration tests (`tests/test_render_adapter.py`, 6), real-browser smoke tests that skip without chromium (`tests/test_render_harness_smoke.py`, 2; `test_harness_determinism` also guarded).
- TS: 9 new bridge tests in the runtime suite.
- Full gate: 273 Python tests OK (`Ran 273 tests ... OK (skipped=3)` without chromium), 109 runtime tests passing, `claude plugin validate --strict` clean.

### Non-goals (deferred)
Pixel/perceptual diffing (`_diff_raster`), real PNG decode in `screenshot_compare.ts`, Figma baseline download, stub backend implementations.

---

## Part 12 — Pixel Diffing + Figma Baseline Download

Real pixel-level comparison: a stdlib-only PNG codec feeds a per-pixel diff with region
detection and node attribution; Figma baseline PNGs download into the content-addressed
asset store; the repair loop scores renders with a capped pixel weight. The IR remains the
immutable source of truth — the baseline is a supplementary signal.

### What Changed
1. **`core/png_codec.py`** — new: stdlib `zlib`+`struct` PNG decode (8-bit RGB/RGBA, color
   types 2/6, non-interlaced, filters 0–4 incl. Paeth) + minimal filter-0 `encode_png`;
   typed `PngError` for everything unsupported.
2. **`core/pixel_diff.py`** — new: per-pixel comparison (`color_threshold`, diffRatio, MAE),
   contiguous-region detection (`min_region_area`), bbox-intersection node attribution, and
   the `python3 -m core.pixel_diff` CLI (one JSON line; clean error sentinel).
3. **`core/figma_assets.py`** — new: `download_baselines()` over `FigmaClient.get_images`
   with injectable transport, bounded retry, expiry detection, content-addressed dedup,
   optional `AssetHandler.mark_downloaded`; typed `FigmaAssetError` hierarchy.
4. **`core/diff_engine.py`** — real `_diff_raster`; `diff()` gains optional
   `render_screenshot`/`baseline_png`/`raster_options` (fully backward compatible);
   `DiffReport.raster_stats`; overall score composes
   `(1 − pixel_weight)·structural + pixel_weight·pixels` only when a raster diff ran.
5. **`core/repair_loop.py`** — `RepairConfig` knobs (`baseline_png`, `color_threshold=16`,
   `noise_floor=0.01`, `min_region_area=8`, `pixel_weight=0.15`); both diff call sites pass
   the screenshot the loop already receives; zero control-flow changes.
6. **Deterministic capture** — `RenderHarness.render(..., full_page=True)` optional param;
   `device_scale_factor=1`; `document.fonts.ready` wait; animations/transitions killed in
   `render_html.py`; the repair adapter passes `full_page=False`.
7. **`core/repair_classifier.py`** — `pixel_mismatch` registered → `color` category.
8. **`runtime/src/core/screenshot_compare.ts`** — real shell-out to `core.pixel_diff`
   (hash fast-path kept, `ScreenshotComparison` interface preserved, clean typed failure on
   garbage/missing python); `cmdCompare` accepts `--baseline`.
9. **Docs** — `docs/repair-loop.md` pixel-diff section; Part 10 overclaim corrected; this
   entry.

### Testing
- Python: 56 new tests (png codec 17, pixel diff 15, figma assets 7, diff engine 8,
  repair-loop raster 3, deterministic capture 4 — chromium-gated tests RUN and pass —
  classifier 2). Full gate: **370** Python tests OK, zero
  skips.
- TS: comparator suite replaced with real-PNG shell-out tests (11 tests, was 7). Full gate:
  **113** runtime tests passing.
- `claude plugin validate --strict` clean.

### Non-goals (deferred)
SSIM/perceptual metrics, image resampling, diff heatmap output, baseline auto-refresh,
native TS pixel diffing, grayscale/palette/16-bit PNG support.

## Part 13 — Perceptual Diffing (SSIM) + Baseline Auto-Refresh

Raw pixel-count diffing (Part 12) cannot tell "antialiasing/font noise" from "real
localized change". Part 13 adds a perceptual verdict via pure-stdlib SSIM — computed per
diff-region so a small localized change is never masked by a high global mean — and lets
the loop safely adopt byte-different-but-clean renders as the new baseline. The IR remains
the immutable source of truth; the baseline remains a supplementary signal.

### What Changed
1. **`core/ssim.py`** — new: stdlib-only windowed SSIM (luminance plane, 2×2 downsample to
a 256px cost bound, integral-image sums, pinned C1/C2 constants). `ssim()` for whole
images; `ssim_region()` for bboxes (O(bbox) cost). Identical inputs → exactly 1.0;
`ValueError` on size mismatch or sub-window images (callers treat unmeasurable regions as
real, never clean).
2. **`core/diff_engine.py`** — `_diff_raster` returns an explicit `(mismatches, stats,
diff_ratio, clean)` verdict; regional SSIM gating (bbox grown to `window×window`,
clamped; unmeasurable → conservative real; zero regions → global fallback); SSIM is
**always** computed and recorded when enabled (drift diagnostic) but gates only above
`noise_floor`; a clean verdict suppresses `pixel_mismatch` emission. `RasterOptions`/
`RepairConfig` gain `ssim_enabled=True`, `ssim_threshold=0.95`.
3. **`core/pixel_diff.py`** — `regional_verdict()` moved here as the ONE shared gating rule
(both the engine and the CLI use it — no drift); the CLI/`compare_png_files` output now
carries `ssim`, `min_region_ssim`, `ssim_clean` (null when unmeasurable) and a
`--ssim-threshold` flag; one-JSON-line contract and hash fast-path untouched.
4. **`core/repair_loop.py`** — opt-in baseline auto-refresh (`refresh_baseline=False`
default, `max_baseline_refreshes_per_run=3`): a clean render is adopted as a **versioned
sibling file** `<stem>.refreshed.<n>.png` (the original Figma baseline is NEVER
modified — provenance), `baseline_png` is repointed, adoption is bounded and
self-stabilizing (deterministic capture + hash fast-path → no churn). Guards: no refresh
on regressions, size mismatches, or byte-identical renders; `refresh_baseline=True`
requires `ssim_enabled=True` (config-time error). The event is recorded per iteration
(`baseline_refreshed`, `baseline_new_path`).
5. **`runtime/src/core/screenshot_compare.ts`** + `cmdCompare` — `ScreenshotComparison`
gains optional `ssim`/`minRegionSsim`/`ssimClean` (missing keys → null, old output still
parses); the hash fast-path reports the clean verdict; `cmdCompare` prints the perceptual
verdict ("Perceptually identical: N diff pixels within visual noise (SSIM …)" or
"Perceptual change: SSIM …, min-region SSIM …").
6. **Docs** — this entry; `docs/repair-loop.md` SSIM + auto-refresh sections; README/
CLAUDE.md updated through Part 13.

### Testing
- Python: 25 new tests (ssim 9, diff-engine SSIM gating 10, repair-loop refresh 6). Full
gate: **395** tests OK, zero skips (33 test files).
- TS: 4 new tests (localized-verdict comparator, Part 13 parse, two `cmdCompare` CLI
integration tests). Full gate: **117** runtime tests passing.
- `claude plugin validate --strict` clean.

### Non-goals (deferred)
Diff heatmap image output, native TS pixel diffing, image resampling beyond the fixed SSIM
downsample, grayscale/palette/16-bit PNG support, scheduled/cron baseline refresh
(auto-refresh is loop-scoped), stub backend implementations.

## Part 14 — Backend Implementations (react+tailwind, vue, svelte, swiftui, flutter)

The five stub backend adapters from Part 10 are now real `LayoutPlan` → target-code
lowerings. One shared web style-mapping implementation serves every web target; the two
native targets are self-contained. Every feature the common IR surface cannot express is
an explicit `FidelityLoss` with a named fallback or an inline `fidelity:` marker — never
silent — and a repo-wide honesty audit now locks that contract.

### What Changed
1. **`backends/web_common.py`** — new: the shared web machinery, extracted verbatim from
   `html_css` (`VStyle`/`VNode`, `CssStyleGenerator`, `VNodeBuilder`, `semantic_tag`,
   escaping helpers) plus `ScopedCssGenerator` (scoped CSS collector), `extend_ir_style`
   (IR fills/radius/borders/opacity/shadows/blur/typography/overflow/breakpoints),
   and `bp_to_css_prop` (shared breakpoint semantics, px units for numeric length props).
   ONE style-mapping implementation; html_css, vue, and svelte cannot drift apart.
2. **`backends/react_tailwind/`** — real TSX generator: arbitrary-value Tailwind classes
   for exactness (`bg-[#3366cc]`, `max-[768px]:flex-row`), IR-sourced style/typography,
   real token extraction into `tailwind.config.figmaforge.js`, deterministic.
3. **`backends/vue/`** — real `.vue` SFC: `<template>` scoped `n-{id}` classes,
   `<script setup>`, `<style scoped>` from the shared CSS rules, `@media` breakpoints.
4. **`backends/svelte/`** — real `.svelte` component: `<script lang="ts">` props,
   scoped class markup, shared scoped CSS.
5. **`backends/swiftui/`** — real `.swift` view: `VStack`/`HStack` + spacing/alignment,
   `.frame/.padding/.background/.cornerRadius/.opacity/.font/.shadow/...` modifier
   chains, real `LinearGradient` and `.position()`.
6. **`backends/flutter/`** — real `.dart` widget tree: `Row`/`Column` with
   `mainAxisAlignment`/`crossAxisAlignment` + `SizedBox` gap separators,
   `Container`+`BoxDecoration`, `EdgeInsets`, `Text`+`TextStyle` (incl. `fontFamily`),
   real `LinearGradient`, `Stack`+`Positioned`, `Opacity`, `Clip.hardEdge`.
7. **Fidelity honesty audits** — three commits closing the capability-vs-output gap:
   (a) the five new backends emit every declared-supported feature (gradients, shadows,
   blur, decoration/case, overflow, `.custom` fonts, letter-spacing, clip) and move
   unimplementable features to partial with the base-set subtraction that keeps
   `supports()` honest; (b) the html_css reference backend was emitting only layout CSS
   despite declaring the full style surface — it now lowers the shared IR style
   extension, fixes the invalid `display: absolute` output, and emits the image-fill
   fallback + inline marker; (c) **`tests/test_backend_honesty_audit.py`** — a repo-wide
   audit renders ONE canonical rich fixture through every backend and fails when any
   declared-supported feature has no signal in the output (coverage guard + mutation
   proofs). It immediately caught and fixed: flutter `FONT_FAMILY` (now emitted) and
   `FILL_SIZE`/`HUG_SIZE`/`PERCENT_SIZE` (moved to partial — lowered as computed box
   size, not `Expanded`/`IntrinsicWidth`/`FractionallySizedBox`), swiftui `JUSTIFY`
   (no main-axis justification in SwiftUI stacks — partial), react_tailwind
   `IMAGE_ASSETS` shadowing (supported now subtracts the partial set), and unitless
   breakpoint length values in `bp_to_css_prop`.
8. **Docs** — this entry; README/CLAUDE.md/architecture.md updated through Part 14.

### Testing
- Python: 66 new tests across the six backends + audit (Task 1: 2, Task 2: 11, Task 3: 10,
  Task 4: 10, Task 5: 10, Task 6: 10, audits: 13). Full gate: **461** tests OK, zero skips
  (39 test files), including 5 committed backend golden snapshots.
- TS: unchanged — **117** runtime tests passing, `npx tsc` clean.
- `claude plugin validate --strict` clean.

### Non-goals (deferred)
Real-Figma end-to-end demo, real-repository testing, rollback procedure docs, diff heatmap
output + extended PNG formats.

### Follow-up — real SwiftUI main-axis justification (Spacer)
swiftui's `JUSTIFY` now lowers to real `Spacer()` lines inside VStack/HStack: CENTER
becomes leading + trailing spacers, MAX a leading spacer, SPACE_BETWEEN a spacer between
every pair of children, and MIN/None the default packing. `JUSTIFY` is lifted from partial
back to supported; the repo-wide honesty audit gained the `Spacer()` signal. Full gate:
**471** tests OK (was 466), zero skips.

### Follow-up — real flutter sizing idioms (Expanded / IntrinsicWidth+Height / FractionallySizedBox)
flutter's fill/hug/percent sizing now lower to the real Flutter idioms instead of computed box
sizes: main-axis fill becomes `Expanded` (parent-level, flex only), cross-axis fill becomes
`SizedBox(width/height: double.infinity)`, hug becomes `IntrinsicWidth`/`IntrinsicHeight`, and
percent becomes `FractionallySizedBox(widthFactor/heightFactor:)` — with the computed box size
suppressed on those axes. `FILL_SIZE`/`HUG_SIZE`/`PERCENT_SIZE` are lifted back from partial to
supported; the repo-wide honesty audit gained the corresponding signals. Full gate: **466** tests
OK (was 461), zero skips.

## Part 15 — Real-Figma End-to-End Demo (six backends through the TS runtime)

The TypeScript runtime's 10-stage pipeline previously had **no stage handlers**
(`run` completed with empty results; `generateSimpleHtml` was only a render
fallback). Part 15 gives the pipeline its first real stages — `ingest` and
`generate` — by shelling out to a new Python CLI, so `figmaforge run` and the new
`figmaforge demo` command produce real backend code for the first time.

### What Changed

1. **`scripts/pipeline.py`** — stdlib-only bridge CLI with `ingest` and `generate`
   subcommands. `ingest` fetches a live file (`--file-key`, requires `FIGMA_TOKEN`)
   or reads a local JSON (`--file`) and prints one deterministic JSON line (raw
   payload + injected `file_key`/`pages`). `generate` runs the fixture pipeline
   (`FigmaFile.from_dict → IRBuilder → LayoutAnalyzer → backend.generate`), writes
   files under `<out-dir>/<backend>/`, and prints a deterministic manifest
   (`backend`, files sorted by path, `fidelity_losses`, `metadata`). Exit codes:
   2 unknown backend/invocation, 3 missing token, 4 unreadable/invalid file,
   1 unexpected. No tracebacks.
2. **Registry discovery fix** — `discover_builtins()` imported
   `figmaforge.backends.*`, which cannot resolve in this repo's layout, so
   `get_registry().names()` returned `[]` and the CLI could not validate backends.
   It now imports `backends.*` (with an installed-package fallback).
3. **`runtime/src/core/backend_codegen.ts`** — `TARGET_BACKENDS` maps the six
   backend-bearing presets to Python backend names; `backendForTarget` rejects
   backend-less targets (react+css, react+styled_components) with a typed
   `UnsupportedTargetError`; `invokeBackendGenerator` spawns
   `scripts/pipeline.py generate` (mirroring `createPythonTool`) and returns
   `{ manifest, filesDir }`; `invokeIngest` shares the ingest spawn between the
   stage handler and the demo.
4. **Stage wiring** — `PipelineCoordinator.setShared` seeds shared data (e.g. a
   local file path); `cmdRun` now accepts `--file=<path>` (offline) alongside
   `--file-key` (live) and registers real `ingest` + `generate` handlers, so
   `figmaforge run --file=<fixture> --target=flutter+flutter_widgets` completes
   with a `generated_code` artifact.
5. **`figmaforge demo` command** — ingests once (local file, live key, or the
   checked-in offline fixture with an explicit message) and generates all six
   backends into `--out/<backend>/`, printing a deterministic per-backend table
   (files / fidelity losses / node coverage). `--render` best-effort renders the
   html_css reference output via the Part-11 Playwright harness; failures degrade
   to a note, never a hard error.
6. **`docs/real-figma-demo.md`** — walkthrough: token setup, live + offline paths,
   expected table + per-backend outputs, single-backend `run`, troubleshooting,
   exit codes.

### Testing

- Python: **481** tests OK, zero skips (40 test files) — +10 pipeline CLI tests
  (ingest determinism, missing token, all six backends, manifest determinism,
  unknown backend, missing/invalid file, node coverage, `--out`).
- TS: **124** runtime tests passing, `npx tsc` clean — +7 backend-codegen tests
  (target map coverage, typed rejection, real generate-stage artifacts from the
  fixture, demo offline-fixture + default paths, no-backend stage failure).
- Demo smoke (offline): all six backends generate, zero placeholders, loss counts
  match declarations (html_css 0, web trio 3, swiftui 0, flutter 8); `--render`
  produced a real screenshot via Playwright.
- `claude plugin validate --strict` clean (verified at the Part 15 final gate).

### Non-goals (deferred)

Wiring the remaining pipeline stages (normalize/resolve/layout/assets/render/
compare/repair/verify) into the TS runtime; compiling or executing generated
SwiftUI/Flutter/TSX code; Figma OAuth (token-only); CI for the demo.

## Part 16 — Front-Half Stage Wiring (normalize / resolve / layout through the TS runtime)

Part 15 wired ingest + generate; the other stages were still skipped. Part 16
makes the **full front half** real: normalize/resolve/layout are composable TS
stages whose JSON artifacts are consumed losslessly by the next stage, and
`figmaforge run` exercises the whole front of the pipeline for the first time.

### What Changed

1. **JSON round-trip loaders** — `IRDocument.from_dict` (`core/ir_types.py`,
   ~30 loader helpers) and `LayoutPlan.from_dict` (`core/layout_types.py`, ~16
   helpers) rebuild the full object trees from their `to_dict` shapes. The
   contract is JSON identity — `from_dict(x.to_dict()).to_dict() ==
   x.to_dict()` exactly (floats are already rounded at serialization time) —
   locked by identity tests across every fixture + a programmatic rich IR + a
   single-node plan. One real drift bug was caught by the diff: empty
   `IRComponent()` values would have re-created phantom `component` keys on
   nodes that had none.
2. **Front-half subcommands** (`scripts/pipeline.py`) — `normalize` (build +
   `ir_validator` schema-check the design IR), `resolve` (Resolver → report),
   and `layout` (LayoutAnalyzer → plan), each printing one deterministic JSON
   line with optional `--out`. All fixtures verified to pass the IR schema
   before baking validation in.
3. **Staged generate** — `generate --ir <ir.json> --layout <layout.json>
   [--resolution <report.json>]` consumes the front-half artifacts directly
   (no recompute); `--file` recompute stays for compatibility. Both modes
   share `--viewport` and are proven **byte-identical** (manifests + file
   bytes, react_tailwind + flutter).
4. **TS stage handlers** (`backend_codegen.ts`) — `invokeNormalize`/
   `invokeResolve`/`invokeLayout` (shared temp-file staging + single-JSON-line
   parsing, extracted as `parseJsonLine`), `invokeBackendGeneratorFromStages`
   (staged spawn), and `createNormalizeStageHandler`/`createResolveStageHandler`/
   `createLayoutStageHandler` threading fileJson → irJson → resolutionJson /
   layoutJson through `ctx.shared`. `createGenerateStageHandler` prefers the
   staged path and falls back to legacy `--file` for ingest+generate-only
   callers. `cmdRun` registers all five handlers.
5. **Docs** — this entry; real-figma-demo.md (five-stage artifact layout);
   README/CLAUDE.md/architecture.md updated through Part 16.

### Testing

- Python: **499** tests OK, zero skips (42 test files) — +11 round-trip identity
  tests (IR: 4 fixtures + rich + empty; Layout: 3 fixtures + single-node +
  tree shape) and +7 front-half CLI tests (normalize determinism/validation,
  resolve/layout consume normalize output, invalid-IR exit 4, staged ≡ file
  byte-identity, bad arg combos).
- TS: **128** runtime tests passing, `npx tsc` clean — +4 backend-codegen tests
  (five-stage run → all five artifact kinds; five-handler manifest deep-equals
  file-mode; missing-IR stage error; ingest+generate legacy fallback).
- Smoke: `figmaforge run --file=<fixture> --target=flutter+flutter_widgets`
  produced 6 artifacts (ingest/normalize/resolve/layout/generate + event log)
  and the flutter file — full front half verified end-to-end.
- `claude plugin validate --strict` clean (verified at the Part 16 final gate).

### Non-goals (deferred)

Wiring assets/render/compare/repair/verify into the TS runtime; compiling or
executing generated SwiftUI/Flutter/TSX code; changing the `figmaforge demo`
backend-direct path; schema-version migration for old IR/layout artifacts.
