# FigmaForge — Agent Trajectories

This document captures representative trajectories for the coding agents used to build FigmaForge. Each trajectory shows the agent's instructions, tools used, decisions made, and outcomes.

---

## Trajectory 1: Initial Architecture Design

### Agent Instructions
"Design a technology-agnostic, adaptive, full-lifecycle engineering platform that converts Figma designs into production code. The platform must detect stack-specific signals and route to appropriate capabilities without requiring per-repo authoring."

### Tools Used
- **Brainstorming** (Superpowers): Explored 5 architectural approaches before selecting the plugin-based design
- **Planning** (Superpowers): Created 10-phase lifecycle model with evidence-driven transitions
- **Code Search**: Analyzed existing Figma-to-code tools for patterns to adopt/avoid

### Key Decisions
1. **Plugin architecture over standalone app** — enables integration with existing coding tools without lock-in
2. **Framework-neutral core pipeline** — IR → Layout → Resolution must never generate framework-specific code
3. **100-role catalog** — covers all 10 engineering domains with deterministic routing
4. **Evidence-driven transitions** — lifecycle state machine requires verifiable artifacts at each gate

### Feedback That Shaped Next Steps
- Initial design had 12 phases — reduced to 10 after realizing phases 7-8 (verify/release) could be merged
- First draft allowed framework-specific concepts in core — audit found 6 leakage points, all fixed before proceeding

### Outcome
- Plugin skeleton created with 6 skills, 3 agents, 3 hooks
- 100-role catalog across 10 domains
- Detection and routing engine implemented and tested

---

## Trajectory 2: Design IR Implementation

### Agent Instructions
"Build a normalized intermediate representation for Figma designs that sits between ingestion and code generation. Must be framework-neutral, typed, and support JSON round-trip identity."

### Tools Used
- **Context7**: Verified Figma API documentation for node types and properties
- **TDD** (Superpowers): Wrote IR schema tests first, then implemented types
- **Code Review** (Superpowers): Reviewed 15-area vocabulary for completeness

### Key Decisions
1. **15-area typed vocabulary** — covers documents, pages, frames, text, components, auto-layout, positioning, dimensions, spacing, style, typography, tokens, assets, responsive, annotations
2. **JSON round-trip identity** — `from_dict(x.to_dict()).to_dict() == x.to_dict()` exactly, locked by identity tests
3. **Preserve node IDs and source paths** — enables traceability from generated code back to Figma
4. **Report unsupported properties** — `unsupported_properties()` surfaces what the IR doesn't cover

### Feedback That Shaped Next Steps
- First IR design omitted `annotations` area — added after discovering Figma's annotation system
- Initially planned to embed CSS properties in IR — moved to layout engine to maintain neutrality
- Round-trip test caught phantom `component` keys on empty IRComponent values

### Outcome
- `ir_types.py` (784 lines) — full typed vocabulary
- `ir_builder.py` (598 lines) — pure normalization
- `ir_validator.py` — stdlib JSON-Schema validation
- 11 round-trip identity tests

---

## Trajectory 3: Backend Adapter Architecture

### Agent Instructions
"Decouple the core pipeline from code generation. Create a backend adapter architecture where each target framework has its own adapter, but the core remains completely framework-neutral."

### Tools Used
- **Code Review** (Superpowers): Identified 6 framework leakage points in core modules
- **Brainstorming** (Superpowers): Designed the Feature vocabulary with 40+ canonical constants
- **TDD** (Superpowers): Wrote honesty audit before implementing backends

### Key Decisions
1. **BackendAdapter ABC** — abstract methods: name, display_name, capabilities, generate()
2. **Feature vocabulary** — 40+ canonical constants (FLEX, GRID, SHADOWS, etc.) that are framework-neutral
3. **FidelityLoss contract** — backends MUST emit one per unsupported feature, never silently approximate
4. **Honesty audit** — renders ONE canonical fixture through every backend, fails when any declared-supported feature has no signal in the output

