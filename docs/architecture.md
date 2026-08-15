# FigmaForge Universal Adaptive Platform — Architecture

## Overview

FigmaForge is a technology-agnostic, adaptive, full-lifecycle Claude Code engineering platform. It enables any software project type by detecting stack-specific signals and routing to the appropriate capabilities, without requiring per-repo authoring of agents, skills, or workflows.

**Status:** Planned but not implemented. All prerequisites are done (skill bundles, PinchTab, backdrops).

---

## Design Philosophy

- **No application stack selection:** The repository is NOT assumed to be React, Node, Python, etc. Detection is evidence-based, not guessing.
- **Minimal inversion:** Plugin is a Claude Code plugin — NOT a Claude API, Managed Agents, or Agent SDK application.
- **Deterministic routing:** Detection and routing logic is bounded and stateless before Claude interprets results.
- **Read-only discovery:** Early phases do not modify the repository.
- **Safety-first:** Mutations require explicit approval and verification gates.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         User Request (any natural language)              │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        SessionStart / Lifecycle Hooks                   │
│  ┌─────────────────────┐   ┌─────────────────────┐   ┌────────────────┐│
│  │ SessionDetector     │   │ PreToolUseGate      │   │ PostToolUse    ││
│  │ (runs detector)     │   │ (gates mutations)   │   │ Validator      ││
│  └──────────┬──────────┘   └──────────┬──────────┘   └────────────────┘│
└─────────────┼───────────────────────────┼───────────────────────────────┘
              │                           │
              ▼                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Router (deterministic)                          │
│  • Reads repo evidence (manifests, source, configs)                      │
│  • Scores candidate roles (phases, deliverables, existing capabilities)   │
│  • Returns at most: 3 roles, 2 skills, 1 execution mode, 1 approval      │
└─────────────┬───────────────────────────────┬────────────────────────────┘
              │                               │
              ▼                               ▼
┌─────────────────────┐               ┌─────────────────────┐
│  Phase Router       │               │  Role Catalog       │
│  (routes request)   │               │  (100 roles)        │
└──────────┬──────────┘               └─────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      Lifecycle State Machine (10 phases)                │
│  intake → discover → define → design → plan → implement                │
│  verify → release → operate → learn                                   │
│                                                                          │
│  • Atomic state writes to `.figmaforge/runs/<run-id>/state.json`         │
│  • Append-only events to `.figmaforge/runs/<run-id>/events.jsonl`        │
│  • Evidence-driven transitions (not prose claims)                       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Core Components

### 1. Detector (core/detector.py)

**Responsibility:** Inspect repository evidence and return a structured assessment.

**Input:** Repository root path.

**Output:**
```json
{
  "status": "unclassified | classified",
  "root": "/path/to/repo",
  "languages": ["python", "typescript"],
  "package_managers": ["pip", "pnpm"],
  "frameworks": ["fastapi", "react"],
  "test_commands": ["pytest", "test"],
  "build_commands": ["uvicorn", "vite build"],
  "lsp_candidates": ["pyright", "typescript-language-server"],
  "confidence": 0.75,
  "evidence": ["pyproject.toml exists", "package.json exists"],
  "warnings": []
}
```

