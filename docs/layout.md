# FigmaForge Responsive Layout & Constraint Solver (Part 5)

**Status:** Implemented. Input is the normalized Design IR (Part 3) plus the
resolved project library (Part 4). No code generation yet.

Part 5 answers *"how should this design lay out?"*. It reads the IR's layout,
dimensions, positioning, responsive constraints, and typography, and produces a
single **framework-neutral layout plan** — a schema-validated JSON report a
future generator would consume. Nothing here emits React/CSS (or any framework).

```
Design IR (IRDocument) + project library (library/)
        │  core/layout_engine.py      (inference: flex/grid/absolute, sizing,
        │                              spacing, alignment, anchoring, text,
        │                              overflow, nested propagation)
        │  core/constraint_model.py   (constraint extraction + contradiction /
        │                              underdetermination detection + BoxSolver)
        ▼
Per-node LayoutNodePlan trees
        │  core/breakpoint_model.py   (numeric ladder + evidence-only changes)
        │  core/layout_analyzer.py    (confidence, counts, diagnostics)
        ▼
LayoutPlan (JSON, schema-validated by schemas/layout-plan.schema.json)
```

## The 12 requirements → how each is met

| # | Requirement | Implementation |
|---|-------------|----------------|
| 1 | Auto-layout → flex/grid | `IRLayout.mode == "auto"` → `display:flex`; `"grid"` + `layoutGrids` → `display:grid`. Semantic flow preferred. |
| 2 | Fixed / fill / hug / percent | Per-axis `SizingSpec.mode`. `FIXED`→`fixed`, `FILL`→`fill`, `AUTO`→`hug`, flex-`grow`→`percent` (share of free space). |
| 3 | Min/max width & height | `min_width/max_width/min_height/max_height` from `IRDimensions` + `IResponsive`; every resolved size is clamped through `BoxSolver`. |
| 4 | Padding, margin, gap | Padding + gap from `IRLayout`. Margin is **only** inferred from evidence (absolute offsets, `align_self`); Figma has no native margin — otherwise reported not-modeled. |
| 5 | H/V alignment | `primaryAxisAlignItems`→`justify`, `counterAxisAlignItems`→`align`, `layoutAlign`→`align_self`; cross-axis placement (MIN/CENTER/MAX/STRETCH). |
| 6 | Absolute positioning where necessary | Only nodes the IR positions absolutely (`position.mode=="absolute"` or raw `layoutPositioning: ABSOLUTE`). Flow children are never made absolute. |
| 7 | Anchoring & constraints | Figma `constraints` MIN/CENTER/MAX/STRETCH/SCALE → anchor pairs with content-relative offsets. STRETCH re-derives the extent. |
| 8 | Responsive breakpoints | Numeric ladder from library breakpoint tokens (sm 640 / md 1024 / lg 1440). Changes emitted **only** when the engine's own prediction differs across widths. |
| 9 | Text wrapping & content sizing | Heuristic glyph widths (`chars × font-size × 0.55`, documented), greedy word-wrap; hug text uses measured content; flagged `approximate`. |
| 10 | Overflow / clip / scroll | `wrap` + measured content beyond the box → `clip`; `scroll` is **never inferred** (reported as unsupported — Figma IR has no scroll). |
| 11 | Nested propagation | Post-order build: hug extents resolve bottom-up, fill/percent resolve top-down against the parent content box; children laid into the solved box. |
| 12 | Confidence scores | Evidence-based 0..1 per node (see below), aggregated per plan. Ambiguous/approximate cases lower the score and add a diagnostic. |

## Determinism & honesty rules

- **Fixed sizes are preserved exactly**, clamped only when the design's own
  min/max require it.
- **Nothing is invented.** A bound that cannot be solved from evidence is marked
  *underdetermined* and contributes no number (``box: null``).
- **Contradictions are reported, not resolved.** `min_width > max_width`, a fixed
  value outside its own min/max, and negative min/max are flagged
  (`contradiction`) and zero the node's confidence.
- **Relative sizing inside hug is underdetermined.** A child that `fill`s or
  `percent`s an axis whose container is hug-sized cannot be resolved
  (`fill_or_percent_in_hug_container`) — classic CSS percentage-in-auto, reported
  honestly.
- **Breakpoints are measured, not guessed.** Every `BreakpointChange` carries
  `evidence` describing the measured difference; nodes that don't change are
  listed explicitly under `no_change`.
- **Text measurement is approximate.** No glyph metrics exist in a stdlib-only
  analysis path; heuristic widths are always flagged `approximate` and lower
  confidence.

## Confidence model

Per node, start at `1.0`; a contradiction zeroes it (`0.0`); otherwise
deductions (each recorded in `assumptions`):

| Assumption / evidence | Deduction |
|-----------------------|-----------|
| `text_width_heuristic` | −0.30 |
| `fill_or_percent_in_hug_container` | −0.30 |
| `hug_no_content` | −0.30 |
| `absolute_without_anchors` | −0.20 |
| `grid_hug_approximated` | −0.15 |
| `scale_anchor_approximated_as_min` | −0.10 |
| predicted bounds ≠ Figma bounds | −0.10 |

Bands for the aggregate: high > 0.75, medium 0.45–0.75, low < 0.45. The plan
reports `min`, `mean`, and band counts.

## Modules

- `core/layout_types.py` — `LayoutPlan` / `LayoutNodePlan` + value objects, all
  `to_dict()`-serializable.
- `core/layout_engine.py` — `LayoutEngine.screens(document, viewport, base)` →
  per-page plan trees; `TextMeasurer` heuristic.
- `core/constraint_model.py` — `ConstraintModel.report()` (extract + detect),
  `BoxSolver` primitives.