### Feedback That Shaped Next Steps
- First backend design allowed `generate()` to return None for unsupported features — changed to require FidelityLoss
- Initial Feature vocabulary had 20 constants — expanded to 40+ after audit revealed gaps
- Honesty audit immediately caught 4 silent drops (flutter FONT_FAMILY, FILL_SIZE, swiftui JUSTIFY, react_tailwind IMAGE_ASSETS)

### Outcome
- `backends/protocol.py` (432 lines) — BackendAdapter ABC, Feature, FidelityLoss
- `backends/registry.py` (160 lines) — auto-discovery and management
- 6 real backends (HTML/CSS, React+Tailwind, Vue, Svelte, SwiftUI, Flutter)
- Repo-wide honesty audit with 66 backend tests

---

## Trajectory 4: Auto-Repair Loop

### Agent Instructions
"Implement an automatic visual repair loop that iterates between rendering, diffing, classifying mismatches, planning patches, and executing repairs until visual similarity reaches the configured threshold."

### Tools Used
- **TDD** (Superpowers): Wrote repair classifier tests first, then implemented 9 categories
- **Systematic Debugging** (Superpowers): Traced pixel diff false positives to antialiasing noise
- **Code Review** (Superpowers): Reviewed patch ordering strategy

### Key Decisions
1. **9 repair categories** — geometry, spacing, typography, color, token, asset, responsive, missing_element, extra_element
2. **Strategy-ordered patching** — missing/extra elements first, then parent geometry, shared tokens, typography, assets, color
3. **Full rollback support** — every mutation recorded as MutationRecord with old/new values
4. **SSIM gating** — perceptual comparison filters antialiasing noise before repair

### Feedback That Shaped Next Steps
- First repair loop tried to fix everything at once — strategy ordering improved convergence
- Initial pixel diff triggered false repairs on antialiasing — SSIM gate solved this
- Rollback was added after a patch caused regression in a different area

### Outcome
- `repair_classifier.py` (425 lines) — 9 categories with source attribution
- `patch_planner.py` (461 lines) — strategy-ordered with shared-token grouping
- `patch_executor.py` (444 lines) — source-level patches with rollback
- `repair_loop.py` (423 lines) — 6 stopping conditions
- 30 repair-loop tests including end-to-end with intentional defects

---

## Trajectory 5: Bundler Harness for Real-Toolchain Rendering

### Agent Instructions
"React+Tailwind, Vue, and Svelte output can't be rendered as standalone HTML. Create a deterministic bundler harness that builds, serves, and screenshots the real output."

### Tools Used
- **TDD** (Superpowers): Wrote bundler harness tests with real Vite builds
- **Systematic Debugging** (Superpowers): Traced asset path issues through Vite's build pipeline
- **Code Review** (Superpowers): Reviewed pinned dependency strategy

### Key Decisions
1. **Deterministic Vite scaffold** — exact pinned deps, multi-page build, asset copy + url() rewrite
2. **Ephemeral-port serve** — no fixed ports, no conflicts
3. **Real build errors** — build failure → explicit error with vite stderr, never a fake screenshot
4. **Self-contained fallbacks** — every referenced component gets a local definition

### Feedback That Shaped Next Steps
- First harness used latest Vite — pinned to specific version for reproducibility
- Asset paths broke after Vite's base URL rewrite — added explicit url() rewrite step
- Component references caused build errors — added self-contained fallback definitions

### Outcome
- `bundler_harness.py` — deterministic Vite scaffold for react/vue/svelte
- React 0.9987 / Vue 1.0000 / Svelte 1.0000 SSIM-clean on checked-in fixture
- `--no-bundle` escape for honest degradation

---

## Trajectory 6: Full Pipeline Integration

### Agent Instructions
"Wire all 10 stages into the TypeScript runtime: ingest → normalize → resolve → layout → assets → generate → render → compare → repair → verify. Each stage must produce a JSON artifact consumed by the next."

