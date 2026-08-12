# FigmaForge Runtime Architecture

## Overview

The FigmaForge runtime is a TypeScript orchestration layer that coordinates the complete Figma-to-code pipeline:

```
Figma input → normalized IR → token/component resolution → layout inference
→ code generation → asset loading → browser rendering → visual comparison
→ source repair → final verification
```

Built with **zero external runtime dependencies** — only TypeScript and Node.js stdlib.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLI (main.ts)                           │
│   run  │  inspect  │  render  │  compare  │  repair  │  replay │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                  PipelineCoordinator                            │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ StateMachine: deterministic stage transitions            │  │
│  │ EventLog: append-only audit trail                        │  │
│  │ CheckpointManager: resumable checkpoints                 │  │
│  │ ArtifactStore: content-addressed output storage          │  │
│  │ BudgetTracker: token/time/iteration limits               │  │
│  │ ToolRegistry: typed tool protocol + invocation tracking  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Security Boundaries                                      │  │
│  │  PathSandbox   │ ShellGuard │ SecretGuard │ ApprovalGate │  │
│  │  AssetValidator │ BudgetTracker │ AbortSignal            │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Retry with exponential backoff + cancellation            │  │
│  │ Replaceable ModelProvider interface                      │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│              Evaluation Harness                                 │
│  Golden fixtures │ Snapshot comparison │ Failure injection      │
│  Metrics collection │ Suite runner                             │
└─────────────────────────────────────────────────────────────────┘
```

## Pipeline Stages

| # | Stage | Input | Output | Description |
|---|-------|-------|--------|-------------|
| 1 | `ingest` | Figma file key | Raw Figma JSON | Fetch file from Figma API |
| 2 | `normalize` | Raw Figma JSON | Design IR | Convert to framework-neutral IR |
| 3 | `resolve` | Design IR + library | ResolutionReport | Match components and tokens |
| 4 | `layout` | Design IR | LayoutPlan | Infer layout constraints |
| 5 | `generate` | LayoutPlan | VNode/VStyle | Generate React/CSS code |
| 6 | `assets` | IR + asset refs | AssetManifest | Load and hash images/SVGs |
| 7 | `render` | Generated code | Screenshot + metadata | Browser render |
| 8 | `compare` | Screenshot + plans | DiffReport | Visual comparison |
| 9 | `repair` | DiffReport | Patches + re-render | Iterative visual repair |
| 10 | `verify` | Final render + diff | Pass/fail + metrics | Final similarity check |

## Module Reference

### Core Modules (`runtime/src/core/`)

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `types.ts` | 159 | Pipeline stages, IDs, config, model provider interface |
| `events.ts` | 138 | Structured event log (append-only, JSON-serializable) |
| `checkpoint.ts` | 165 | Checkpoint save/load/resume after each stage |
| `artifacts.ts` | 176 | Content-addressed artifact storage |
| `tools.ts` | 203 | Typed tool registry + Python bridge |
| `state.ts` | 229 | Deterministic state machine with transitions |
| `budget.ts` | 147 | Token, time, and iteration budget enforcement |
| `retry.ts` | 155 | Retry with exponential backoff + cancellation |
| `security.ts` | 400 | Path sandbox, secret guard, shell guard, approval gate |
| `pipeline.ts` | 328 | Pipeline coordinator orchestrating all stages |
| `evaluation.ts` | 389 | Golden fixtures, snapshot comparison, failure injection |
| `index.ts` | 14 | Barrel export |

### CLI (`runtime/src/cli/`)

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `main.ts` | 373 | CLI entry point with 6 commands |

### Tests (`runtime/tests/`)

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `test_framework.ts` | 176 | Minimal test framework (no dependencies) |
| `test_all.ts` | 939 | 79 tests across 12 suites |
| `run_all.ts` | 24 | Test runner entry point |

## Key Design Decisions

### 1. Deterministic State Machine

All pipeline transitions are explicit and enforced. The state machine:
- Requires stages to execute in order
- Validates status before each transition
- Records every transition in the event log
- Supports checkpoint-based resume

### 2. No Agent Frameworks

The runtime is pure TypeScript — no ADK, LangGraph, CrewAI, Temporal, or any orchestration framework. This ensures:
- Full control over execution flow
- Deterministic behavior for reproducibility
- Zero external runtime dependencies
- Easy debugging and testing

### 3. Security by Default

- **PathSandbox**: Filesystem access restricted to explicitly approved directories
- **ShellGuard**: Only pre-approved commands (python3, node) can execute
- **SecretGuard**: Automatic detection and redaction of secrets in logs
- **ApprovalGate**: Explicit user consent required before writing files
- **AssetValidator**: Size and type validation for all external assets

### 4. Replaceable Model Provider

The `ModelProvider` interface allows swapping LLM backends without changing pipeline code:
```typescript
interface ModelProvider {
  readonly name: string;
  complete(prompt: string, options?: ModelOptions): Promise<ModelResult>;
}
```

A `NullModelProvider` is included for fully deterministic runs.

### 5. Resumable Checkpoints

After each stage completes, a checkpoint is saved. If the process crashes:
- The run can resume from the latest valid checkpoint
- Already-completed stages are skipped
- Metrics are restored from the checkpoint

### 6. Content-Addressed Artifacts

Every artifact is stored with a SHA-256 content hash in its filename, enabling:
- Deduplication
- Integrity verification
- Cache-friendly storage

## CLI Commands

```bash
# Run the full pipeline
figmaforge run --file-key=<key> --output-dir=./output

# Inspect a previous run
figmaforge inspect --run-id=<id> --output-dir=./output

# Replay event log from a previous run
figmaforge replay --run-id=<id> --output-dir=./output

# Single-stage execution (requires prior run)
figmaforge render --run-id=<id>
figmaforge compare --run-id=<id>
figmaforge repair --run-id=<id>
```

## Evaluation

### Golden Fixtures

Located in `runtime/evaluation/fixtures/golden/`:
- `simple-button/` — Single button component
- `login-screen/` — Multi-element form layout
- `card-layout/` — Auto-layout card grid

Each fixture contains:
- `figma.json` — Figma API response fixture
- `config.json` — Test thresholds
- Optional: `expected_ir.json`, `expected_layout.json`, `expected_code.json`

### Running Evaluation

```bash
# Build and run tests
npx tsc && node dist/runtime/tests/run_all.js

# Run evaluation suite programmatically
import { runEvalSuite } from "./runtime/src/core/evaluation.js";
const result = await runEvalSuite({
  fixturesDir: "runtime/evaluation/fixtures/golden",
  outputDir: "./eval-output",
  similarityThreshold: 0.95,
  maxRepairIterations: 10,
});
```

## Acceptance Criteria

| Criterion | Status | How |
|-----------|--------|-----|
| Same input → same trace | ✅ | Deterministic pipeline, no randomness except retry jitter |
| Crashed run can resume | ✅ | Checkpoint after each stage, `resumeFromCheckpoint()` |
| No repo changes before approval | ✅ | `ApprovalGate` + `PathSandbox` |
| Every repair traceable | ✅ | `EventLog` records every action |
| Failed repair can't replace better result | ✅ | Rollback on regression, state machine validation |
| Full pipeline with one command | ✅ | `figmaforge run --file-key=<key>` |
