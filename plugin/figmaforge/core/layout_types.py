"""
Typed, framework-neutral layout plan model (Part 5).

Consumed by the layout analyzer: the layout engine, constraint model, and
breakpoint model together produce a single :class:`LayoutPlan` describing how a
Design IR should lay out at a given viewport. Following FigmaForge conventions:

- Standard library only; no external dependencies.
- Every value is framework-neutral vocabulary — nothing here emits React/CSS
  (or any framework) output. A future generator would consume this plan.
- ``to_dict()`` on every object is JSON-serializable; snapshots are stable
  (``sort_keys=True`` in the serializer).
- ``None`` values are dropped on serialization (falsy-but-present values such
  as ``0`` and ``False`` are kept), matching ``ir_types`` ``_compact``.
- Everything ambiguous or unresolved is reported explicitly (``diagnostics``,
  ``ConstraintIssue``, per-node ``confidence``), never silently guessed.

Layout vocabulary (framework-neutral):

- ``display``: ``flex`` | ``grid`` | ``absolute`` | ``none``
- sizing ``mode`` per axis: ``fixed`` | ``fill`` | ``hug`` | ``percent``
- anchors: ``min`` (left/top) | ``center`` | ``max`` (right/bottom) |
  ``stretch`` | ``scale``
- overflow per axis: ``visible`` | ``clip`` | ``scroll``
"""  # noqa: E501

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Bump when the serialized layout-plan shape changes incompatibly.
LAYOUT_PLAN_VERSION = 1

# Display modes
DISPLAY_FLEX = "flex"
DISPLAY_GRID = "grid"
DISPLAY_ABSOLUTE = "absolute"
DISPLAY_NONE = "none"

# Sizing modes (per axis)
SIZING_FIXED = "fixed"
SIZING_FILL = "fill"
SIZING_HUG = "hug"
SIZING_PERCENT = "percent"

# Anchoring vocabulary (derived from Figma constraints_h/v)
ANCHOR_MIN = "min"
ANCHOR_CENTER = "center"
ANCHOR_MAX = "max"
ANCHOR_STRETCH = "stretch"
ANCHOR_SCALE = "scale"

# Overflow behavior (per axis)
OVERFLOW_VISIBLE = "visible"
OVERFLOW_CLIP = "clip"
OVERFLOW_SCROLL = "scroll"

# Text wrap
WRAP_NONE = "nowrap"
WRAP_WRAP = "wrap"

# Diagnostics severities
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"

# Constraint issue kinds
ISSUE_CONTRADICTION = "contradiction"
ISSUE_UNDERDETERMINED = "underdetermined"
ISSUE_UNSUPPORTED = "unsupported"

# Border tolerance for treating a predicted box as reproducing Figma bounds.
BOUNDS_EPSILON = 1e-4

# Overall confidence bands (0..1)
CONFIDENCE_HIGH = 0.75
CONFIDENCE_MEDIUM = 0.45
CONFIDENCE_LOW = 0.0


def _round(value: Optional[float], places: int = 4) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return None


def _compact(data: Dict[str, Any]) -> Dict[str, Any]:
    """Drop ``None`` values while keeping falsy-but-present ones (0, "", False)."""
    return {k: v for k, v in data.items() if v is not None}


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass
class Box:
    """Predicted (or recorded Figma) layout bounds."""

    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x": _round(self.x) or 0.0,
            "y": _round(self.y) or 0.0,
            "width": _round(self.width) or 0.0,
            "height": _round(self.height) or 0.0,
        }


@dataclass
class AxisSizing:
    """Sizing model for one axis (horizontal/vertical)."""

    mode: str = SIZING_FIXED
    value: Optional[float] = None  # fixed value / percent ratio / measured hug
    min: Optional[float] = None
    max: Optional[float] = None
    measured: Optional[float] = None  # content-driven size (hug), if measurable
    explicit: bool = True  # grounding (True) vs derived (False)