- `core/breakpoint_model.py` — `BreakpointModel` ladder + `infer()` diffing
  measured signatures.
- `core/layout_analyzer.py` — `LayoutAnalyzer.analyze(document, library, viewport)`
  → `LayoutPlan` with counts, confidence, diagnostics, flattened constraints.
- `schemas/layout-plan.schema.json` — the report schema (validated by the
  existing `core/ir_validator.py`).
- `fixtures/figma/layout_{desktop,tablet,mobile,nested,content_overflow}.json`
- `tests/test_layout_engine.py`, `test_layout_property.py`, `test_layout_snapshot.py`
- `tests/snapshots/layout-plan.json`

## Usage

```python
from core.figma_types import FigmaFile
from core.ir_builder import IRBuilder
from core.layout_analyzer import LayoutAnalyzer
from core.layout_types import plan_to_json
from core.ir_validator import ensure_valid, load_schema
from pathlib import Path

doc = IRBuilder().build(FigmaFile.from_dict("lay1440", raw_desktop_response))
plan = LayoutAnalyzer().analyze(doc)                      # default viewport = native width
ensure_valid(plan.to_dict(), load_schema(Path("schemas/layout-plan.schema.json")))
print(plan_to_json(plan))

# Analyze a specific viewport (used by the property tests across 320..1920):
narrow = LayoutAnalyzer().analyze(doc, viewport=375)
```

## Representative input → output

Input node (from `fixtures/figma/layout_desktop.json`, the `Header` frame):

```json
{
  "id": "d:2",
  "name": "Header",
  "type": "FRAME",
  "absoluteBoundingBox": { "x": 24, "y": 24, "width": 1392, "height": 40 },
  "layoutMode": "HORIZONTAL",
  "primaryAxisAlignItems": "MIN",
  "counterAxisAlignItems": "MIN",
  "itemSpacing": 0,
  "layoutSizingHorizontal": "FILL",
  "layoutSizingVertical": "FIXED"
}
```

Output `LayoutNodePlan` (abridged):

```json
{
  "node_id": "d:2",
  "name": "Header",
  "kind": "frame",
  "display": "flex",
  "direction": "row",
  "box": { "x": 24, "y": 24, "width": 1392, "height": 40 },
  "figma_box": { "x": 24, "y": 24, "width": 1392, "height": 40 },
  "bounds_delta": 0.0,
  "sizing": {
    "horizontal": { "mode": "fill", "explicit": false },
    "vertical": { "mode": "fixed", "value": 40, "explicit": true }
  },
  "spacing": { "padding": { "top": 0, "right": 0, "bottom": 0, "left": 0 }, "gap": 0.0 },
  "alignment": { "justify": "MIN", "align": "MIN" },
  "overflow": { "x": "visible", "y": "visible", "wrap": "nowrap" },
  "confidence": 1.0,
  "children": [
    {
      "node_id": "d:3",
      "name": "Brand",
      "kind": "text",
      "display": "none",
      "box": { "x": 24, "y": 24, "width": 44.0, "height": 24 },
      "sizing": {
        "horizontal": { "mode": "hug", "measured": 44.0 },
        "vertical": { "mode": "hug", "measured": 24.0 }
      },
      "text": {
        "characters": "Brand", "font_size": 16,
        "measured_width": 44.0, "measured_height": 24.0,
        "wrapped": false, "approximate": true
      },
      "confidence": 0.7
    }
  ]
}
```

Notes on the example:

- `layoutSizingHorizontal: FILL` → `horizontal.mode: "fill"`, width = content box
  (1392 = 1440 − 2×24 padding).
- Text is `AUTO` (hug): width 5 chars × 16 × 0.55 = 44.0 — flagged
  `approximate`, confidence 0.7.
- Predicted bounds reproduce the Figma bounds (`bounds_delta: 0.0`) at the
  design's native width.

### Breakpoint example (from `layout_tablet.json`)

A growing card (`t:6`, `layoutGrow: 1`) shares free space, so its width changes
across the ladder. This is a **measured** change, with evidence:

```json
{
  "breakpoint": "md",
  "width": 1024,
  "node_id": "t:6",
  "property": "width",
  "before": "288",
  "after": "480",
  "evidence": "measured layout changes between widths 640 and 1024"
}
```

The `Brand` text (`t:3`) does not change at any width and is recorded under
`no_change` — no behavior is silently invented.

## Contradiction / underdetermined / unsupported report

Every case is surfaced in the plan — nothing is dropped:

- **contradictions** — `min_width > max_width`, fixed size outside its own
  min/max, negative min/max. Severity `error`, confidence `0.0`.
- **underdetermined** — hug with no measurable content, `FILL` sizing with no
  declared width, percent/fill in a hug container, absolute node with no box,
  min, or STRETCH anchor. Severity `warning`, `box: null`.
- **unsupported** — native `overflow: scroll` (no IR representation),
  non-string `layoutAlign`/`layoutWrap` payloads, heuristic text metrics.
  Severity `info`.

For the shipped fixtures, the report is:

| Fixture | Cases surfaced |
|---------|----------------|
| `layout_desktop` | text metrics approximate (info) |
| `layout_tablet` | text metrics approximate (info) |
| `layout_mobile` | text metrics approximate + wrapped text (info) |
| `layout_nested` | text metrics approximate (info) |
| `layout_content_overflow` | `o:4` contradiction (`min_width 120 > max_width 80`), clipped content |

## Snapshot workflow

```
# initial / intended change:
REWRITE_SNAPSHOTS=1 python3 plugin/figmaforge/tests/test_layout_snapshot.py
# verification (CI / normal):
python3 plugin/figmaforge/tests/test_layout_snapshot.py
```

A mismatch means either the fixture changed or the layout-plan shape changed —
review the diff before regenerating.