**Detection Signals:**
- Language manifests: `package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `pom.xml`, etc.
- Lockfiles/package managers: `pnpm-lock.yaml`, `requirements.txt`, `Gemfile`, etc.
- Source extensions (configurable thresholds)
- Test framework config
- CI providers (GitHub Actions, GitLab CI, CircleCI, Buildkite)
- Container/IaC (Terraform, Pulumi, CloudFormation, Kubernetes)
- Database migration/config files
- Existing Claude/MCP/LSP configuration
- Monorepo/workspace manifests

**Rule:** A language module becomes eligible only when there is a manifest OR sufficiently strong source evidence. Binary presence alone is NOT enough.

### 2. Router (core/router.py)

**Responsibility:** Score and select roles based on the request and detected evidence.

**Scoring Rules:**
- +4: Explicit trigger match
- +3: Lifecycle-phase match
- +3: Repository signal match
- +2: Requested deliverable match
- +1: Mapped external capability installed
- -5: Stack-specific role conflicts with detected evidence
- -3: Role requires a stack but repo is unclassified

**Output:**
```json
{
  "phases": ["define", "design", "plan"],
  "roles": [
    {
      "id": "requirements-architect",
      "title": "Requirements Architect",
      "domain": "architecture",
      "score": 9,
      "reason": "Explicit trigger 'requirements' matches role; repository signals favor architecture domain"
    }
  ],
  "external_skills": ["engineering-skills:senior-security"],
  "execution_mode": "direct",
  "stack_status": "classified",
  "approval_gates": ["external_mutation"],
  "unloaded_modules": ["python", "go"]
}
```

**Constraint:** Router must NEVER install a plugin, connect MCP, choose a stack, or execute a specialist skill itself.

### 3. Lifecycle Model

**10 Phases:**
1. **intake** — Capture user request, initialize run state
2. **discover** — Gather evidence (detected, user-provided)
3. **define** — Define requirements and acceptance criteria
4. **design** — Design solution (roles, components, interfaces)
5. **plan** — Create phased implementation plan
6. **implement** — Execute implementation (gate-protected)
7. **verify** — Verify changes against acceptance criteria
8. **release** — Release changes (approval-gated)
9. **operate** — Operate and monitor (separate authorization)
10. **learn** — Capture learnings for future runs

**State Files:**
- `.figmaforge/runs/<run-id>/state.json` — Atomic current state
- `.figmaforge/runs/<run-id>/events.jsonl` — Append-only event log

**Evidence-Driven Transitions:**
- `define → design` requires requirements + acceptance criteria exist
- `implement → verify` requires changed artifact inventory
- `verify → release` requires checks passed OR failures explicitly accepted
- `release → operate` requires deployment authorization + operational checks defined
- External mutation requires explicit approval record

### 4. Catalog (roles.json)

**Composition:** 10 domains × 10 roles = 100 unique role entries.

**Domains:**
1. Discovery and strategy
2. Experience and design
3. Architecture
4. Application engineering
5. Data and AI
6. Quality and security
7. Delivery and operations
8. Governance and risk
9. Growth and business
10. Executive and domain

**Role Structure:**
```json
{
  "id": "product-manager",
  "title": "Product Manager",
  "domain": "discovery",
  "phases": ["intake", "define"],
  "triggers": ["requirements", "epic", "roadmap", "okr"],
  "deliverables": ["user stories", "epic backlog", "prioritized list"],
  "repository_signals": ["README.md contains 'product'", "docs/roadmap.md exists"],
  "risk": "medium",
  "capability_refs": ["product-skills:product-discovery"],
  "fallback_pack": "general-product-manager"
}
```

**Constraint:** Role catalog is NEVER injected into model context. Router reads it and emits only the selected role records.

---

### 5. Design Intermediate Representation (IR)

**Status:** Implemented (Part 3). Input is the Figma ingestion layer; no
code generation yet.

A framework-neutral, typed view of a Figma file that sits between ingestion and
any future renderer. See `docs/design-ir.md` for the full spec and a
raw-node→IR example.

- `core/ir_types.py` — typed IR models (`IRDocument`/`IRNode`) covering 15 areas
  (documents/pages, frames, text, components/instances, auto-layout,
  positioning, dimensions, spacing, style, typography, tokens, assets,
  responsive, prototype, annotations) with deterministic JSON serialization.
- `core/ir_builder.py` — pure normalization from `FigmaFile`/`Node`
  (`IRBuilder.build`); preserves node ids, source paths, `raw` payloads, and
  unmapped properties (`unsupported_properties()`).
- `core/ir_validator.py` + `schemas/design-ir.schema.json` — stdlib-only
  JSON-Schema validation of the serialized IR.
- `tests/test_ir.py` + `tests/test_ir_snapshot.py` — fixture and snapshot tests.

**Constraint:** The IR is framework-neutral and must never generate React/CSS
(or any framework) output. Code generation is a later phase.

### 6. Component & Token Resolution (Part 4)

**Status:** Implemented. Input is the Design IR + the project library; no code
generation yet. See `docs/resolution.md`.

Resolves Figma components, variants, and tokens onto the repository's existing
library (`library/`), producing a schema-validated JSON report
(`schemas/resolution-report.schema.json`):

- `core/component_index.py` + `core/variant_resolver.py` — component indexing,
  instance-to-component resolution, variant extraction.
- `core/matcher.py` — deterministic mapping (explicit `figma_keys` override,
  then normalized name/alias) with explicit **resolved / ambiguous / missing**
  outcomes; never guesses on multiple matches.
- `core/token_resolver.py` — semantic tokens (color, typography, spacing,
  radius, shadow, opacity, breakpoint) with token references instead of
  duplicated values; unsupported token types reported, not dropped.
- `core/resolver.py` — `Resolver.resolve()` → `ResolutionReport`.

**Constraint:** Matching is deterministic string/key logic; no model-based or
fuzzy matching, no agent frameworks.

### 7. Responsive Layout & Constraint Solver (Part 5)

**Status:** Implemented. Input is the Design IR + the resolved project library;
no code generation yet. See `docs/layout.md`.

Lays a Design IR out into a framework-neutral `LayoutPlan` (the seam a future
code generator consumes):

- `core/layout_engine.py` — flex/grid/absolute inference, per-axis sizing
  (fixed/fill/hug/percent), min/max, spacing, alignment, anchoring, text
  wrapping + content sizing (heuristic, flagged approximate), overflow/clip,
  nested propagation.
- `core/constraint_model.py` — deterministic constraint extraction and the two
  failure classes that are **reported, never resolved**: contradictions
  (e.g. `min_width > max_width`) and underdetermined bounds.
- `core/breakpoint_model.py` — numeric breakpoints from library tokens
  (sm 640 / md 1024 / lg 1440); changes emitted only when measured across widths.
- `core/layout_analyzer.py` — `LayoutAnalyzer.analyze()` → `LayoutPlan`
  (counts, confidence, diagnostics, flattened constraint report).
- `schemas/layout-plan.schema.json` + `tests/test_layout_engine.py`,
  `test_layout_property.py`, `test_layout_snapshot.py`.

**Constraint:** framework-neutral plan only — never React/CSS (or any framework)
output. All ambiguous/approximate/unsupported layout cases are surfaced in the
report, never silently guessed.

### 8. React & CSS Code Generator (Part 6)

**Status:** Implemented. Consumes the `LayoutPlan` (Part 5), `ResolutionReport`
(Part 4), and the Design IR (Part 3) and emits a framework-neutral **VNode** tree
plus abstract **VStyle** maps. Style adapters (CSS Modules / Tailwind / SCSS) can
render the maps to real strings without re-running analysis.

- `core/generator_types.py` — the `VNode`/`VStyle`/`GeneratorManifest` protocol
  (deterministic serialization for snapshotting).
- `core/react_generator.py` — recursive `LayoutPlan` → `VNode` traversal;
  semantic tag mapping, `data-figma-id` debug IDs, text content extraction.
- `core/css_generator.py` — `LayoutPlan` → `VStyle` mapping: display, fixed
  sizing, padding/gap, flex/grid direction, alignment, absolute positioning.
- `tests/test_generator_snapshot.py` — golden-file + determinism tests.

**Constraint:** generated files are kept separate from handwritten code; no
framework dependencies, no agent frameworks, no screenshot coordinates as the
primary layout strategy. Absolute positioning is emitted only when the layout
solver explicitly requires it.

### 9. Backend Adapter Architecture (Part 10)

**Status:** Implemented. The framework-neutral core pipeline (Parts 3–7) is
decoupled from code generation via replaceable backend adapters.

The core pipeline (IR → Layout → Resolution) is **framework-neutral**. A
backend adapter is the *target-specific lowering* step that converts a
`LayoutPlan` + `Design IR` into generated source code for a particular
framework and styling system.

**Architecture:**

```
Figma  →  Design IR  →  LayoutPlan  →  BackendAdapter.generate()
                                            ↓
                                    Generated code (target-specific)
                                            ↓
                                    Target renderer (browser / simulator)
                                            ↓
                                    Visual comparison  →  Repair