@dataclass
class SizingSpec:
    """Per-axis sizing for a node."""

    horizontal: Optional[AxisSizing] = None
    vertical: Optional[AxisSizing] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "horizontal": _axis_to_dict(self.horizontal),
            "vertical": _axis_to_dict(self.vertical),
        })


def _axis_to_dict(axis: Optional[AxisSizing]) -> Optional[Dict[str, Any]]:
    if axis is None:
        return None
    return _compact({
        "mode": axis.mode,
        "value": _round(axis.value),
        "min": _round(axis.min),
        "max": _round(axis.max),
        "measured": _round(axis.measured),
        "explicit": axis.explicit,
    })


@dataclass
class EdgeOffsets:
    """Four-edge offsets used for padding and inferred margins."""

    top: Optional[float] = None
    right: Optional[float] = None
    bottom: Optional[float] = None
    left: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "top": _round(self.top),
            "right": _round(self.right),
            "bottom": _round(self.bottom),
            "left": _round(self.left),
        })


@dataclass
class SpacingSpec:
    """Padding, margin, and gap model for a node."""

    padding: Optional[EdgeOffsets] = None
    margin: Optional[EdgeOffsets] = None
    margin_source: Optional[str] = None  # where margin was inferred from
    gap: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "padding": self.padding.to_dict() if self.padding else None,
            "margin": self.margin.to_dict() if self.margin else None,
            "margin_source": self.margin_source,
            "gap": _round(self.gap),
        })


@dataclass
class AlignmentSpec:
    """Main/cross axis alignment (Figma -> framework-neutral vocabulary)."""

    justify: Optional[str] = None  # main-axis alignment
    align: Optional[str] = None  # cross-axis alignment
    align_self: Optional[str] = None  # per-item override

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "justify": self.justify,
            "align": self.align,
            "align_self": self.align_self,
        })


@dataclass
class Anchoring:
    """Anchor pair + offsets for absolute positioning."""

    horizontal: Optional[str] = None
    vertical: Optional[str] = None
    left: Optional[float] = None
    right: Optional[float] = None
    top: Optional[float] = None
    bottom: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "horizontal": self.horizontal,
            "vertical": self.vertical,
            "left": _round(self.left),
            "right": _round(self.right),
            "top": _round(self.top),
            "bottom": _round(self.bottom),
        })


@dataclass
class OverflowSpec:
    """Overflow/clipping/scroll behavior per axis + wrap."""

    x: str = OVERFLOW_VISIBLE
    y: str = OVERFLOW_VISIBLE
    wrap: Optional[str] = None  # nowrap | wrap
    clipped_content: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "x": self.x,
            "y": self.y,
            "wrap": self.wrap,
            "clipped_content": self.clipped_content,
        })


@dataclass
class TextModel:
    """Content-driven text model (wrap + measured size)."""

    characters: str = ""
    font_size: Optional[float] = None
    measured_width: Optional[float] = None
    measured_height: Optional[float] = None
    wrapped: bool = False
    lines: List[str] = field(default_factory=list)
    approximate: bool = False  # True when measured via the width heuristic

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "characters": self.characters,
            "font_size": _round(self.font_size),
            "measured_width": _round(self.measured_width),
            "measured_height": _round(self.measured_height),
            "wrapped": self.wrapped,
            "lines": list(self.lines),
            "approximate": self.approximate,
        })


@dataclass
class ConfidenceDecision:
    """A recorded confidence-weighted inference decision."""

    decision: str = ""
    score: float = 1.0
    reason: str = ""
    evidence: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "decision": self.decision,
            "score": self.score,
            "reason": self.reason,
            "evidence": self.evidence,
        })


@dataclass
class Diagnostic:
    """Machine-readable diagnostic: severity / code / message / node."""

    severity: str = SEVERITY_INFO
    code: str = ""
    message: str = ""
    node_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "node_id": self.node_id,
        })


@dataclass
class ConstraintIssue:
    """A detected constraint problem (contradiction / underdetermined / unsupported)."""

    kind: str = ISSUE_UNDERDETERMINED
    axis: Optional[str] = None  # "horizontal" | "vertical" | None
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "kind": self.kind,
            "axis": self.axis,
            "message": self.message,
        })


