# FigmaForge Design Intermediate Representation (IR)

**Status:** Implemented (Part 3). No code generation yet.

The Design IR is the normalized, **framework-neutral** view of a Figma file
produced from the Part-2 ingestion layer. It is the seam between Figma's raw
REST payloads and any future code generator (React, CSS, SwiftUI, …) — nothing
here renders or emits framework code.

```
Figma REST (raw JSON)
        │  core/figma_client.py   (Part 2, transport)
        ▼
core/figma_types.py  (typed ingestion models: FigmaFile / Node)
        │  core/ir_builder.py     (Part 3, pure normalization)
        ▼
core/ir_types.py     (typed Design IR: IRDocument / IRNode)
        │  ir_to_dict / ir_to_json + core/ir_validator.py + schemas/design-ir.schema.json
        ▼
JSON serialization, schema-validated snapshots
```

## Design principles

- **Stdlib only.** No external libraries, no agent frameworks (no ADK,
  LangGraph, CrewAI). Matches `CLAUDE.md`.
- **Everything preserved.** Original Figma node ids are kept verbatim; the
  parent-child tree is kept; each node carries its `raw` Figma dict for
  debugging; properties the normalizer does not map are kept under `unknown`
  (never silently dropped).
- **Semantic vs raw separated.** Each node exposes normalized, typed fields
  (e.g. `layout.mode == "auto"`, `style.fills[0].color` as `IRColor`) while the
  original payload stays available in `raw`.
- **Consistent internal types.** Colors are always `IRColor` (r/g/b/a in 0..1),
  dimensions are `float`, spacing is `IRSpacing`, tokens are `IRToken`.
- **Deterministic + validated.** Serialization is stable (`sort_keys=True`),
  covered by snapshot tests, and checked against a JSON schema.

## The 15 modeled areas

| # | Area | IR type / field |
|---|------|-----------------|
| 1 | Documents and pages | `IRDocument`, `IRNode.kind` `document`/`page` |
| 2 | Frames and sections | `IRNode.kind` `frame`/`group`/`section` |
| 3 | Text nodes | `IRNode.kind` `text` + `IRTextContent` |
| 4 | Components and instances | `IRComponent`, `IRInstance` |
| 5 | Auto-layout | `IRLayout.mode == "auto"` |
| 6 | Flex/grid/absolute positioning | `IRLayout` (wrap/grow/shrink, `grid_columns`), `IRPosition` |
| 7 | Width/height/min/max | `IRDimensions` |
| 8 | Padding/gaps/alignment/spacing | `IRSpacing` + `IRLayout.justify/align/gap` |
| 9 | Fills/borders/shadows/opacity/radius | `IRStyle` + `IRFill`/`IRBorder`/`IRShadow`/`IRBlur` |
| 10 | Typography and text styles | `IRTypography` |
| 11 | Variables and design tokens | `IRToken`, `IRTokenRef`, `IRTokens` |
| 12 | Assets and image references | `IRAssetRef` + `IRDocument.assets` |
| 13 | Responsive constraints | `IResponsive` |
| 14 | Prototype links and interactions | `IRPrototype`, `IRInteraction`, `IRLink` |
| 15 | Annotations and developer metadata | `IRAnnotations` |

## Modules

- `core/ir_types.py` — typed models + `to_dict()` + `ir_to_json()`.
- `core/ir_builder.py` — `IRBuilder.build(FigmaFile) -> IRDocument`;
  `IRBuilder.unsupported_properties()` reports unmapped raw keys per node.
- `core/ir_validator.py` — stdlib-only JSON-Schema (draft-07 subset)
  validator: `validate_ir()`, `ensure_valid()`.
- `schemas/design-ir.schema.json` — the IR schema.
- `tests/test_ir.py` — fixture tests (all 15 areas + validation).
- `tests/test_ir_snapshot.py` — snapshot of the normalized fixture.
- `tests/snapshots/file.json` — checked-in normalized output.

## Usage

```python
from core.figma_types import FigmaFile
from core.ir_builder import IRBuilder
from core.ir_types import ir_to_json
from core.ir_validator import ensure_valid

figma_file = FigmaFile.from_dict("abc123", raw_file_response)
builder = IRBuilder(images=images_map)               # images_map from /v1/images
document = builder.build(figma_file)

ensure_valid(document.to_dict())                     # raises IRValidationError on failure
json_text = ir_to_json(document)                     # deterministic, snapshot-stable
report = builder.unsupported_properties()            # {node_id: [unmapped keys, ...]}
```

## Example — raw Figma node → normalized IR

Raw `Button Card` frame (from `fixtures/figma/file.json`, `children` elided
with `…` for readability):

