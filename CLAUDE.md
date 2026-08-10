# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 1. Project Overview

FigmaForge is a technology-agnostic, adaptive, full-lifecycle Claude Code engineering platform. It is implemented as a Claude Code plugin, NOT as a standalone application. It enables any software project type by detecting stack-specific signals and routing to the appropriate capabilities dynamically, without requiring per-repo authoring of agents, skills, or workflows.

This is a complete platform implementation (version 0.0.1-dev), containing a 100-role catalog, detection algorithms, a deterministic routing engine, a 10-phase lifecycle state machine, safety hooks, and MCP/LSP templates.

## 2. Technology Stack

- **Code:** Python 3 (standard library only) for detection, routing, lifecycle state, and hooks.
- **Data:** JSON (`.claude-plugin/plugin.json`, `catalog/roles.json`, `hooks/hooks.json`).
- **Interfaces:** Claude Code Plugin constraints and schema structures.
- **No Application Framework:** The repository does NOT use React, Node, Webpack, Vite, FastAPI, etc. It only uses core Python and shell commands for configuration and discovery.

## 3. Important Repository Structure

- `LICENSE` — MIT License (preserved exactly as 2026 Md Shagil Nizami).
- `CLAUDE.md` — This repository-wide configuration file.
- `.claude/settings.json` — Minimal configurations mapping for Claude Code (no secrets).
- `.mcp.json` — Contains a project-scoped mapping (`stdio` server `pinchtab`).
- `plugin/figmaforge/` — The primary source code and content:
  - `core/` (Python): `detector.py`, `router.py`, `catalog.py`, `state.py` and structural hooks.
  - `catalog/`: `roles.json` (100 roles across 10 domains).
  - `agents/`: `context-scout.md`, `lifecycle-planner.md`, `fresh-verifier.md`.
  - `skills/`: `route.md`, `lifecycle.md`, `doctor.md`, `mcp-template.md`, `lsp-template.md`, `demo.md`.
  - `hooks/`: `hooks.json` mapping.
  - `schemas/`: Custom validation schemas.
  - `templates/`: Inert examples for MCP and LSP configurations.
  - `tests/`: Basic validation scripts.
- `docs/architecture.md` — In-depth architectural blueprint.

Nested `CLAUDE.md` files should NOT be created. The structure is global to the plugin domain.

## 4. Runtime Architecture and Data Flow

1. **User Request:** Arrives in natural language.
2. **SessionStart / Lifecycle Hooks:** Validate and analyze context silently.
3. **Detector:** Analyzes repository artifacts (manifests, languages, tools) strictly through signatures.
4. **Router:** Deterministically outputs phases, roles, and execution execution_mode based on signals and catalog roles, generating an actionable pipeline.
5. **Phase Router:** Integrates with the 10-phase lifecycle state machine (`.figmaforge/runs/<run-id>/state.json`). Transitions are evidence-based, recorded as localized append-only events.
6. **Delivery:** The chosen roles provide actionable inputs for Claude Code to execute safely.

## 5. Verified Development Commands

* **Validation:** 
  `claude plugin validate --strict plugin/figmaforge`
* **Test the Detector:**
  `python3 plugin/figmaforge/tests/test_detector.py`
  `python3 plugin/figmaforge/tests/test_integration.py`
* **Test the Implementation:**
  Load with: `claude --plugin-dir ./plugin/figmaforge`
  Invoke via: `claude --plugin-dir ./plugin/figmaforge -p '/figmaforge:route Design a secure CLI feature'`

## 6. Coding Conventions

- **Reuse Constraints:** No new libraries or dependencies (e.g. LangGraph, CrewAI, ADK). Rely on Python stdlib and structured Claude prompts natively.
- **Architecture Stability:** Keep the 10-domain 100-role catalog format static unless an architectural RFC is approved. 
- **Evidence Over Inference:** Rely solely on explicit JSON/Manifest signals via `detector.py`.
- **Atomic Operations:** File outputs for states must remain atomic (`LifecycleState`).

## 7. Testing Requirements

- The test scripts (`plugin/figmaforge/tests/*.py`) must successfully execute (`0` return code).
- Adding a new module or role requires verifying the schema matches (`claude plugin validate --strict`).

## 8. Safety Rules

- **PreToolUse external-mutation gate**: Prevents unintended or malicious outbound/external/infrastructure state changes. It flags shell patterns like `git push`, `terraform apply`, and `kubectl delete`.
- **Inert Templates ONLY:** Ensure `templates/mcp/*` remain purely inert structures without exposed tokens or destructive pathways. 
- Never expose or copy credentials.
- No deploying, committing, rotating secrets, or destructive actions without express, structured gates.

## 9. Change Workflow

1. Discuss architecture impact (review `docs/architecture.md`).
2. Run standard validations on current tree (`python3 plugin/figmaforge/tests/test_integration.py`).
3. Make atomic, minimal coherent changes explicitly matching schemas.
4. Verify tests and the plugin definition pass successfully.
5. Only document verified, executable routines.

## 10. Definition of Done

- Scope is strictly adhered to (no speculative integrations).
- All changes maintain schema constraints (run `claude plugin validate --strict`).
- `tests/test_integration.py` successfully reads and catalogs the changes without failures.
- Changes align exactly with architectural constraints in `docs/architecture.md`.
- No exposed credentials, secrets, or unintentional active `.lsp.json`/`.mcp.json` templates exist.