@dataclass
class ConstraintReport:
    """The constraint model's verdict for a node: counts + issues."""

    total: int = 0
    grounding: int = 0  # explicit (source of truth) constraints
    derived: int = 0  # inferred constraints
    contradictions: List[ConstraintIssue] = field(default_factory=list)
    underdetermined: List[ConstraintIssue] = field(default_factory=list)
    unsupported: List[ConstraintIssue] = field(default_factory=list)

    @property
    def issues(self) -> List[ConstraintIssue]:
        return list(self.contradictions + self.underdetermined + self.unsupported)

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "total": self.total,
            "grounding": self.grounding,
            "derived": self.derived,
            "contradictions": [i.to_dict() for i in self.contradictions],
            "underdetermined": [i.to_dict() for i in self.underdetermined],
            "unsupported": [i.to_dict() for i in self.unsupported],
        })


# ---------------------------------------------------------------------------
# Breakpoints
# ---------------------------------------------------------------------------


@dataclass
class BreakpointChange:
    """A responsive change this node exhibits at a specific breakpoint."""

    breakpoint: str = ""
    width: float = 0.0
    node_id: Optional[str] = None
    property: str = ""
    before: Optional[str] = None
    after: Optional[str] = None
    evidence: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "breakpoint": self.breakpoint,
            "width": _round(self.width) or 0.0,
            "node_id": self.node_id,
            "property": self.property,
            "before": self.before,
            "after": self.after,
            "evidence": self.evidence,
        })


@dataclass
class BreakpointPlan:
    """Document-level breakpoint summary."""

    breakpoints: List[Dict[str, Any]] = field(default_factory=list)  # [{name, width}]
    changes: List[BreakpointChange] = field(default_factory=list)
    no_change: List[str] = field(default_factory=list)  # node ids explicitly unchanged

    @property
    def counts(self) -> Dict[str, int]:
        return {
            "breakpoints": len(self.breakpoints),
            "changes": len(self.changes),
            "no_change": len(self.no_change),
        }

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "breakpoints": list(self.breakpoints),
            "changes": [c.to_dict() for c in self.changes],
            "no_change": list(self.no_change),
            "counts": self.counts,
        })


# ---------------------------------------------------------------------------
# Node + plan
# ---------------------------------------------------------------------------


@dataclass
class LayoutNodePlan:
    """The layout plan for a single IR node."""

    node_id: str = ""
    name: str = ""
    kind: str = ""  # frame | group | text | shape | ...
    display: str = DISPLAY_NONE
    direction: Optional[str] = None  # row | column (flex) / None
    order: int = 0  # document order within parent
    box: Optional[Box] = None  # predicted bounds (None => unsolved)
    figma_box: Optional[Box] = None  # recorded Figma bounds
    bounds_delta: Optional[float] = None  # max(|dx|, |dy|) at native viewport
    sizing: Optional[SizingSpec] = None
    spacing: Optional[SpacingSpec] = None
    alignment: Optional[AlignmentSpec] = None
    anchors: Optional[Anchoring] = None
    text: Optional[TextModel] = None
    overflow: Optional[OverflowSpec] = None
    breakpoints: List[BreakpointChange] = field(default_factory=list)
    confidence: float = 1.0
    assumptions: List[str] = field(default_factory=list)  # things assumed/inferred
    constraints: Optional[ConstraintReport] = None
    diagnostics: List[Diagnostic] = field(default_factory=list)
    children: List["LayoutNodePlan"] = field(default_factory=list)

    def descendant(self, node_id: str) -> Optional["LayoutNodePlan"]:
        """Find a descendant plan by node id (pre-order)."""
        if self.node_id == node_id:
            return self
        for child in self.children:
            hit = child.descendant(node_id)
            if hit is not None:
                return hit
        return None

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "node_id": self.node_id,
            "name": self.name,
            "kind": self.kind,
            "display": self.display,
            "direction": self.direction,
            "order": self.order,
            "box": self.box.to_dict() if self.box else None,
            "figma_box": self.figma_box.to_dict() if self.figma_box else None,
            "bounds_delta": _round(self.bounds_delta),
            "sizing": self.sizing.to_dict() if self.sizing else None,
            "spacing": self.spacing.to_dict() if self.spacing else None,
            "alignment": self.alignment.to_dict() if self.alignment else None,
            "anchors": self.anchors.to_dict() if self.anchors else None,
            "text": self.text.to_dict() if self.text else None,
            "overflow": self.overflow.to_dict() if self.overflow else None,
            "breakpoints": [b.to_dict() for b in self.breakpoints],
            "confidence": self.confidence,
            "assumptions": list(self.assumptions),
            "constraints": self.constraints.to_dict() if self.constraints else None,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "children": [c.to_dict() for c in self.children],
        })