### Tools Used
- **TDD** (Superpowers): Wrote stage handler tests before implementing
- **Systematic Debugging** (Superpowers): Traced ENOENT errors to pluginDir resolution
- **Code Review** (Superpowers): Reviewed shared state threading between stages

### Key Decisions
1. **Single-JSON-line contract** — every Python CLI subcommand prints one deterministic JSON line
2. **Staged generate** — `--ir/--layout/[--resolution]` consumes front-half artifacts directly (byte-identical to `--file` recompute)
3. **Assets before generate** — PIPELINE_STAGES reordered so asset manifest is a generate input
4. **Honest degradation** — no-score/render-degraded/reference-baseline contracts never fabricate work

### Feedback That Shaped Next Steps
- First integration had stages running in wrong order — assets must run before generate
- PluginDir default was wrong when running from runtime/ directory — fixed to use project root
- Repair loop was regenerating wrong backend — threads the run's backend + resolution + assets

### Outcome
- 10-stage pipeline: ingest → normalize → resolve → layout → assets → generate → render → compare → repair → verify
- `figmaforge run --target=react+tailwind` produces Score: 1, Verification: PASSED
- 672 Python tests + 148 TS tests passing

---

## Trajectory 7: Image-to-IR — Any Screenshot → Production Code

### Agent Instructions
"Extend FigmaForge to accept any image (screenshot, mockup, wireframe) as input, not just Figma JSON. Use a vision model to extract structured layout and produce the same IRDocument that feeds the existing pipeline."

### Tools Used
- **Brainstorming** (Superpowers): Designed the hybrid architecture where Figma JSON and any image converge on the same IR
- **TDD** (Superpowers): Wrote 27 tests for image_analyzer before implementing
- **Code Review** (Superpowers): Reviewed the prompt engineering for the vision model
- **Context7**: Verified Anthropic and OpenAI vision API contracts

### Key Decisions
1. **Same IRDocument output** — image_analyzer produces the same IRDocument type as ir_builder.py, so the entire downstream pipeline (layout → code → compare → repair) works unchanged
2. **Vision model as a protocol** — `VisionModel` protocol allows swapping providers without code changes
3. **Confidence scores** — each extracted element carries a confidence score for downstream filtering
4. **Honest normalization skip** — when image input produces IR directly, the normalize/audit stages are skipped (not forced to pass)

### Feedback That Shaped Next Steps
- First prompt asked for CSS output — changed to structured JSON matching the IR vocabulary
- Initial design tried to use OCR for text extraction — vision models handle this better
- Circular reference protection was added after discovering potential infinite loops in element hierarchies

### Outcome
- `image_analyzer.py` (450 lines) — vision model integration + IR construction
- `pipeline.py image_ingest` — CLI subcommand
- TypeScript runtime: `--image` flag, `invokeImageIngest()`, stage handler routing
- 27 new tests: helpers, mock vision model, edge cases
- 699 Python tests + 148 TS tests all passing

---

## Agent Usage Summary

| Phase | Agent Capabilities Used | Key Outcome |
|-------|------------------------|-------------|
| Architecture | Brainstorming, Planning | 10-phase lifecycle, 100-role catalog |
| IR Design | Context7, TDD, Code Review | 15-area typed vocabulary, round-trip identity |
| Backends | Code Review, Brainstorming, TDD | 6 backends, honesty audit, Feature vocabulary |
| Repair Loop | TDD, Systematic Debugging, Code Review | 9 categories, strategy ordering, rollback |
| Bundler | TDD, Systematic Debugging, Code Review | Real Vite builds, ephemeral ports |
| Integration | TDD, Systematic Debugging, Code Review | 10-stage pipeline, honest degradation |
| Image-to-IR | Brainstorming, TDD, Code Review, Context7 | Universal input (any image → code) |

**Total coding agent sessions:** ~47 (across 23 parts)
**Key agent contributions:** Architecture design, TDD implementation, systematic debugging of integration issues, code review for leakage points, vision model prompt engineering
