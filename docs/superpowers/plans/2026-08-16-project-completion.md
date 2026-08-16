# FigmaForge Project Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the project’s remaining production gaps in priority order while preserving the existing deterministic pipeline and honest degradation rules.

**Architecture:** First make the ten-stage runtime durable by restoring shared artifacts, event history, and adaptive context across process restarts. Then add reproducible CI and live/native acceptance layers. Finally expand adaptive policy execution, model-provider integration, backend fidelity, accessibility, visual diagnostics, and operational controls as opt-in capabilities with explicit contracts.

**Tech Stack:** Python standard library and `unittest`; TypeScript/Node.js runtime; Playwright/Chromium; Docker/Colima Flutter SDK; GitHub Actions; JSON artifacts and schemas.

**Spec:** `docs/superpowers/specs/2026-08-16-adaptive-preflight-design.md`, plus the current architecture and runtime documents.

## Global Constraints

- Preserve the existing ten-stage order and artifact honesty rules.
- Existing non-adaptive runs must remain behaviorally unchanged.
- Python tests use `python3 -m unittest`; do not introduce pytest.
- Generated output must remain deterministic for identical inputs.
- No secrets may be written to artifacts, logs, fixtures, or committed files.
- Browser, native, network, and Docker checks must be explicit and report skips/failures honestly.
- Every implementation change begins with a failing test and ends with targeted plus regression verification.

---

### Task 1: Durable checkpoint resume

**Files:**
- Modify: `runtime/src/core/checkpoint.ts`
- Modify: `runtime/src/core/artifacts.ts`
- Modify: `runtime/src/core/pipeline.ts`
- Modify: `runtime/src/cli/main.ts`
- Test: `runtime/tests/test_all.ts`, `runtime/tests/adaptive_run.test.ts`

**Interfaces:**
- `Checkpoint` gains a serializable `shared` snapshot for stage inputs that are safe to persist.
- `PipelineCoordinator` restores shared state before selecting the resume stage.
- Artifact manifests and event logs are rehydrated before the resumed run continues.

- [ ] Write a failing test that completes ingest/normalize, constructs a new coordinator, resumes, and proves resolve receives the restored `irJson`.
- [ ] Write a failing test that proves prior artifacts and events remain in the final manifest after resume.
- [ ] Implement explicit serializable shared-state capture with secret/path filtering.
- [ ] Restore shared state and rehydrate stores before stage execution.
- [ ] Verify targeted resume tests, `npm test`, and the full integration tier.

### Task 2: Explicit resume semantics

**Files:**
- Modify: `runtime/src/cli/main.ts`
- Modify: `runtime/src/core/pipeline.ts`
- Test: `runtime/tests/test_all.ts`, `runtime/tests/adaptive_run.test.ts`
- Docs: `README.md`, `docs/runtime-troubleshooting.md`

**Interfaces:**
- `RuntimeConfig` receives `resume: boolean`.
- `figmaforge run` starts fresh by default and resumes only with `--resume`.

- [ ] Write failing tests for fresh same-run execution and explicit `--resume`.
- [ ] Implement the flag in config and pipeline startup.
- [ ] Reject incompatible fresh runs with a clear run-id/checkpoint message or archive old state safely.
- [ ] Verify no accidental resume occurs.