@dataclass
class LayoutPlan:
    """The full result of analyzing a Design IR's layout."""

    schema_version: int = LAYOUT_PLAN_VERSION
    file_key: str = ""
    viewport: float = 0.0  # viewport this plan was solved at
    base_width: float = 0.0  # the design's native width (1x scale)
    source: Optional[Dict[str, Any]] = None  # IR source metadata snapshot
    screens: List[LayoutNodePlan] = field(default_factory=list)  # top-level pages
    breakpoints: Optional[BreakpointPlan] = None
    constraints: Optional[ConstraintReport] = None
    counts: Dict[str, int] = field(default_factory=dict)
    confidence: Dict[str, Any] = field(default_factory=dict)  # aggregate metrics
    diagnostics: List[Diagnostic] = field(default_factory=list)

    def nodes(self) -> List[LayoutNodePlan]:
        out: List[LayoutNodePlan] = []
        for screen in self.screens:
            out.extend(screen.walk())
        return out

    def node(self, node_id: str) -> Optional[LayoutNodePlan]:
        for screen in self.screens:
            hit = screen.descendant(node_id)
            if hit is not None:
                return hit
        return None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "schema_version": self.schema_version,
            "file_key": self.file_key,
            "viewport": _round(self.viewport) or 0.0,
            "base_width": _round(self.base_width) or 0.0,
            "source": self.source,
            "screens": [s.to_dict() for s in self.screens],
            "breakpoints": self.breakpoints.to_dict() if self.breakpoints else None,
            "constraints": self.constraints.to_dict() if self.constraints else None,
            "counts": dict(self.counts),
            "confidence": dict(self.confidence),
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        })

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LayoutPlan":
        """Rebuild a :class:`LayoutPlan` from its ``to_dict`` JSON shape.

        Round-trip contract: ``from_dict(x.to_dict()).to_dict() == x.to_dict()``
        exactly (floats are already rounded at serialization time). Absent
        keys stay absent; the ``source`` snapshot is preserved verbatim when
        present.
        """
        source = data.get("source")
        return cls(
            schema_version=int(data.get("schema_version", LAYOUT_PLAN_VERSION)),
            file_key=str(data.get("file_key", "") or ""),
            viewport=float(data.get("viewport", 0.0) or 0.0),
            base_width=float(data.get("base_width", 0.0) or 0.0),
            source=dict(source) if isinstance(source, dict) else None,
            screens=[s for s in (_node_plan_from_dict(screen) for screen in data.get("screens", []) or []) if s is not None],
            breakpoints=_breakpoint_plan_from_dict(data.get("breakpoints")),
            constraints=_constraint_report_from_dict(data.get("constraints")),
            counts=dict(data.get("counts", {}) or {}),
            confidence=dict(data.get("confidence", {}) or {}),
            diagnostics=[_diagnostic_from_dict(d) for d in data.get("diagnostics", []) or []],
        )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def plan_to_dict(plan: LayoutPlan) -> Dict[str, Any]:
    """Serialize a :class:`LayoutPlan` to a plain JSON-safe dict."""
    return plan.to_dict()


