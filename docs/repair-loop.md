# Visual Repair Loop — Design Document

## Overview

The visual repair loop is an automatic, iterative system that detects visual
differences between the Figma design and the rendered output, then repairs
them by modifying **source code and design tokens** — never screenshots or
reference images.

The loop runs: **Render → Diff → Classify → Plan → Execute → Re-render**

until a stopping condition is met.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        RepairLoop                             │
│                                                               │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐ │
│  │  Render   │──▶│   Diff   │──▶│ Classify │──▶│   Plan   │ │
│  │ Harness   │   │  Engine  │   │  Repair  │   │  Patches │ │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘ │
│       ▲                                              │        │
│       │              ┌──────────┐                    │        │
│       └──────────────│ Execute  │◀───────────────────┘        │
│                      │ Patches  │                              │
│                      └──────────┘                              │
│                           │                                    │
│                      ┌────▼────┐                               │
│                      │ History │                               │
│                      │ Manifest│                               │
│                      └─────────┘                               │
└──────────────────────────────────────────────────────────────┘
```

## Modules

| Module | Responsibility |
|--------|---------------|
| `repair_classifier.py` | Converts `DiffReport` mismatches into classified `RepairCandidate` objects |
| `patch_planner.py` | Orders candidates by repair strategy, groups shared tokens, produces `PatchPlan` |
| `patch_executor.py` | Applies patches to source artifacts (tokens, styles, layout), records mutations |
| `repair_loop.py` | Iteration controller — orchestrates the render-diff-classify-plan-execute cycle |
| `repair_history.py` | Append-only manifest preserving every iteration's state for rollback |

## Repair Candidate Categories

Every mismatch from the diff engine is classified into exactly one of nine categories:

| Category | Description | Example |
|----------|-------------|---------|
| `geometry` | Width/height/position mismatch | Expected 200×80, rendered 190×70 |
| `spacing` | Padding/gap/margin mismatch | Small uniform size delta (≤4px) |
| `typography` | Font size/weight/line-height mismatch | Expected 16px, rendered 14px |
| `color` | Fill/border color mismatch | Expected #ff0000, rendered #ff0033 |
| `token` | Design token value mismatch | Token `color-primary` resolves wrong |
| `asset` | Image/SVG reference mismatch | Wrong asset URL or missing asset |
| `responsive` | Breakpoint behavior mismatch | Layout doesn't switch at 768px |
| `missing_element` | Present in design, absent in render | Figma node has no rendered output |
| `extra_element` | Present in render, absent in design | Rendered node not in Figma file |

## Source Attribution

Each repair candidate is mapped back to its source artifacts:

```python
SourceMapping(
    figma_node_id="123:456",      # Figma node that originated the difference
    component_name="Header",       # Generated component name
    source_file="src/Header.tsx",  # Source file path (from GeneratorManifest)
    css_selector='[data-figma-id="123:456"]',  # CSS selector
    token_key="color-primary",     # Bound design token (if any)
    token_property="fontSize",     # Which token property is affected
)
```

## Repair Strategy (Priority Order)

Patches are applied in this order to maximize impact and minimize risk:

1. **Global environment mismatches** — viewport, background, global styles
2. **Missing / extra elements** — structural issues must be fixed first
3. **Parent geometry before child geometry** — fixing a parent may fix children
4. **Shared tokens before local styles** — one token change fixes multiple nodes
5. **Layout constraints before absolute coordinates** — flex/grid before position:absolute
6. **Typography before fine pixel offsets** — font changes affect layout
7. **Assets before color tuning** — correct images before adjusting colors
8. **Re-run full visual comparison** after each meaningful batch

## Stopping Conditions

The loop terminates when **any** of these conditions is met:

| Condition | Description | Default |
|-----------|-------------|---------|
| `threshold_satisfied` | Similarity score ≥ configured threshold | 0.95 |
| `no_safe_repair` | Planner produced zero patches | — |
| `insufficient_progress` | Score improvement < minimum per iteration | 0.005 |
| `max_iterations_reached` | Iteration count reached the hard limit | 10 |
| `approval_denied` | Human reviewer rejected the patch plan | — |
| `regression_detected` | Score dropped after applying patches | — |

### Configuration

```python
RepairConfig(
    similarity_threshold=0.95,     # stop when score >= this
    max_iterations=10,             # hard iteration limit
    min_progress=0.005,            # minimum improvement per iteration
    min_patches_per_iteration=1,   # stop if fewer patches generated
    require_approval=False,        # pause for human approval
    auto_rollback_on_regression=True,  # roll back if score drops
)
```

## Safety Rules

### Invariants (NEVER violated)

1. **Never modify screenshots or reference images.** The repair loop only modifies source code, design tokens, and layout constraints.
2. **Never hide differences.** Every mismatch is either classified into a repair candidate or reported as unclassifiable — nothing is silently dropped.
3. **Never blur or alter reference images.** The Figma design IR is the immutable source of truth.
4. **Never make arbitrary broad rewrites.** Each patch is the smallest possible source-level change that addresses a specific mismatch.
5. **Prefer regeneration over manual editing.** When a patch changes a token or layout constraint, the generators should re-emit the output rather than patching generated files directly.

### Rollback

Every mutation is recorded as a `MutationRecord` with the old and new values.
The `PatchExecutor.rollback()` method restores all mutations in reverse order.
The `RepairHistory` manifest preserves the complete state at each iteration,
enabling rollback to any previous iteration.

### Approval Gate

When `require_approval=True`, the loop pauses before each batch of patches
and invokes the approval callback. If the callback returns `False`, the loop
stops with `approval_denied` — no patches are applied.

## Iteration History

Every iteration records:

- `similarity_before` / `similarity_after` — score change
- `diff_report` — full mismatch list from the diff engine
- `classification` — all repair candidates with categories and confidence
- `patch_plan` — ordered patches with strategy justification
- `execution_result` — which patches succeeded/failed
- `screenshot_path` — rendered output for this iteration
- `source_diff` — summary of source changes applied
- `repair_decisions` — human-readable list of what was changed and why

The history is persisted as JSON and can be loaded/saved for debugging.

## Confidence Scoring

Each repair candidate receives a deterministic confidence score (0.0–1.0):

| Factor | Bonus |
|--------|-------|
| Base confidence | 0.5 |
| Both expected and actual values present | +0.2 |
| Well-defined mismatch type (geometry, typography, missing) | +0.2 |
| Partial mismatch type (spacing, color) | +0.1 |
| Node has a bound design token | +0.1 |

## Test Coverage

30 tests across 6 test classes:

| Class | Tests | Coverage |
|-------|-------|----------|
| `TestRepairClassifier` | 8 | Category classification, spacing refinement, shared tokens, serialization |
| `TestPatchPlanner` | 4 | Strategy ordering, shared-token grouping, parent-before-child |
| `TestPatchExecutor` | 4 | Token/style patches, rollback, rejected patches |
| `TestRepairHistory` | 5 | Iteration recording, ordering, best score, save/load |
| `TestRepairLoop` | 5 | Threshold stopping, max iterations, approval, history, serialization |
| `TestFixtureRepairLoop` | 3 | End-to-end pipeline with intentional defects |

## File Inventory

| File | Lines | Purpose |
|------|-------|---------|
| `core/repair_classifier.py` | 425 | DiffReport → RepairCandidates |
| `core/patch_planner.py` | 461 | Candidates → ordered PatchPlan |
| `core/patch_executor.py` | 444 | Apply patches with rollback |
| `core/repair_loop.py` | 423 | Iteration controller |
| `core/repair_history.py` | 213 | Iteration manifest |
| `tests/test_repair_loop.py` | 828 | 30 tests |
| `docs/repair-loop.md` | — | This document |
