"""
Constraint model for the layout analyzer (Part 5).

Extracts a deterministic constraint model from the Design IR for each node and
detects the two failure classes this layer must *never* paper over:

- **Contradictions** — the design states conflicting constraints for the same
  bound (e.g. ``min_width > max_width``, or a fixed width outside its own
  min/max range). These are reported, never resolved by guessing.
- **Underdetermined** — too few constraints exist to solve a bound (e.g. ``hug``
  with no content and no min/max, ``percent``/``fill`` with no resolved
  container width). These are reported as ``ConstraintIssue``; the solver refuses
  to invent a number.

The module also provides pure arithmetic primitives (``BoxSolver``) used by the
layout engine: clamping, content-box math, and axis resolution. Following
FigmaForge conventions: stdlib only, deterministic, and every omission is
reported explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .ir_types import IResponsive, IRSpacing, IRNode
from .layout_types import (
    Box,
    ConstraintIssue,
    ConstraintReport,
    ISSUE_CONTRADICTION,
    ISSUE_UNDERDETERMINED,
    ISSUE_UNSUPPORTED,
)

AXIS_HORIZONTAL = "horizontal"
AXIS_VERTICAL = "vertical"

# Constraint kinds (values the model reasons about)
CONSTRAINT_WIDTH = "width"
CONSTRAINT_HEIGHT = "height"
CONSTRAINT_MIN = "min"
CONSTRAINT_MAX = "max"
CONSTRAINT_ANCHOR = "anchor"


@dataclass
class Constraint:
    """One extracted constraint on an axis."""

    kind: str = CONSTRAINT_WIDTH
    axis: str = AXIS_HORIZONTAL
    value: Optional[float] = None
    explicit: bool = True  # grounding (True) vs derived/inferred (False)
    source: str = ""  # which IR field it came from

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "axis": self.axis,
            "value": self.value,
            "explicit": self.explicit,
            "source": self.source,
        }


@dataclass
class AxisFacts:
    """All sizing facts for one axis, extracted from IR node fields."""

    axis: str
    width_value: Optional[float] = None  # explicit width/height
    sizing: Optional[str] = None  # FIXED | FILL | AUTO | None
    grow: Optional[float] = None  # layoutGrow (percent basis)
    shrink: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    constraints: Optional[str] = None  # responsive constraints MIN/CENTER/...
    position: Optional[float] = None  # absoluteBoundingBox offset on this axis
    left: Optional[float] = None  # offsets for anchoring (left/top)
    right: Optional[float] = None  # offsets for anchoring (right/bottom)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "axis": self.axis,
            "width_value": self.width_value,
            "sizing": self.sizing,
            "grow": self.grow,
            "shrink": self.shrink,
            "min": self.min,
            "max": self.max,
            "constraints": self.constraints,
            "position": self.position,
            "left": self.left,
            "right": self.right,
        }


def clamp(value: float, minimum: Optional[float], maximum: Optional[float]) -> float:
    """Clamp ``value`` into [minimum, maximum] (open bounds pass through)."""
    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


class ConstraintModel:
    """Build a :class:`ConstraintReport` for a single IR node."""

    def __init__(self, node: IRNode):
        self.node = node
        self.facts = self._extract_facts(node)

    # ------------------------------------------------------------------ API
    def report(self) -> ConstraintReport:
        """Extract constraints and detect contradictions / underdetermination."""
        constraints = self.extract_constraints()
        report = ConstraintReport(
            total=len(constraints),
            grounding=sum(1 for c in constraints if c.explicit),
            derived=sum(1 for c in constraints if not c.explicit),
        )
        report.contradictions = self.detect_contradictions()
        report.underdetermined = self.detect_underdetermined()
        report.unsupported = self.detect_unsupported()
        return report

    def extract_constraints(self) -> List[Constraint]:
        """Flatten every IR sizing constraint into a deterministic list."""
        resp: Optional[IResponsive] = self.node.responsive
        constraints: List[Constraint] = []

        h = self.facts[AXIS_HORIZONTAL]
        v = self.facts[AXIS_VERTICAL]

        def _add(axis_facts: AxisFacts, size_key: str, min_key: str, max_key: str) -> None:
            constraints.append(Constraint(
                kind=CONSTRAINT_WIDTH if axis_facts.axis == AXIS_HORIZONTAL else CONSTRAINT_HEIGHT,
                axis=axis_facts.axis,
                value=axis_facts.width_value,
                explicit=axis_facts.width_value is not None,
                source=size_key,
            ))
            if axis_facts.min is not None:
                constraints.append(Constraint(
                    kind=CONSTRAINT_MIN, axis=axis_facts.axis, value=axis_facts.min,
                    explicit=True, source=min_key,
                ))
            if axis_facts.max is not None:
                constraints.append(Constraint(
                    kind=CONSTRAINT_MAX, axis=axis_facts.axis, value=axis_facts.max,
                    explicit=True, source=max_key,
                ))

        # Horizontal
        _add(
            h,
            size_key="dimensions.width",
            min_key="dimensions.min_width",
            max_key="dimensions.max_width",
        )
        # Vertical
        _add(
            v,
            size_key="dimensions.height",
            min_key="dimensions.min_height",
            max_key="dimensions.max_height",
        )

        # Responsive sizing/constraints (anchoring evidence)
        for axis, resp_axis in (
            (AXIS_HORIZONTAL, resp.constraints_horizontal if resp else None),
            (AXIS_VERTICAL, resp.constraints_vertical if resp else None),
        ):
            if resp_axis:
                constraints.append(Constraint(
                    kind=CONSTRAINT_ANCHOR, axis=axis, value=None,
                    explicit=True, source=f"responsive.constraints_{axis[0]}",
                ))
        return constraints

    # ----------------------------------------------------------- detection
    def detect_contradictions(self) -> List[ConstraintIssue]:
        issues: List[ConstraintIssue] = []
        h = self.facts[AXIS_HORIZONTAL]
        v = self.facts[AXIS_VERTICAL]

        for axis_facts in (h, v):
            axis = axis_facts.axis
            if axis_facts.min is not None and axis_facts.max is not None \
                    and axis_facts.min > axis_facts.max:
                issues.append(ConstraintIssue(
                    kind=ISSUE_CONTRADICTION,
                    axis=axis,
                    message=(
                        f"{axis} min_width>max_width unresolved "
                        f"({axis_facts.min} > {axis_facts.max})"
                    ),
                ))
            if axis_facts.width_value is not None \
                    and axis_facts.min is not None \
                    and axis_facts.width_value < axis_facts.min:
                issues.append(ConstraintIssue(
                    kind=ISSUE_CONTRADICTION,
                    axis=axis,
                    message=(
                        f"{axis} fixed value {axis_facts.width_value} below min "
                        f"{axis_facts.min} (design is self-inconsistent)"
                    ),
                ))
            if axis_facts.width_value is not None \
                    and axis_facts.max is not None \
                    and axis_facts.width_value > axis_facts.max:
                issues.append(ConstraintIssue(
                    kind=ISSUE_CONTRADICTION,
                    axis=axis,
                    message=(
                        f"{axis} fixed value {axis_facts.width_value} above max "
                        f"{axis_facts.max} (design is self-inconsistent)"
                    ),
                ))
            if axis_facts.min is not None and axis_facts.min < 0:
                issues.append(ConstraintIssue(
                    kind=ISSUE_CONTRADICTION,
                    axis=axis,
                    message=f"{axis} min is negative ({axis_facts.min})",
                ))
            if axis_facts.max is not None and axis_facts.max < 0:
                issues.append(ConstraintIssue(
                    kind=ISSUE_CONTRADICTION,
                    axis=axis,
                    message=f"{axis} max is negative ({axis_facts.max})",
                ))
        return issues

    def detect_underdetermined(self) -> List[ConstraintIssue]:
        """Report bounds the *constraint set alone* cannot determine.

        Content-aware verdicts (hug with unmeasurable content, fills inside a
        hug container, percent without a resolved parent) are computed by the
        engine, which has the measured extents; this method reports only what is
        universally unresolvable from the node's own fields.
        """
        issues: List[ConstraintIssue] = []
        h = self.facts[AXIS_HORIZONTAL]
        v = self.facts[AXIS_VERTICAL]
        was_absolute = bool(self.node.position and self.node.position.mode == "absolute")

        for axis_facts in (h, v):
            axis = axis_facts.axis
            # A declared FIXED sizing mode with no value and no min is meaningless.
            if axis_facts.sizing == "FIXED" and axis_facts.width_value is None \
                    and axis_facts.min is None:
                issues.append(ConstraintIssue(
                    kind=ISSUE_UNDERDETERMINED,
                    axis=axis,
                    message=(
                        f"{axis} sizing is FIXED but no width/min is declared; "
                        "the bound cannot be solved"
                    ),
                ))
            # An absolutely-positioned node that cannot be sized or placed at all.
            if was_absolute and axis_facts.width_value is None \
                    and axis_facts.min is None \
                    and axis_facts.constraints not in ("STRETCH",):
                issues.append(ConstraintIssue(
                    kind=ISSUE_UNDERDETERMINED,
                    axis=axis,
                    message=(
                        f"{axis} absolute node has no explicit box, min, or "
                        "STRETCH anchor; it cannot be placed or sized"
                    ),
                ))
        return issues

    def detect_unsupported(self) -> List[ConstraintIssue]:
        issues: List[ConstraintIssue] = []
        # Constraints we do not model (they carry no numeric evidence).
        raw = getattr(self.node, "raw", {}) or {}
        for key in ("layoutAlign", "layoutWrap"):
            if key in raw and isinstance(raw.get(key), dict):
                issues.append(ConstraintIssue(
                    kind=ISSUE_UNSUPPORTED,
                    axis=None,
                    message=f"raw {key} has a non-string value; not modeled",
                ))
        return issues

    # ------------------------------------------------------------- helpers
    def _extract_facts(self, node: IRNode) -> Dict[str, AxisFacts]:
        dims = node.dimensions
        resp = node.responsive
        layout = node.layout
        position = node.position

        h_pos = position.x if position else None
        v_pos = position.y if position else None

        h_left = position.left if position else None
        h_right = position.right if position else None
        v_top = position.top if position else None
        v_bottom = position.bottom if position else None

        h_constraint = resp.constraints_horizontal if resp else None
        v_constraint = resp.constraints_vertical if resp else None

        h_grow = layout.grow if layout else None
        v_grow = None  # grow applies to main axis only (derived elsewhere)

        return {
            AXIS_HORIZONTAL: AxisFacts(
                axis=AXIS_HORIZONTAL,
                width_value=(dims.width if dims else None),
                sizing=(dims.sizing_horizontal if dims else None),
                grow=h_grow,
                shrink=(layout.shrink if layout else None),
                min=(dims.min_width if dims else None),
                max=(dims.max_width if dims else None),
                constraints=h_constraint,
                position=h_pos,
                left=h_left,
                right=h_right,
            ),
            AXIS_VERTICAL: AxisFacts(
                axis=AXIS_VERTICAL,
                width_value=(dims.height if dims else None),
                sizing=(dims.sizing_vertical if dims else None),
                grow=v_grow,
                shrink=None,
                min=(dims.min_height if dims else None),
                max=(dims.max_height if dims else None),
                constraints=v_constraint,
                position=v_pos,
                left=v_top,
                right=v_bottom,
            ),
        }


# ---------------------------------------------------------------------------
# Pure solving primitives
# ---------------------------------------------------------------------------


class BoxSolver:
    """Deterministic arithmetic for layout bounds.

    Pure functions only — no IR access, no state. The engine wires node facts
    through these.
    """

    @staticmethod
    def size_from_fixed(value: Optional[float], facts: AxisFacts) -> Optional[float]:
        if value is None:
            return None
        return clamp(value, facts.min, facts.max)

    @staticmethod
    def size_from_fill(parent_content: Optional[float], facts: AxisFacts) -> Optional[float]:
        if parent_content is None:
            return None
        return clamp(parent_content, facts.min, facts.max)

    @staticmethod
    def size_from_percent(
        parent_content: Optional[float],
        share: float,
        total_share: float,
        facts: AxisFacts,
    ) -> Optional[float]:
        if parent_content is None or total_share <= 0:
            return None
        return clamp(parent_content * share / total_share, facts.min, facts.max)

    @staticmethod
    def size_from_hug(
        measured: Optional[float],
        facts: AxisFacts,
    ) -> Optional[float]:
        if measured is None and facts.min is None:
            return None  # unresolvable: nothing to anchor the size to
        base = measured if measured is not None else facts.min or 0.0
        return clamp(base, facts.min, facts.max)

    @staticmethod
    def content_box(outer: Box, padding: Optional[IRSpacing]) -> Box:
        """Inner box after consuming padding."""
        top = padding.top if padding and padding.top is not None else 0.0
        right = padding.right if padding and padding.right is not None else 0.0
        bottom = padding.bottom if padding and padding.bottom is not None else 0.0
        left = padding.left if padding and padding.left is not None else 0.0
        return Box(
            x=outer.x + left,
            y=outer.y + top,
            width=max(0.0, outer.width - left - right),
            height=max(0.0, outer.height - top - bottom),
        )

    @staticmethod
    def padding_offsets(padding: Optional[IRSpacing]) -> Dict[str, float]:
        if padding is None:
            return {"top": 0.0, "right": 0.0, "bottom": 0.0, "left": 0.0}
        return {
            "top": padding.top or 0.0,
            "right": padding.right if padding.right is not None else 0.0,
            "bottom": padding.bottom if padding.bottom is not None else 0.0,
            "left": padding.left or 0.0,
        }

    @staticmethod
    def delta(a: Optional[Box], b: Optional[Box]) -> float:
        """: The max component-wise difference between two boxes.
        There are no sides in Box; x/y are origins and width/height are extents.

        Used to compare a predicted box to a Figma box (both parent-relative).
        """
        if a is None or b is None:
            return float("inf")
        return max(
            abs(a.x - b.x),
            abs(a.y - b.y),
            abs(a.width - b.width),
            abs(a.height - b.height),
        )