def plan_to_json(plan: LayoutPlan, indent: int = 2) -> str:
    """Serialize a :class:`LayoutPlan` to a deterministic JSON string.

    ``sort_keys=True`` keeps key ordering stable so snapshot tests are
    reproducible across Python versions.
    """
    return json.dumps(plan.to_dict(), indent=indent, sort_keys=True)


# ---------------------------------------------------------------------------
# Loaders (from_dict) — JSON round-trip (Part 16)
# ---------------------------------------------------------------------------
# Each loader reproduces exactly what the matching ``to_dict`` emits: absent
# keys stay absent (helpers return None for non-dict input), so
# ``from_dict(x.to_dict()).to_dict() == x.to_dict()`` holds exactly.


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _box_from_dict(data: Any) -> Optional[Box]:
    if not isinstance(data, dict):
        return None
    return Box(
        x=float(data.get("x", 0.0) or 0.0),
        y=float(data.get("y", 0.0) or 0.0),
        width=float(data.get("width", 0.0) or 0.0),
        height=float(data.get("height", 0.0) or 0.0),
    )


def _axis_from_dict(data: Any) -> Optional[AxisSizing]:
    if not isinstance(data, dict):
        return None
    return AxisSizing(
        mode=data.get("mode", SIZING_FIXED),
        value=_float_or_none(data.get("value")),
        min=_float_or_none(data.get("min")),
        max=_float_or_none(data.get("max")),
        measured=_float_or_none(data.get("measured")),
        explicit=bool(data.get("explicit", True)),
    )


def _sizing_from_dict(data: Any) -> Optional[SizingSpec]:
    if not isinstance(data, dict):
        return None
    return SizingSpec(
        horizontal=_axis_from_dict(data.get("horizontal")),
        vertical=_axis_from_dict(data.get("vertical")),
    )


def _edges_from_dict(data: Any) -> Optional[EdgeOffsets]:
    if not isinstance(data, dict):
        return None
    return EdgeOffsets(
        top=_float_or_none(data.get("top")),
        right=_float_or_none(data.get("right")),
        bottom=_float_or_none(data.get("bottom")),
        left=_float_or_none(data.get("left")),
    )


def _spacing_from_dict(data: Any) -> Optional[SpacingSpec]:
    if not isinstance(data, dict):
        return None
    return SpacingSpec(
        padding=_edges_from_dict(data.get("padding")),
        margin=_edges_from_dict(data.get("margin")),
        margin_source=data.get("margin_source"),
        gap=_float_or_none(data.get("gap")),
    )


def _alignment_from_dict(data: Any) -> Optional[AlignmentSpec]:
    if not isinstance(data, dict):
        return None
    return AlignmentSpec(
        justify=data.get("justify"),
        align=data.get("align"),
        align_self=data.get("align_self"),
    )


def _anchoring_from_dict(data: Any) -> Optional[Anchoring]:
    if not isinstance(data, dict):
        return None
    return Anchoring(
        horizontal=data.get("horizontal"),
        vertical=data.get("vertical"),
        left=_float_or_none(data.get("left")),
        right=_float_or_none(data.get("right")),
        top=_float_or_none(data.get("top")),
        bottom=_float_or_none(data.get("bottom")),
    )


def _overflow_from_dict(data: Any) -> Optional[OverflowSpec]:
    if not isinstance(data, dict):
        return None
    return OverflowSpec(
        x=data.get("x", OVERFLOW_VISIBLE),
        y=data.get("y", OVERFLOW_VISIBLE),
        wrap=data.get("wrap"),
        clipped_content=data.get("clipped_content"),
    )


def _text_model_from_dict(data: Any) -> Optional[TextModel]:
    if not isinstance(data, dict):
        return None
    return TextModel(
        characters=str(data.get("characters", "") or ""),
        font_size=_float_or_none(data.get("font_size")),
        measured_width=_float_or_none(data.get("measured_width")),
        measured_height=_float_or_none(data.get("measured_height")),
        wrapped=bool(data.get("wrapped", False)),
        lines=list(data.get("lines", []) or []),
        approximate=bool(data.get("approximate", False)),
    )


