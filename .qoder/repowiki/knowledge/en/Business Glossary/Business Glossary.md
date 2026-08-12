---
kind: business_term
name: Business Glossary
category: business_term
scope:
    - '**'
---

### Design IR
- Definition：Framework-neutral intermediate representation of a Figma file produced by the ingestion layer (Parts 3–5). It normalizes raw Figma nodes into typed `IRDocument`/`IRNode` structures covering frames, text, components, auto-layout, positioning, dimensions, spacing, style, typography, tokens, assets, responsive rules, prototype links, and annotations. Used as the single source of truth consumed by the component/token resolver, layout engine, and code generators; never framework-specific (no React/CSS output).
- Aliases：IR、design IR、Figma IR

### LayoutPlan
- Definition：Framework-neutral layout plan produced by the responsive layout engine (Part 5) from the Design IR plus resolved library. Encodes flex/grid/absolute inference, per-axis sizing (fixed/fill/hug/percent), min/max, spacing, alignment, anchoring, text wrapping, overflow/clip, nested propagation, breakpoints, and diagnostics. Consumed by code generators (Part 6) and the visual repair loop; never emits framework code directly.
- Aliases：layout plan、plan

### ResolutionReport
- Definition：Schema-validated JSON report produced by the component/token resolver (Part 4) that maps Figma components, variants, and design tokens onto the repository's existing library under `library/`. Outcomes are deterministic: resolved, ambiguous, or missing — never guessed. Consumed by the layout engine and code generators.
- Aliases：resolution report、resolver report

### VNode / VStyle
- Definition：Abstract virtual DOM protocol emitted by the code generator (Part 6). `VNode` is a framework-neutral tree of semantic tags with `data-figma-id` debug IDs; `VStyle` is a map of layout/style properties (display, sizing, padding/gap, flex/grid direction, alignment, absolute positioning). Style adapters (CSS Modules, Tailwind, SCSS) render these maps to real strings without re-running analysis.
- Aliases：VNode、VStyle、virtual node、virtual style

### Repair Loop
- Definition：Automatic iterative system (Part 8) that detects visual differences between the Figma design and rendered output, then repairs them by modifying source code and design tokens — never screenshots or reference images. Cycle: Render → Diff → Classify → Plan → Execute → Re-render, stopping on threshold satisfaction, no safe repair, insufficient progress, max iterations (default 10), approval denied, or regression detected. Every mutation recorded with rollback support.
- Aliases：visual repair loop、repair pipeline、repair cycle

### RepairCandidate
- Definition：A classified mismatch from the diff engine, mapped back to source artifacts (Figma node id, component name, source file, CSS selector, bound token key/property). Exactly one of nine categories: geometry, spacing, typography, color, token, asset, responsive, missing_element, extra_element. Each carries a deterministic confidence score (0.0–1.0) based on base confidence, value presence, mismatch type definition, and whether a bound design token is involved.
- Aliases：repair candidate、candidate

### PatchPlan
- Definition：Ordered set of patches produced by the patch planner, prioritized to maximize impact and minimize risk: global environment mismatches first, then missing/extra elements, parent-before-child geometry, shared tokens before local styles, layout constraints before absolute coordinates, typography before fine pixel offsets, assets before color tuning. Shared tokens are grouped so one change fixes multiple nodes.
- Aliases：patch plan、plan

### LifecycleState
- Definition：Atomic current state of a 10-phase lifecycle run (intake → discover → define → design → plan → implement → verify → release → operate → learn), persisted to `.figmaforge/runs/<run-id>/state.json` with append-only events in `events.jsonl`. Transitions are evidence-driven (not prose claims) and require specific preconditions (e.g., requirements exist to move define→design; changed artifact inventory to move implement→verify).
- Aliases：state、run state、lifecycle state

### Detector
- Definition：Evidence-based repository stack detection module that inspects manifests, lockfiles, source extensions, test/build commands, CI providers, container/IaC configs, database migrations, and existing Claude/MCP/LSP configuration to produce a structured assessment. A language module becomes eligible only when there is a manifest OR sufficiently strong source evidence — binary presence alone is not enough.
- Aliases：stack detector、detector module

### Router
- Definition：Deterministic role selection engine that scores candidate roles from the 100-role catalog (10 domains × 10 roles) based on explicit trigger match (+4), lifecycle-phase match (+3), repository signal match (+3), requested deliverable match (+2), mapped external capability installed (+1), and stack conflicts (-5). Outputs at most 3 roles, 2 skills, 1 execution mode, 1 approval gate. Never installs plugins, connects MCP, chooses a stack, or executes specialist skills itself.
- Aliases：role router、router module

### PinchTab
- Definition：An MCP (Model Context Protocol) server configured as a stdio transport in the root `.mcp.json` under the name `pinchtab`. It is the only active MCP server in this project's configuration; the plugin does not approve or connect MCP servers automatically.
- Aliases：pinchtab、MCP server pinchtab

### Golden Fixtures
- Definition：Deterministic baseline inputs used for snapshot testing across the pipeline: Figma API response fixtures paired with expected outputs (IR, layout, code) located under `runtime/evaluation/fixtures/golden/` (simple-button, login-screen, card-layout) and `plugin/figmaforge/tests/snapshots/`. Regenerated via `REWRITE_SNAPSHOTS=1` after intentional output changes.
- Aliases：golden、golden fixture、snapshot fixture