```

**Key rules:**
- A backend MUST declare its capabilities and limitations explicitly via
  `BackendCapabilities` (supported / unsupported / partial features).
- A backend MUST NOT silently approximate a feature it cannot represent.
  When a feature cannot be expressed in the target, the backend records a
  `FidelityLoss` entry — the caller decides whether to proceed.
- A backend consumes the framework-neutral IR and LayoutPlan; it never
  mutates them.

**Composable target model (TypeScript runtime):**

```typescript
interface CodegenTarget {
  framework: Framework;     // "react" | "vue" | "svelte" | "html" | ... | (string & {})
  styling: StylingSystem;   // "css" | "tailwind" | "styled_components" | ... | (string & {})
}
```

Any framework can pair with any styling system. The target is NOT a fixed
enum — it is an open composition. The backend registry resolves whether a
concrete adapter exists for a given combination.

**Components:**

- `backends/protocol.py` — `BackendAdapter` ABC, `Feature` vocabulary (40+
  canonical features), `FidelityLoss`, `BackendCapabilities`,
  `GeneratedOutput`, `WEB_COMMON_FEATURES`.
- `backends/registry.py` — `BackendRegistry` with register/unregister/get/
  require/find/list, auto-discovery via `discover_builtins()`, global
  singleton via `get_registry()`.
- `backends/web_common.py` — **Shared web machinery (Part 14):** `VStyle`/`VNode`,
  `CssStyleGenerator`, `VNodeBuilder`, `ScopedCssGenerator`, `extend_ir_style` (IR
  fills/radius/borders/opacity/shadows/blur/typography/overflow/breakpoints),
  `bp_to_css_prop`. ONE style-mapping implementation for every web target.
- `backends/html_css/` — **Fully implemented** reference backend (Part 10).
  `_HtmlEmitter` + `_wrap_html_document`; reuses the shared web machinery.
  Generates HTML + CSS files.
- `backends/react_tailwind/` — **Implemented (Part 14).** Real TSX generator with
  arbitrary-value Tailwind classes, IR-sourced style/typography, breakpoint variants,
  and token extraction into `tailwind.config.figmaforge.js`.
- `backends/vue/` — **Implemented (Part 14).** Vue 3 SFC: `<template>` scoped
  `n-{id}` classes, `<script setup>`, `<style scoped>` from the shared CSS rules.
- `backends/svelte/` — **Implemented (Part 14).** Svelte component: `<script lang="ts">`
  props, scoped class markup, shared scoped CSS.
- `backends/swiftui/` — **Implemented (Part 14).** SwiftUI view structs: VStack/HStack
  + spacing/alignment, modifier chains (frame/padding/background/cornerRadius/opacity/
  font/shadow), real LinearGradient and `.position()`; unsupported features declared
  explicitly.
- `backends/flutter/` — **Implemented (Part 14).** Flutter widget trees: Row/Column
  with main/cross axis alignment + SizedBox gap separators, Container+BoxDecoration,
  EdgeInsets, Text+TextStyle, Stack+Positioned; unsupported features declared
  explicitly.

**Core modules remain framework-neutral:**
- `ir_types.py` (784 lines) — framework-neutral semantic vocabulary.
- `layout_types.py` (540 lines) — abstract display/sizing/anchoring.
- `layout_engine.py` (988 lines) — inference from IR, no framework assumptions.
- `token_resolver.py` (374 lines) — semantic token resolution.

**Legacy generators preserved for backward compatibility:**
- `core/generator_types.py` — `VNode`/`VStyle` protocol (still used by
  snapshot tests).
- `core/react_generator.py`, `core/css_generator.py` — original generators
  (functionality absorbed into `backends/html_css/`).

**Constraint:** The core pipeline (IR, layout, resolution) MUST remain
framework-neutral. All framework-specific knowledge lives in backend
adapters. No React, Vue, CSS, Tailwind, or any framework concept may leak
into `core/`.

---

## Components

### Agents (3)

All agents are lightweight, isolated, and provide clear value. They are NOT 100 subagents and NOT always-on.

1. **context-scout** — Read-only repository discovery returning a concise evidence summary.
2. **lifecycle-planner** — Converts a complex request into phased work and gates, without editing.
3. **fresh-verifier** — Independently verifies claims using a clean context and no write tools.

**File:** `plugin/figmaforge/agents/`

### Skills (6)

Skills are repeated procedures with known triggers. Only 6 core skills added.

1. **route** — Detect context and select phases, roles, existing skills, execution mode.
2. **lifecycle** — Create or advance an evidence-backed task run.
3. **doctor** — Inspect plugin structure, context cost, dependencies, dormant integrations.
4. **mcp-template** — Explain or render an inert MCP template to stdout.
5. **lsp-template** — Recommend an official LSP plugin or render a custom template.
6. **demo** — Run or explain the bounded demo.

**File:** `plugin/figmaforge/skills/`

### Hooks (3)

Hooks are packaged in `plugin/figmaforge/hooks/hooks.json`, NOT in project `.claude/settings.json`.

1. **SessionStart detector** (`session_detector.py`)
   - Runs the detector; injects concise additional context only when actionable evidence exists.
   - Empty repo: exit 0, no stdout.
   - Missing Python or detector failure: nonblocking.
   - No files created.

2. **PreToolUse external-mutation gate** (`external_mutation_gate.py`)
   - Inspects Bash commands and MCP tool names for creation/update/deletion/publication/deployment/transition/outbound communication.
   - Returns `permissionDecision: 'ask'` for patterns like:
     - `git push`, `package publication`
     - `terraform apply/destroy`, `kubectl apply/delete`
     - `remote POST/PUT/PATCH/DELETE`
     - Jira/Confluence creation/edits
     - Credential changes, plugin installation/marketplace registration
   - Defense in depth — not replacement for Claude Code permissions.

3. **PostToolUse validator** (`post_edit_validator.py`)
   - Triggered on Edit|Write.
   - Reads changed path, looks up canonical validator from detected manifests.
   - Runs only already-declared bounded check.
   - Exits 0 silently if no toolchain/applicable check.
   - Reports observed failure to Claude but does NOT undo the edit.
   - Never installs dependencies, auto-formats, starts services, or infers a command.
   - Avoids Stop hook in initial implementation (prone to loops, no product runtime exists).

---

## MCP Templates

**Location:** `templates/mcp/`

1. **stdio.example.json** — Inert MCP stdio template; uses `example.invalid` for URLs, symbolic env names, no functioning command unless intended.

2. **http-oauth.example.json** — Inert MCP http-oauth template; same safety rules.

3. **README.md** — Explains how to merge a reviewed template manually; no command writes `.mcp.json`, invokes `claude mcp add`, `claude mcp login`, or resets approvals.

**Constraint:** Root `.mcp.json` stays unchanged. No plugin-root `.mcp.json`. MCP templates exist only as templates, not as active configs.

---

## LSP Templates

**Location:** `templates/lsp/`

### Official LSP Plugin Matrix
| Language    | Plugin                | Required Binary |
|-------------|-----------------------|-----------------|
| C/C++       | clangd-lsp            | clangd          |
| C#          | csharp-lsp            | csharp-ls       |
| Go          | gopls-lsp             | gopls           |
| Java        | jdtls-lsp             | jdtls           |
| Kotlin      | kotlin-lsp            | kotlin-language-server |
| Lua         | lua-lsp               | lua-language-server |
| PHP         | php-lsp               | intelephense    |
| Python      | pyright-lsp           | pyright-langserver |
| Rust        | rust-analyzer-lsp     | rust-analyzer   |
| Swift       | swift-lsp             | sourcekit-lsp   |
| TypeScript  | typescript-lsp        | typescript-language-server |

**Custom Template:** `custom-server.example.json` — Reserved for unsupported languages only. No active `.lsp.json` added to repo root or plugin root.

**Constraint:** Installation uses local scope first; remains explicit user action.

---

## Domain Packs

**Location:** `catalog/domains/`

10 domain packs, one per domain. Each pack contains:
- 10 role definitions
- Capability references
- Recommended skills (when available)
- Integration points

**Example pack:** `domains/discovery/` — Contains roles like product-manager, business-analyst, requirements-analyst.

---

## Implementation Sequence

### Phase 1: Safety Checkpoint
1. Re-read git status
2. Create+verify backup (`FigmaForge.backups/<timestamp>/`)
3. Record original hashes for `LICENSE`, `.mcp.json`, `CLAUDE.md`, `.claude/settings.json`
4. Create feature branch `feat/adaptive-claude-platform`
5. Re-report external plaintext-credential warning (do NOT display value)

### Phase 2: Plugin and Marketplace Skeleton
- Create plugin root: `plugin/figmaforge/`
- Create marketplace manifest: `figmaforge/.claude-plugin/plugin.json`
- Create directory structure

### Phase 3: Schemas and Catalogs
- Add exactly 100 unique role entries (10 domains × 10 roles)
- Add capability references
- Add lifecycle and modules schemas

### Phase 4: Detector and Router
- Implement `core/detector.py` (Python stdlib only)
- Implement `core/router.py` (deterministic scoring)
- Verify current repo stays unclassified

### Phase 5: Lifecycle State
- Implement state machine with atomic writes
- Implement append-only events.jsonl
- Add evidence-driven transition rules

### Phase 6: Skills and Agents
- Create 6 skills (route, lifecycle, doctor, mcp-template, lsp-template, demo)
- Create 3 agents (context-scout, lifecycle-planner, fresh-verifier)
- Include short trigger-specific descriptions in frontmatter

### Phase 7: Hooks
- Implement SessionStart detector (session_detector.py)
- Implement PreToolUse external-mutation gate (external_mutation_gate.py)
- Implement PostToolUse validator (post_edit_validator.py)
- Package hooks in `hooks/hooks.json`

### Phase 8: MCP and LSP Templates
- Create MCP templates (stdio, http-oauth, README)
- Create LSP templates (official plugins matrix, custom server)
- Ensure templates are inert (no credential-like values, no functioning commands)

### Phase 9: Documentation, Validation, and Demo
- Write validation plan
- Run bounded E2E demo offline
- Create README, rollback docs, architecture docs
- Update CLAUDE.md

---

## Validation Plan

### Schema Validation
```bash
claude plugin validate --strict plugin/figmaforge
claude plugin validate --strict .
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m compileall plugin/figmaforge/core plugin/figmaforge/hooks
parse every JSON with stdlib or jq
```

### Catalog Validation
- Exactly 100 roles
- IDs/aliases unique
- Every lifecycle phase valid
- Every domain pack exists
- Capability refs syntactically valid
- Missing optional external skills do not fail routing
- Route output conforms to schema

### E2E Demo (Bounded, Offline)
```bash
# Step 1: Validate plugin
claude plugin validate --strict plugin/figmaforge
claude plugin validate --strict .