### Task 3: CI, packaging, and release validation

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/integration.yml`
- Modify: `package.json`, `runtime/package.json`
- Create: `scripts/release_check.sh`
- Test: CI workflow validation and local release smoke commands

**Interfaces:**
- Fast CI runs Python tests, TypeScript build/tests, plugin validation, and diff checks.
- Integration CI runs Chromium and Docker Flutter jobs where runners support them.

- [ ] Define the fast workflow with pinned Python/Node versions and no secret-dependent tests.
- [ ] Define optional integration jobs with explicit environment labels and artifact uploads.
- [ ] Add package/release smoke validation without publishing.
- [ ] Verify workflows syntactically and run the local equivalent.

### Task 4: Authenticated Figma acceptance

**Files:**
- Modify: `plugin/figmaforge/tests/test_pipeline_cli.py`
- Create: `plugin/figmaforge/tests/test_live_figma_acceptance.py`
- Modify: `docs/runtime-troubleshooting.md`, `docs/real-figma-demo.md`

**Interfaces:**
- Live acceptance is opt-in via `FIGMAFORGE_LIVE_ACCEPTANCE=1`, `FIGMA_TOKEN`, and a test file key.
- Missing credentials produce an explicit skip, never a fabricated pass.

- [ ] Add credential-free skip tests and secret-redaction assertions.
- [ ] Add the authenticated smoke flow: ingest → baseline → generate → render → compare.
- [ ] Bound network time, retries, asset bytes, and output retention.
- [ ] Verify fixture and live paths separately.

### Task 5: Native compile/run acceptance

**Files:**
- Modify: `plugin/figmaforge/scripts/native_acceptance.py`
- Create: `plugin/figmaforge/scripts/native_run_acceptance.py`
- Create: `plugin/figmaforge/tests/test_native_run_acceptance.py`
- Docs: `README.md`, `docs/runtime-architecture.md`

**Interfaces:**
- SwiftUI uses an opt-in Xcode project/simulator check.
- Flutter uses the existing Docker analyzer plus an opt-in test/build target.
- Toolchain absence is structured as `skipped`.

- [ ] Write failing command-construction tests for Swift and Flutter run checks.
- [ ] Implement minimal generated-app wrappers and bounded compile/analyze commands.
- [ ] Add simulator/emulator screenshot checks only when the platform is available.
- [ ] Verify Docker Flutter and local Swift parsing without requiring credentials.

### Task 6: Adaptive route policies

**Files:**
- Modify: `runtime/src/core/adaptive_preflight.ts`, `runtime/src/core/pipeline.ts`
- Modify: `runtime/src/core/security.ts`, `runtime/src/cli/main.ts`
- Test: `runtime/tests/adaptive_run.test.ts`
- Docs: `docs/superpowers/specs/2026-08-16-adaptive-preflight-design.md`

**Interfaces:**
- Route phases become observable policy metadata first.
- Approval gates remain authoritative and are mapped to existing approval callbacks.
- Unsupported policy requests fail clearly rather than silently changing execution.

- [ ] Write failing tests for approval-gate recording, policy compatibility, and unsupported policy rejection.
- [ ] Implement policy validation and event emission without bypassing `--no-approval` semantics.
- [ ] Verify adaptive fresh and resumed runs.

### Task 7: Model-provider and host adapters

**Files:**
- Modify: `runtime/src/core/providers.ts`, `runtime/src/core/types.ts`, `runtime/src/core/tools.ts`
- Create: `runtime/src/adapters/provider_protocol.ts`
- Create: `runtime/tests/provider_contract.test.ts`
- Docs: `README.md`, `docs/runtime-architecture.md`

**Interfaces:**
- Provider contract supports structured JSON, timeout, cancellation, token accounting, and redacted errors.
- Anthropic/OpenAI remain built-ins; arbitrary HTTP-compatible and local providers use the same adapter interface.

- [ ] Write failing contract tests for structured output, timeout, cancellation, and provider errors.
- [ ] Implement the provider adapter and connect token usage to `BudgetTracker`.
- [ ] Add a host-neutral CLI/JSON adapter independent of Claude Code.
- [ ] Verify with local fake providers and no network.

### Task 8: Backend fidelity and accessibility

**Files:**
- Modify: `plugin/figmaforge/core/ir_types.py`, backend modules, capability audit
- Create: `plugin/figmaforge/core/accessibility.py`
- Create: `plugin/figmaforge/tests/test_accessibility.py`
- Docs: `docs/design-ir.md`, `README.md`

**Interfaces:**
- Unsupported features remain explicit `FidelityLoss` entries.
- Accessibility report is a separate artifact with node-level findings.

- [ ] Add failing fixtures for gradients, SVG/image assets, interactions, semantic text, and focusable controls.
- [ ] Implement accessible-role/name/contrast checks and backend mappings where safe.
- [ ] Add multi-backend regression assertions and update capability declarations.
- [ ] Verify deterministic output and honest losses.

### Task 9: Visual diagnostics and operational hardening

**Files:**
- Modify: `plugin/figmaforge/core/pixel_diff.py`, `runtime/src/core/screenshot_compare.ts`
- Modify: `runtime/src/core/events.ts`, artifact retention/security modules
- Create: `docs/adr/` decisions for caching, retention, and baseline provenance
- Test: diff heatmaps, run locking, cleanup, redaction, and concurrency tests

**Interfaces:**
- Diff reports may include optional heatmap artifacts and multi-viewport summaries.
- Runs acquire a lock per output/run id and release it on completion or cancellation.
- Retention is bounded and configurable; original baselines remain immutable.

- [ ] Write failing tests for heatmap generation, lock contention, cleanup bounds, and secret redaction.
- [ ] Implement each feature behind explicit flags/configuration.
- [ ] Add cache keys for normalized IR, assets, and deterministic generated output.
- [ ] Verify concurrent run behavior and artifact provenance.

### Task 10: Documentation and final audit

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `docs/architecture.md`, `docs/DEVELOPMENT_LOG.md`
- Create: `docs/compatibility-matrix.md`

- [ ] Remove stale historical claims that describe implemented features as placeholders.
- [ ] Publish a matrix for backend features, toolchain checks, provider support, and host portability.
- [ ] Run all fast, integration, native, plugin-validation, and release checks.
- [ ] Record remaining intentional non-goals and final verified counts.