def _diagnostic_from_dict(data: Any) -> Diagnostic:
    if not isinstance(data, dict):
        return Diagnostic()
    return Diagnostic(
        severity=data.get("severity", SEVERITY_INFO),
        code=str(data.get("code", "") or ""),
        message=str(data.get("message", "") or ""),
        node_id=data.get("node_id"),
    )


def _constraint_issue_from_dict(data: Any) -> ConstraintIssue:
    if not isinstance(data, dict):
        return ConstraintIssue()
    return ConstraintIssue(
        kind=data.get("kind", ISSUE_UNDERDETERMINED),
        axis=data.get("axis"),
        message=str(data.get("message", "") or ""),
    )


def _constraint_report_from_dict(data: Any) -> Optional[ConstraintReport]:
    if not isinstance(data, dict):
        return None
    return ConstraintReport(
        total=int(data.get("total", 0)),
        grounding=int(data.get("grounding", 0)),
        derived=int(data.get("derived", 0)),
        contradictions=[_constraint_issue_from_dict(i) for i in data.get("contradictions", []) or []],
        underdetermined=[_constraint_issue_from_dict(i) for i in data.get("underdetermined", []) or []],
        unsupported=[_constraint_issue_from_dict(i) for i in data.get("unsupported", []) or []],
    )


def _breakpoint_change_from_dict(data: Any) -> BreakpointChange:
    if not isinstance(data, dict):
        return BreakpointChange()
    return BreakpointChange(
        breakpoint=str(data.get("breakpoint", "") or ""),
        width=float(data.get("width", 0.0) or 0.0),
        node_id=data.get("node_id"),
        property=str(data.get("property", "") or ""),
        before=data.get("before"),
        after=data.get("after"),
        evidence=str(data.get("evidence", "") or ""),
    )


def _breakpoint_plan_from_dict(data: Any) -> Optional[BreakpointPlan]:
    if not isinstance(data, dict):
        return None
    return BreakpointPlan(
        breakpoints=[dict(b) for b in data.get("breakpoints", []) or [] if isinstance(b, dict)],
        changes=[_breakpoint_change_from_dict(c) for c in data.get("changes", []) or []],
        no_change=list(data.get("no_change", []) or []),
    )


def _node_plan_from_dict(data: Any) -> Optional[LayoutNodePlan]:
    """Rebuild a :class:`LayoutNodePlan` subtree (children recursively)."""
    if not isinstance(data, dict):
        return None
    return LayoutNodePlan(
        node_id=str(data.get("node_id", "") or ""),
        name=str(data.get("name", "") or ""),
        kind=str(data.get("kind", "") or ""),
        display=str(data.get("display", DISPLAY_NONE) or DISPLAY_NONE),
        direction=data.get("direction"),
        order=int(data.get("order", 0)),
        box=_box_from_dict(data.get("box")),
        figma_box=_box_from_dict(data.get("figma_box")),
        bounds_delta=_float_or_none(data.get("bounds_delta")),
        sizing=_sizing_from_dict(data.get("sizing")),
        spacing=_spacing_from_dict(data.get("spacing")),
        alignment=_alignment_from_dict(data.get("alignment")),
        anchors=_anchoring_from_dict(data.get("anchors")),
        text=_text_model_from_dict(data.get("text")),
        overflow=_overflow_from_dict(data.get("overflow")),
        breakpoints=[_breakpoint_change_from_dict(b) for b in data.get("breakpoints", []) or []],
        confidence=float(data.get("confidence", 1.0)),
        assumptions=list(data.get("assumptions", []) or []),
        constraints=_constraint_report_from_dict(data.get("constraints")),
        diagnostics=[_diagnostic_from_dict(d) for d in data.get("diagnostics", []) or []],
        children=[c for c in (_node_plan_from_dict(ch) for ch in data.get("children", []) or []) if c is not None],
    )