# Step 2: Detect current repo (FigmaForge)
assert status == "unclassified"
assert no language module/LSP activated

# Step 3: Route task "Design a secure, testable CLI feature and define acceptance criteria"
assert phases returned: universal
assert requirements/architecture/security roles selected
assert no concrete application stack selected
assert no language module/LSP activated

# Step 4: Create lifecycle state in temp dir
# Step 5: Advance intake → discover → define with fixture evidence

# Step 6: Exercise hook fixtures
# - Safe no-op: exit 0
# - Approval request for external mutation: ask
# - No-toolchain post-edit: no-op

# Step 7: Verify MCP/LSP templates inert
# Step 8: Delete demo temp directory

# Step 9: Recheck root PinchTab status and original config hash
```

---

## Safety Invariants (Preserved Forever)

1. **LICENSE** byte-for-byte unchanged
2. **root .mcp.json** retains same PinchTab command/args/empty env/project approval semantics
3. No MCP server approved/connected/authenticated automatically
4. No LSP plugin/language server activated solely because its binary exists
5. No application language/framework/package manager/deployment platform inferred from repo name
6. Plaintext credential in parent settings file never copied/printed/hashed/committed/modified
7. **MCP templates** are inert; no command writes `.mcp.json` or invokes `claude mcp add`/`login`/resets approvals
8. **LSP templates** are inert; no active `.lsp.json` added to repo or plugin root

---

## Backup Strategy

**Location:** `FigmaForge.backups/<UTC-timestamp>/`

**Contents:**
- `repository.bundle` — Git bundle of all refs
- `worktree.tar.gz` — All repo-local files (tracked + untracked, empty .claude extension dirs)
- `manifest.txt` — File list, modes, current commit/branch metadata
- `checksums.sha256` — SHA-256 hashes of all files
- `git-status.txt` — Git status snapshot

**Scope:** All repo-local tracked+untracked files, empty .claude extension dirs, file modes. Excludes parent `/Users/mdshagilnizami/code/projects/.claude/settings.json`, session transcripts, user plugin caches, credential stores.

**Creation:**
- Set restrictive umask (0700)
- Verify tar archive, Git bundle, hashes before continuing

**Rollback (documented, not auto-executed):**
1. Stop and preserve failed working tree
2. Verify selected backup checksums
3. Restore into new sibling dir first
4. Compare restored tree to backup manifest
5. Only replace working dir after explicit user confirmation
6. Use Git bundle to recover committed refs independently
7. Recheck root .mcp.json and LICENSE match pre-change hashes

**Security:**
- Mode 0700 directory
- No world-writable files
- No signed executables except `bin/figmaforge` (created in Phase 9)

---

## Summary

FigmaForge is a planned, not-implemented, technology-agnostic, adaptive platform. It provides:

- **100 catalog roles** across 10 domains
- **6 core skills** (route, lifecycle, doctor, mcp-template, lsp-template, demo)
- **3 agents** (context-scout, lifecycle-planner, fresh-verifier)
- **3 hooks** (SessionStart, PreToolUse, PostToolUse)
- **Detector + Router** with deterministic, evidence-based scoring
- **10-phase lifecycle** with atomic state and append-only events
- **MCP/LSP templates** for safe template consumption
- **Evidence-driven transitions** (not prose claims)
- **Strict safety invariants** and backup/rollback strategy

**Implementation status:** Core modules (detector, router, catalog, state machine) are implemented and tested. The Figma-to-Code pipeline (Parts 1-8) is fully implemented. The backend adapter architecture (Parts 10 + 14) is implemented with all six backends real — HTML+CSS (reference), React+Tailwind, Vue, Svelte, SwiftUI, and Flutter — sharing one web style-mapping implementation (`web_common.py`) and enforcing the capability-vs-output honesty rule via a repo-wide audit. The TypeScript orchestration runtime (Part 9) is implemented with composable code-generation targets. Integration between the Adaptive Platform and the Figma pipeline is in progress.

---

**Next:** Begin with Phase 1 (Safety Checkpoint) as the first implementation step.