```json
{
  "id": "2:3",
  "name": "Button Card",
  "type": "FRAME",
  "visible": true,
  "opacity": 1.0,
  "absoluteBoundingBox": { "x": 0, "y": 60, "width": 320, "height": 200 },
  "constraints": { "vertical": "TOP", "horizontal": "LEFT" },
  "backgroundColor": { "r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0 },
  "fills": [ { "type": "SOLID", "color": { "r": 0.08, "g": 0.12, "b": 0.24, "a": 1.0 }, "blendMode": "NORMAL" } ],
  "strokes": [ { "type": "SOLID", "color": { "r": 0.9, "g": 0.92, "b": 0.95, "a": 1.0 } } ],
  "effects": [ { "type": "DROP_SHADOW", "radius": 8, "spread": 0, "color": { "r": 0, "g": 0, "b": 0, "a": 0.25 }, "offset": { "x": 0, "y": 4 } } ],
  "layoutMode": "VERTICAL",
  "layoutSizingHorizontal": "FIXED",
  "layoutSizingVertical": "AUTO",
  "primaryAxisAlignItems": "CENTER",
  "counterAxisAlignItems": "CENTER",
  "primaryAxisSizingMode": "FIXED",
  "counterAxisSizingMode": "AUTO",
  "paddingTop": 16, "paddingRight": 16, "paddingBottom": 16, "paddingLeft": 16,
  "itemSpacing": 8,
  "boundVariables": { "paddingLeft": { "type": "VARIABLE_ALIAS", "id": "1:33" } },
  "children": [ … "3:4" (text), "4:5" (group) … ]
}
```

Normalized IR (as produced by `IRBuilder`):

```json
{
  "id": "2:3",
  "name": "Button Card",
  "kind": "frame",
  "node_type": "FRAME",
  "source": { "file_key": "abc123", "node_id": "2:3", "node_type": "FRAME", "path": ["0:0", "0:1"] },
  "layout": {
    "mode": "auto",
    "direction": "column",
    "justify": "CENTER",
    "align": "CENTER",
    "padding": { "top": 16.0, "right": 16.0, "bottom": 16.0, "left": 16.0 },
    "gap": 8.0,
    "sizing_primary": "FIXED",
    "sizing_counter": "AUTO"
  },
  "dimensions": {
    "width": 320.0,
    "height": 200.0,
    "sizing_horizontal": "FIXED",
    "sizing_vertical": "AUTO"
  },
  "style": {
    "fills": [ { "kind": "solid", "color": { "r": 0.08, "g": 0.12, "b": 0.24, "a": 1.0 }, "opacity": 1.0, "visible": true, "blend_mode": "NORMAL", "gradient_stops": [] } ],
    "borders": [ { "color": { "r": 0.9, "g": 0.92, "b": 0.95, "a": 1.0 }, "visible": true } ],
    "shadows": [ { "kind": "drop", "color": { "r": 0.0, "g": 0.0, "b": 0.0, "a": 0.25 }, "x": 0.0, "y": 4.0, "blur": 8.0, "spread": 0.0, "visible": true } ],
    "blurs": [],
    "opacity": 1.0
  },
  "responsive": { "constraints_horizontal": "LEFT", "constraints_vertical": "TOP", "sizing_horizontal": "FIXED", "sizing_vertical": "AUTO" },
  "tokens": { "refs": [ { "property_name": "paddingLeft", "token_key": "1:33", "kind": "variable" } ], "bound_variables": { "paddingLeft": "1:33" }, "style_refs": {} },
  "asset": { "node_id": "2:3", "url": "https://s3-alpha.figma.com/assets/card.svg" },
  "children": ["3:4", "4:5"],
  "unknown": { "backgroundColor": { "r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0 } },
  "raw": { … complete original Figma node dict, for debugging … }
}
```

Notes on the example:

- `layoutMode: VERTICAL` → `layout.mode: "auto"`, `layout.direction: "column"`.
- `primaryAxisAlignItems`/`counterAxisAlignItems` → `layout.justify`/`align`.
- The drop shadow effect → `style.shadows[0]` (kind, color, offset, blur, spread).
- `boundVariables.paddingLeft` → `tokens.bound_variables` + an `IRTokenRef`.
- The asset URL comes from `images.json` (the `/v1/images` response), not the
  file payload — it is attached by `IRBuilder(images=…)`.
- `backgroundColor` is the **legacy** Figma property that the typed ingestion
  layer does not model. It is *not dropped*: it appears in `unknown` and is
  reported by `unsupported_properties()`. `fills` is the canonical source and is
  fully modeled.

## Accessibility diagnostics

`core.accessibility.analyze_document()` produces a separate deterministic
report rather than mixing usability findings into backend fidelity losses. It
currently checks accessible names for interactive-looking nodes and WCAG AA
text contrast when foreground and parent background colors are available.
`pipeline.py generate` includes this report under `accessibility_report` in
every backend manifest. Findings are node-level and do not block generation;
the caller decides whether errors require remediation.

## Unsupported Figma properties

`IRBuilder.unsupported_properties()` returns `{node_id: [property_key, …]}` for
every raw key that has no typed IR mapping. These are **preserved** in
`IRNode.unknown`, never discarded.

For the current fixtures the report is:

```json
{ "2:3": ["backgroundColor"] }
```

Known Figma properties that may surface this way in real files (and are
preserved but not modeled yet): `backgroundColor`/`background` (legacy fill
fields), `exportSettings`, `strokeCap`/`strokeJoin`/`strokeMiterAngle`,
`textTruncation`, `layoutPositioning`, `prototypeStartNode` (file level),
`devStatus` (dev-mode metadata is captured under `annotations.developer_metadata`
instead). Extending the IR for any of these is a localized change: add a typed
field, map it in `ir_builder`, extend the schema, update the snapshot.

## Snapshot workflow

```
# initial / intended change:
REWRITE_SNAPSHOTS=1 python3 plugin/figmaforge/tests/test_ir_snapshot.py

# verification (CI / normal):
python3 plugin/figmaforge/tests/test_ir_snapshot.py
```

A mismatch means either the fixture changed or the IR shape changed — review the
diff before regenerating.
