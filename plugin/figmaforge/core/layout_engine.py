"""
Layout inference engine (Part 5).

Reads the normalized Design IR and builds a per-node :class:`LayoutPlan` tree —
the framework-neutral "how should this design lay out" model. This module owns
the *inference*: flex/grid/absolute classification, per-axis sizing
(fixed/fill/hug/percent), min/max, spacing, alignment, anchoring, text wrapping,
overflow, and nested propagation. The constraint model, breakpoint model, and
confidence/orchestration live in sibling modules.

Determinism and honesty rules, consistent with FigmaForge conventions:

- **Fixed sizes are preserved exactly**, clamped only when the design's own
  min/max require it.
- **Nothing is invented.** A bound that cannot be solved from evidence is marked
  underdetermined and contributes no number. ``percent``/``fill`` inside a
  ``hug`` container and fills under an unresolved parent are reported, not
  guessed.
- **Semantic layout preferred.** Children of an auto-layout parent flow;
  absolute positioning is used only where the IR positions the node absolutely.
- Text content sizes are *heuristic* (:class:`TextMeasurer`) and always flagged
  ``approximate`` — see ``docs/layout.md``.

Resolution order within ``_build``:

1. Cheap non-hug axes (fixed/fill/percent) are resolved first so a provisional
   content box is known; hug axes are left open.
2. Children are built against that provisional box (hug axes expose ``None``,
   so a child filling a hug axis is correctly flagged underdetermined).
3. Hug axes are resolved from child/text extents.
4. Finally the container lays its children out (flow or absolute).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .constraint_model import (
    AXIS_HORIZONTAL,
    AXIS_VERTICAL,
    BoxSolver,
    ConstraintModel,
)
from .ir_types import IRDocument, IRNode, KIND_PAGE, KIND_TEXT
from .layout_types import (
    AlignmentSpec,
    Anchoring,
    AxisSizing,
    Box,
    Diagnostic,
    EdgeOffsets,
    LayoutNodePlan,
    OverflowSpec,
    SizingSpec,
    SpacingSpec,
    TextModel,
    WRAP_NONE,
    WRAP_WRAP,
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    DISPLAY_GRID,
    DISPLAY_NONE,
    OVERFLOW_CLIP,
    OVERFLOW_VISIBLE,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SIZING_FILL,
    SIZING_FIXED,
    SIZING_HUG,
    SIZING_PERCENT,
)

# Heuristic average glyph advance relative to the font size (no glyph metrics
# available in a stdlib-only analysis path). Documented; text measurements are
# flagged ``approximate``.
DEFAULT_CHAR_ADVANCE = 0.55
DEFAULT_LINE_HEIGHT_FACTOR = 1.2

# Figma responsive constraint -> framework-neutral anchor
_ANCHOR_BY_CONSTRAINT = {
    "MIN": "min",
    "CENTER": "center",
    "MAX": "max",
    "STRETCH": "stretch",
    "SCALE": "scale",
}

# Cross-axis placement factors for flow containers
_CROSS_FACTOR_BY_ALIGN = {"MIN": 0.0, "CENTER": 0.5, "MAX": 1.0}

# Assumption codes recorded per node
ASSUME_TEXT_APPROX = "text_width_heuristic"
ASSUME_ABS_NO_ANCHOR = "absolute_without_anchors"
ASSUME_SCALE_AS_MIN = "scale_anchor_approximated_as_min"
ASSUME_FILL_IN_HUG = "fill_or_percent_in_hug_container"
ASSUME_GRID_HUG = "grid_hug_approximated"


@dataclass
class _ParentContext:
    """What a node knows about its containing box while being built."""

    node: Optional[IRNode] = None  # containing IR node (for Figma coords)
    type: str = "page"  # "page" | "flow" | "absolute" | "grid"
    direction: Optional[str] = None  # "row" | "column" (flow/grid only)
    content: Optional[Box] = None  # padded content box (None when unsolved)
    grow_total: float = 0.0  # total main-axis grow among flow siblings
    gap: float = 0.0  # parent gap on the main axis
    flow_count: int = 0  # number of flow siblings (for gap math in percents)


@dataclass
class _AxisResult:
    """The resolved value for one axis."""

    size: Optional[float] = None
    mode: str = SIZING_FIXED
    value: Optional[float] = None
    measured: Optional[float] = None
    assumption: Optional[str] = None


def _safe(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 6)


def _axis_pad(pad: Dict[str, float], is_h: bool) -> float:
    if is_h:
        return pad.get("left", 0.0) + pad.get("right", 0.0)
    return pad.get("top", 0.0) + pad.get("bottom", 0.0)


class TextMeasurer:
    """Heuristic text measurement (flag ``approximate``, stdlib only)."""

    def __init__(
        self,
        char_advance: float = DEFAULT_CHAR_ADVANCE,
        line_height_factor: float = DEFAULT_LINE_HEIGHT_FACTOR,
    ):
        self._char_advance = char_advance
        self._line_height_factor = line_height_factor

    def measure(
        self,
        node: IRNode,
        available_width: Optional[float],
        fixed_width: Optional[float],
    ) -> Optional[TextModel]:
        """Measure a text node; ``None`` when there is no measurable content."""
        if not node.is_text or node.text is None or not node.text.characters:
            return None
        text = node.text.characters
        typo = node.typography
        font_size = typo.font_size if typo else None
        if not font_size or font_size <= 0:
            font_size = 16.0  # documented default when the IR omits it
        line_height = (typo.line_height if typo and typo.line_height else None) or \
            font_size * self._line_height_factor
        letter_spacing = (typo.letter_spacing if typo else None) or 0.0
        per_char = font_size * self._char_advance + letter_spacing

        auto_resize = (typo.auto_resize if typo else None) or "NONE"
        wrap_width = fixed_width if fixed_width is not None else available_width

        natural_width = per_char * len(text)
        if auto_resize == "WIDTH_AND_HEIGHT" or wrap_width is None or natural_width <= wrap_width:
            lines = [text]
            wrapped = False
            measured_width = natural_width
        else:
            lines = self._wrap(text, per_char, wrap_width)
            wrapped = len(lines) > 1
            measured_width = max(self._line_width(line, per_char) for line in lines)
        measured_height = len(lines) * line_height
        return TextModel(
            characters=text,
            font_size=font_size,
            measured_width=round(measured_width, 4),
            measured_height=round(measured_height, 4),
            wrapped=wrapped,
            lines=lines,
            approximate=True,
        )

    # ------------------------------------------------------------- helpers
    def _wrap(self, text: str, per_char: float, wrap_width: float) -> List[str]:
        words = text.split()
        lines: List[str] = []
        current = ""
        current_w = 0.0
        space_w = per_char
        for word in words:
            word_w = self._line_width(word, per_char)
            if not current:
                current, current_w = word, word_w
            elif current_w + space_w + word_w <= wrap_width:
                current = f"{current} {word}"
                current_w += space_w + word_w
            elif word_w <= wrap_width:
                lines.append(current)
                current, current_w = word, word_w
            else:  # single word longer than the width: deterministic hard split
                lines.append(current)
                lines.extend(self._hard_split(word, per_char, wrap_width))
                current, current_w = "", 0.0
        if current:
            lines.append(current)
        return lines or [text]

    @staticmethod
    def _hard_split(word: str, per_char: float, wrap_width: float) -> List[str]:
        pieces: List[str] = []
        chunk = ""
        width = 0.0
        for ch in word:
            cw = per_char
            if width + cw > wrap_width and chunk:
                pieces.append(chunk)
                chunk, width = "", 0.0
            chunk += ch
            width += cw
        if chunk:
            pieces.append(chunk)
        return pieces

    @staticmethod
    def _line_width(line: str, per_char: float) -> float:
        return per_char * len(line)


class LayoutEngine:
    """Build screen-level ``LayoutNodePlan`` trees from a Design IR."""

    def __init__(
        self,
        measurer: Optional[TextMeasurer] = None,
        bounds_epsilon: float = 1e-4,
    ):
        self._measurer = measurer or TextMeasurer()
        self._bounds_epsilon = bounds_epsilon
        self._viewport = 0.0
        self._base_width = 0.0
        self._current_node: Optional[IRNode] = None

    # ------------------------------------------------------------------ API
    def screens(
        self,
        document: IRDocument,
        viewport: float,
        base_width: float,
    ) -> List[LayoutNodePlan]:
        """Analyze every page into a top-level screen plan."""
        self._viewport = viewport
        self._base_width = base_width
        context = _ParentContext(
            node=None,
            type="page",
            content=Box(x=0.0, y=0.0, width=viewport, height=0.0),
        )
        plans = [
            self._build(page, context, order=idx)
            for idx, page in enumerate(document.pages)
        ]
        for plan in plans:
            self._finalize(plan, 0.0, 0.0)
        return plans

    # ------------------------------------------------------------ main pass
    def _build(
        self,
        node: IRNode,
        parent: _ParentContext,
        order: int,
    ) -> LayoutNodePlan:
        self._current_node = node
        model = ConstraintModel(node)
        report = model.report()
        facts_h = model.facts[AXIS_HORIZONTAL]
        facts_v = model.facts[AXIS_VERTICAL]

        display, direction = self._infer_display(node)
        padding = node.layout.padding if node.layout else None
        gap = (node.layout.gap if node.layout else None) or 0.0

        text: Optional[TextModel] = None
        if node.kind == KIND_TEXT:
            # AUTO (hug) text wraps to the available content width; FIXED text
            # wraps to its recorded width.
            fixed_width = None if facts_h.sizing == "AUTO" else facts_h.width_value
            text = self._measurer.measure(
                node,
                parent.content.width if parent.content else None,
                fixed_width,
            )

        plan = LayoutNodePlan(
            node_id=node.id,
            name=node.name,
            kind=node.kind,
            display=display,
            direction=direction,
            order=order,
            constraints=report,
            text=text,
        )

        # --- step 1: provisional content box from cheap (non-hug) axes
        provisional = self._provisional_content(
            node, facts_h, facts_v, parent, display, direction, padding,
        )

        # --- step 2: build children against the provisional box
        if node.children:
            plan.children = self._build_children(node, display, direction, padding, provisional)

        # --- step 3: resolve both axes (hug now measurable)
        res_h = self._resolve_axis(
            axis=AXIS_HORIZONTAL, facts=facts_h, node=node,
            parent=parent, display=display, direction=direction,
            children=plan.children, text=text, padding=padding, gap=gap,
        )
        res_v = self._resolve_axis(
            axis=AXIS_VERTICAL, facts=facts_v, node=node,
            parent=parent, display=display, direction=direction,
            children=plan.children, text=text, padding=padding, gap=gap,
        )

        # --- box + placement (absolute only; flow origin set by the parent)
        box = None
        if res_h.size is not None and res_v.size is not None:
            if parent.type in ("page", "absolute"):
                x, y, w2, h2 = self._anchor_box(
                    facts_h, facts_v, res_h.size, res_v.size,
                    parent.content, node, parent.node,
                )
            else:
                x, y, w2, h2 = 0.0, 0.0, res_h.size, res_v.size
            box = Box(x=x, y=y, width=w2, height=h2)
        plan.box = box
        plan.figma_box = self._figma_box(node, parent.node)

        # --- model outputs
        plan.sizing = self._sizing_spec(res_h, res_v, facts_h, facts_v)
        plan.spacing = self._spacing(node, parent, box)
        plan.alignment = self._alignment(node)
        plan.anchors = self._anchors(facts_h, facts_v, parent)

        # --- assumptions + text-approximation diagnostic
        for assumption in (res_h.assumption, res_v.assumption):
            if assumption:
                plan.assumptions.append(assumption)
        if text is not None and text.approximate and text.characters:
            plan.assumptions.append(ASSUME_TEXT_APPROX)
        if facts_h.constraints == "SCALE" or facts_v.constraints == "SCALE":
            plan.assumptions.append(ASSUME_SCALE_AS_MIN)
        for axis_res, axis_name in ((res_h, "horizontal"), (res_v, "vertical")):
            if axis_res.size is None:
                plan.diagnostics.append(Diagnostic(
                    severity=SEVERITY_WARNING, code="underdetermined",
                    message=(
                        f"{axis_name} size cannot be resolved "
                        "(hug with no measurable content, or an unresolved parent)"
                    ),
                    node_id=plan.node_id))
        self._collect_diagnostics(plan, box, report)

        # --- step 4: nested propagation (lay out children in the solved box)
        if display in (DISPLAY_FLEX, DISPLAY_GRID) and node.children and box is not None:
            # Lay children out relative to the parent's local origin; the
            # top-down ``_finalize`` pass accumulates ancestor offsets.
            content = BoxSolver.content_box(
                Box(x=0.0, y=0.0, width=box.width, height=box.height), padding)
            self._lay_out(
                children=plan.children,
                direction=direction,
                content=content,
                display=display,
                gap=gap,
                align=(node.layout.align if node.layout else None),
                justify=(node.layout.justify if node.layout else None),
                grid_columns=(node.layout.grid_columns if node.layout else None),
            )
        # Overflow needs children positioned, so it runs after step 4.
        plan.overflow = self._overflow(node, plan.children, box, padding, text)
        return plan

    # ------------------------------------------------------- provisional box
    def _provisional_content(
        self,
        node: IRNode,
        facts_h,
        facts_v,
        parent: _ParentContext,
        display: str,
        direction: Optional[str],
        padding,
    ) -> Box:
        """Content box formed from axes resolvable *before* children exist."""
        pad = BoxSolver.padding_offsets(padding)

        def cheap_axis(axis, facts):
            if node.kind == KIND_PAGE:
                # A page is the viewport: its content box is the parent extent.
                content = parent.content
                return (content.width if content else None) \
                    if axis == AXIS_HORIZONTAL else (content.height if content else None)
            if facts.sizing == "AUTO":
                return None  # hug: only measurable after children/text
            content = parent.content
            content_axis = (content.width if content else None) \
                if axis == AXIS_HORIZONTAL else (content.height if content else None)
            parent_dir = parent.direction
            is_cross = bool(
                parent.type == "flow" and parent_dir and
                ((axis == AXIS_HORIZONTAL and parent_dir == "column") or
                 (axis == AXIS_VERTICAL and parent_dir == "row")))
            cross_fill = bool(is_cross and (
                (node.layout and node.layout.align_self == "FILL") or
                (parent.node and parent.node.layout and parent.node.layout.align == "STRETCH")))
            # FILL overrides any recorded fixed width (Figma semantics).
            if facts.sizing == "FILL" or cross_fill:
                return BoxSolver.size_from_fill(content_axis, facts)
            if facts.width_value is not None:
                return BoxSolver.size_from_fixed(facts.width_value, facts)
            is_main = bool(
                parent.type == "flow" and parent_dir and
                ((axis == AXIS_HORIZONTAL and parent_dir == "row") or
                 (axis == AXIS_VERTICAL and parent_dir == "column")))
            if is_main and node.layout and node.layout.grow and node.layout.grow > 0:
                total = parent.grow_total if parent.grow_total else 1.0
                return BoxSolver.size_from_percent(content_axis, node.layout.grow, total, facts)
            if parent.type == "grid" and (facts.sizing == "FILL" or facts.width_value is None):
                total = parent.grow_total if parent.grow_total else 1.0
                return BoxSolver.size_from_percent(content_axis, 1.0, total, facts)
            return None  # hug: only measurable after children/text

        w = cheap_axis(AXIS_HORIZONTAL, facts_h)
        h = cheap_axis(AXIS_VERTICAL, facts_v)
        return Box(
            x=0.0, y=0.0,
            width=(w - pad["left"] - pad["right"]) if w is not None else None,  # type: ignore[arg-type]
            height=(h - pad["top"] - pad["bottom"]) if h is not None else None,  # type: ignore[arg-type]
        )

    # ------------------------------------------------------ axis resolution
    def _available(self, parent: _ParentContext, content_axis: Optional[float]) -> Optional[float]:
        """Free space on an axis inside ``parent`` for percent sizing.

        Percent children grow against the container's *free* space — the content
        extent minus the gaps the fixed siblings occupy. When the content extent
        is unresolved (None) the free space is too, and percent sizing is flagged
        underdetermined by the caller.
        """
        if content_axis is None:
            return None
        if parent.flow_count > 1 and parent.gap:
            return max(0.0, content_axis - parent.gap * (parent.flow_count - 1))
        return content_axis

    def _resolve_axis(
        self,
        axis: str,
        facts,
        node: IRNode,
        parent: _ParentContext,
        display: str,
        direction: Optional[str],
        children: List,
        text: Optional[TextModel],
        padding,
        gap: float,
    ) -> _AxisResult:
        result = _AxisResult()
        content = parent.content
        content_axis = (content.width if content else None) \
            if axis == AXIS_HORIZONTAL else (content.height if content else None)

        parent_dir = parent.direction
        is_main = bool(
            parent.type == "flow" and parent_dir and
            ((axis == AXIS_HORIZONTAL and parent_dir == "row") or
             (axis == AXIS_VERTICAL and parent_dir == "column")))
        is_cross = bool(parent.type == "flow" and parent_dir and not is_main)
        cross_fill = bool(is_cross and (
            (node.layout and node.layout.align_self == "FILL") or
            (parent.node and parent.node.layout and parent.node.layout.align == "STRETCH")))

        # --- grid: fill/unsized children share a column; explicit widths hold
        if parent.type == "grid":
            if facts.width_value is not None and facts.sizing != "FILL":
                result.mode = SIZING_FIXED
                result.value = facts.width_value
                result.measured = BoxSolver.size_from_fixed(facts.width_value, facts)
                result.size = result.measured
                return result
            share = 1.0
            total = parent.grow_total if parent.grow_total else float(max(1, len(children)))
            size = BoxSolver.size_from_percent(
                self._available(parent, content_axis), share, total, facts)
            if size is None:
                result.mode, result.assumption = SIZING_PERCENT, ASSUME_FILL_IN_HUG
            else:
                result.mode, result.size, result.value = SIZING_PERCENT, size, share
                result.measured = size
            return result

        # --- percent: flex-grow on the flow main axis
        if is_main and node.layout and node.layout.grow and node.layout.grow > 0:
            total = parent.grow_total if parent.grow_total else float(max(1, len(children)))
            size = BoxSolver.size_from_percent(
                self._available(parent, content_axis), node.layout.grow, total, facts)
            if size is None:
                result.mode, result.assumption = SIZING_PERCENT, ASSUME_FILL_IN_HUG
            else:
                result.mode, result.size, result.value = (
                    SIZING_PERCENT, size, node.layout.grow)
            return result

        # --- fill
        if facts.sizing == "FILL" or cross_fill:
            size = BoxSolver.size_from_fill(content_axis, facts)
            if size is None:
                result.mode, result.assumption = SIZING_FILL, ASSUME_FILL_IN_HUG
            else:
                result.mode, result.size = SIZING_FILL, size
            return result

        # --- hug wins over a recorded box when the design says AUTO
        if facts.sizing == "AUTO":
            return self._resolve_hug_axis(
                axis=axis, result=result, display=display, direction=direction,
                children=children, text=text, padding=padding, gap=gap, facts=facts,
            )

        # --- fixed
        if facts.width_value is not None:
            result.mode = SIZING_FIXED
            result.value = facts.width_value
            result.measured = BoxSolver.size_from_fixed(facts.width_value, facts)
            result.size = result.measured
            return result

        # --- hug (content-driven)
        return self._resolve_hug_axis(
            axis=axis, result=result, display=display, direction=direction,
            children=children, text=text, padding=padding, gap=gap, facts=facts,
        )

    def _resolve_hug_axis(self, axis, result, display, direction, children, text, padding, gap, facts) -> _AxisResult:
        result.mode = SIZING_HUG
        if display == DISPLAY_GRID:
            result.assumption = ASSUME_GRID_HUG
        measured = self._content_extent(
            axis=axis, display=display, direction=direction,
            children=children, text=text, padding=padding, gap=gap,
            main_axis=(direction == ("row" if axis == AXIS_HORIZONTAL else "column")),
        )
        result.measured = _safe(measured)
        size = BoxSolver.size_from_hug(measured, facts)
        if size is None:
            result.assumption = "hug_no_content"
        else:
            result.size = size
        return result

    def _content_extent(
        self,
        axis: str,
        display: str,
        direction: Optional[str],
        children: List,
        text: Optional[TextModel],
        padding,
        gap: float,
        main_axis: bool,
    ) -> Optional[float]:
        pad = BoxSolver.padding_offsets(padding)
        is_h = axis == AXIS_HORIZONTAL

        flow_children = [c for c in children if c.display != DISPLAY_ABSOLUTE]
        if flow_children:
            sizes = self._flow_sizes(flow_children, is_h)
            if display == DISPLAY_GRID:
                count = max(1, len(flow_children))
                largest = max((s or 0.0) for s in sizes)
                return _safe(largest * count + gap * (count - 1) + _axis_pad(pad, is_h))
            if main_axis:
                used = sum(s if s is not None else 0.0 for s in sizes)
                return _safe(used + gap * (len(flow_children) - 1) + _axis_pad(pad, is_h))
            cross = max((s or 0.0) for s in sizes)
            return _safe(cross + _axis_pad(pad, is_h))

        if text is not None:
            if is_h:
                return _safe((text.measured_width or 0.0) + _axis_pad(pad, is_h))
            return _safe((text.measured_height or 0.0) + _axis_pad(pad, is_h))
        return None  # nothing measurable: engine flags this as underdetermined

    @staticmethod
    def _flow_sizes(children: List, is_h: bool) -> List[Optional[float]]:
        out: List[Optional[float]] = []
        for child in children:
            if child.box is None:
                out.append(None)
                continue
            out.append(child.box.width if is_h else child.box.height)
        return out

    # ------------------------------------------------------------- children
    def _build_children(self, node, display, direction, padding, provisional: Box) -> List:
        if display == DISPLAY_FLEX:
            grow_total = sum(
                (child.layout.grow or 0) for child in node.children
                if child.layout is not None
            ) or float(max(1, len(node.children)))
            child_type = "flow"
        elif display == DISPLAY_GRID:
            count = (node.layout.grid_columns.get("count")
                     if node.layout and node.layout.grid_columns else None)
            grow_total = float(count if isinstance(count, int) and count > 0 else max(1, len(node.children)))
            child_type = "grid"
        else:
            grow_total = 0.0
            child_type = "absolute"

        flow_count = len(node.children) if display == DISPLAY_FLEX else 0
        parent_gap = node.layout.gap if node.layout else 0.0

        def context_for(child):
            if self._is_raw_absolute(child):
                return _ParentContext(
                    node=node, type="absolute", direction=None,
                    content=provisional, grow_total=0.0,
                )
            return _ParentContext(
                node=node, type=child_type, direction=direction,
                content=provisional, grow_total=grow_total,
                gap=parent_gap, flow_count=flow_count,
            )

        return [
            self._build(child, context_for(child), order=idx)
            for idx, child in enumerate(node.children)
        ]

    # ----------------------------------------------------------- placement
    def _anchor_box(
        self,
        facts_h,
        facts_v,
        w: float,
        h: float,
        c: Optional[Box],
        node: IRNode,
        parent_node: Optional[IRNode],
    ) -> Tuple[float, float, float, float]:
        """Parent-relative anchor math for an absolutely-positioned node.

        Offsets are inferred from the node's recorded position and expressed
        relative to the parsed content box, so every anchor reproduces the
        design's position at the native width (deterministic) and reflows the
        node as the container resizes. STRETCH re-derives the extent.
        """
        w = w if w is not None else 0.0
        h = h if h is not None else 0.0
        if c is None:
            return (0.0, 0.0, w, h)
        cw, ch = c.width, c.height
        if cw is None or ch is None:
            # Parent content extent unresolved (hug under an unsolved parent or
            # an underdetermined frame): cannot anchor — place at content origin.
            return (c.x, c.y, w, h)
        rel_l, _, rel_t, _ = self._rel_offsets(parent_node, w, h)
        x0 = (rel_l if rel_l is not None else 0.0)
        y0 = (rel_t if rel_t is not None else 0.0)
        left = x0 - c.x
        right = (c.x + cw) - (x0 + w)
        top = y0 - c.y
        bottom = (c.y + ch) - (y0 + h)

        ah = facts_h.constraints
        av = facts_v.constraints
        if ah == "MAX":
            x = c.x + c.width - w - right
        elif ah == "CENTER":
            x = c.x + (c.width - w) / 2.0
        else:  # MIN / SCALE / None -> left-anchored
            x = c.x + left
        if av == "MAX":
            y = c.y + c.height - h - bottom
        elif av == "CENTER":
            y = c.y + (c.height - h) / 2.0
        else:  # MIN / TOP / None -> top-anchored
            y = c.y + top

        w2, h2 = w, h
        if ah == "STRETCH":
            w2 = max(0.0, cw - left - right)
            x = c.x + left
        if av == "STRETCH":
            h2 = max(0.0, ch - top - bottom)
            y = c.y + top
        return (x, y, w2, h2)

    def _rel_offsets(
        self,
        parent_node: Optional[IRNode],
        self_w: float,
        self_h: float,
    ) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """(left, right, top, bottom) distances of the current node from the
        parent, computed from canvas coords minus the parent's canvas box."""
        node = self._current_node
        pos = node.position if node else None
        if pos is None:
            return (None, None, None, None)
        ppos = parent_node.position if parent_node else None
        pbox = parent_node.dimensions if parent_node else None
        if ppos is not None and pbox is not None and pos.x is not None and pos.y is not None:
            px, py = ppos.x, ppos.y
            pw, ph = pbox.width, pbox.height
            if px is not None and py is not None and pw is not None and ph is not None:
                left = pos.x - px
                top = pos.y - py
                right = (px + pw) - (pos.x + self_w)
                bottom = (py + ph) - (pos.y + self_h)
                return (left, right, top, bottom)
        return (None, None, None, None)

    # ------------------------------------------------------------ lay-out
    def _lay_out(
        self,
        children: List,
        direction: Optional[str],
        content: Box,
        display: str,
        gap: float,
        align: Optional[str],
        justify: Optional[str],
        grid_columns: Optional[Dict[str, Any]],
    ) -> None:
        count = None
        if grid_columns:
            raw = grid_columns.get("count")
            if isinstance(raw, int) and raw > 0:
                count = raw
        if direction == "row":
            self._lay_out_row(children, content, gap, align, justify, display == DISPLAY_GRID, count)
        else:
            self._lay_out_column(children, content, gap, align, justify, display == DISPLAY_GRID, count)

    def _lay_out_row(self, children, content, gap, align, justify, is_grid, grid_count):
        flow = [c for c in children if c.display != DISPLAY_ABSOLUTE]
        used = sum((c.box.width if c.box else 0) for c in flow)
        free = max(0.0, content.width - used - (gap * max(0, len(flow) - 1) if not is_grid else 0.0))
        start = content.x + self._justify_offset(justify, free)
        cursor = start
        cross_factor = _CROSS_FACTOR_BY_ALIGN.get(align, 0.0)
        for idx, child in enumerate(flow):
            if child.box is None:
                continue
            b = child.box
            if is_grid:
                n = grid_count or max(1, len(flow))
                col = idx % n
                col_w = (content.width - gap * (n - 1)) / n if n else content.width
                b.x = content.x + col * (col_w + gap)
            else:
                b.x = cursor
                cursor += b.width + gap
            if align == "STRETCH":
                b.height = content.height
                b.y = content.y
            elif cross_factor == 0.0:
                b.y = content.y
            elif cross_factor == 0.5:
                b.y = content.y + (content.height - b.height) / 2.0
            else:
                b.y = content.y + content.height - b.height

    def _lay_out_column(self, children, content, gap, align, justify, is_grid, grid_count):
        flow = [c for c in children if c.display != DISPLAY_ABSOLUTE]
        used = sum((c.box.height if c.box else 0) for c in flow)
        free = max(0.0, content.height - used - (gap * max(0, len(flow) - 1) if not is_grid else 0.0))
        start = content.y + self._justify_offset(justify, free)
        cursor = start
        cross_factor = _CROSS_FACTOR_BY_ALIGN.get(align, 0.0)
        for idx, child in enumerate(flow):
            if child.box is None:
                continue
            b = child.box
            if is_grid:
                n = grid_count or max(1, len(flow))
                row = idx % n
                row_h = (content.height - gap * (n - 1)) / n if n else content.height
                b.y = content.y + row * (row_h + gap)
            else:
                b.y = cursor
                cursor += b.height + gap
            if align == "STRETCH":
                b.width = content.width
                b.x = content.x
            elif cross_factor == 0.0:
                b.x = content.x
            elif cross_factor == 0.5:
                b.x = content.x + (content.width - b.width) / 2.0
            else:
                b.x = content.x + content.width - b.width

    @staticmethod
    def _justify_offset(justify: Optional[str], free: float) -> float:
        if justify == "CENTER":
            return free / 2.0
        if justify in ("MAX", "SPACE_BETWEEN"):
            return free if justify == "MAX" else 0.0
        return 0.0

    def _finalize(self, plan: LayoutNodePlan, ox: float, oy: float) -> None:
        """Accumulate ancestor offsets so every box is page-absolute.

        Children are laid out in their parent's local origin during ``_build``;
        this top-down pass adds each ancestor's resolved origin so a node's
        ``box`` is directly comparable to its page-absolute ``figma_box``.
        It also computes ``bounds_delta`` — the only point where predicted and
        Figma bounds are in the same coordinate space — and emits the
        ``bounds_mismatch`` diagnostic for unresolved discrepancies.
        """
        if plan.box is not None:
            plan.box = Box(
                x=plan.box.x + ox,
                y=plan.box.y + oy,
                width=plan.box.width,
                height=plan.box.height,
            )
            if plan.figma_box is not None:
                plan.bounds_delta = BoxSolver.delta(plan.box, plan.figma_box)
                if plan.bounds_delta > self._bounds_epsilon:
                    plan.diagnostics.append(Diagnostic(
                        severity=SEVERITY_WARNING, code="bounds_mismatch",
                        message=(
                            f"predicted bounds differ from Figma by "
                            f"{plan.bounds_delta:.4f}"),
                        node_id=plan.node_id))
        nx, ny = (plan.box.x, plan.box.y) if plan.box is not None else (ox, oy)
        for child in plan.children:
            self._finalize(child, nx, ny)

    # ----------------------------------------------------------- inference
    @staticmethod
    def _is_raw_absolute(node: IRNode) -> bool:
        """Figma marks children absolutely positioned within auto-layout via
        ``layoutPositioning: ABSOLUTE`` (preserved in the IR's raw payload)."""
        raw = node.raw if isinstance(node.raw, dict) else {}
        return raw.get("layoutPositioning") == "ABSOLUTE"

    def _infer_display(self, node: IRNode) -> Tuple[str, Optional[str]]:
        if self._is_raw_absolute(node):
            return DISPLAY_ABSOLUTE, None
        layout = node.layout
        if layout is not None and layout.mode == "auto":
            return DISPLAY_FLEX, layout.direction
        if layout is not None and layout.mode == "grid":
            return DISPLAY_GRID, (layout.direction or "row")
        if node.position is not None and node.position.mode == "absolute":
            return DISPLAY_ABSOLUTE, None
        return DISPLAY_NONE, None

    @staticmethod
    def _sizing_spec(res_h: _AxisResult, res_v: _AxisResult, facts_h, facts_v) -> SizingSpec:
        def axis(res, facts):
            return AxisSizing(
                mode=res.mode,
                value=res.value,
                min=facts.min,
                max=facts.max,
                measured=res.measured,
                explicit=facts.width_value is not None,
            )
        return SizingSpec(horizontal=axis(res_h, facts_h), vertical=axis(res_v, facts_v))

    @staticmethod
    def _spacing(node: IRNode, parent: _ParentContext, box: Optional[Box]) -> SpacingSpec:
        padding = None
        if node.layout and node.layout.padding:
            p = node.layout.padding
            padding = EdgeOffsets(top=p.top, right=p.right, bottom=p.bottom, left=p.left)
        gap = node.layout.gap if node.layout else None
        margin = None
        margin_source = None
        # Absolute offsets are the only native "margin" evidence in Figma.
        if node.position is not None and node.position.mode == "absolute" \
                or LayoutEngine._is_raw_absolute(node):
            margin = EdgeOffsets(
                top=node.position.y or 0.0,
                right=0,
                bottom=0,
                left=node.position.x or 0.0,
            )
            margin_source = "absolute_offset"
        return SpacingSpec(padding=padding, margin=margin, margin_source=margin_source, gap=gap)

    @staticmethod
    def _alignment(node: IRNode) -> AlignmentSpec:
        layout = node.layout
        return AlignmentSpec(
            justify=layout.justify if layout else None,
            align=layout.align if layout else None,
            align_self=layout.align_self if layout else None,
        )

    def _anchors(self, facts_h, facts_v, parent: _ParentContext) -> Anchoring:
        l, r, t, b = self._rel_offsets(parent.node, 0.0, 0.0)
        return Anchoring(
            horizontal=_ANCHOR_BY_CONSTRAINT.get(facts_h.constraints, "min"),
            vertical=_ANCHOR_BY_CONSTRAINT.get(facts_v.constraints, "min"),
            left=l,
            right=r,
            top=t,
            bottom=b,
        )

    def _overflow(self, node, children, box, padding, text) -> OverflowSpec:
        layout = node.layout
        wrap = WRAP_WRAP if layout and layout.wrap == "WRAP" else WRAP_NONE
        x = OVERFLOW_VISIBLE
        y = OVERFLOW_VISIBLE
        clipped = False
        if box is not None and children:
            content = BoxSolver.content_box(box, padding)
            for child in children:
                if child.box is None:
                    continue
                if (child.box.x + child.box.width
                        > content.x + content.width + self._bounds_epsilon) or \
                        (child.box.y + child.box.height
                         > content.y + content.height + self._bounds_epsilon):
                    clipped = True
        if text is not None and box is not None and \
                (text.measured_height or 0) > box.height + self._bounds_epsilon:
            clipped = True
        if clipped and wrap != WRAP_WRAP:
            x = y = OVERFLOW_CLIP
        return OverflowSpec(x=x, y=y, wrap=wrap, clipped_content=clipped)

    # --------------------------------------------------------- diagnostics
    def _collect_diagnostics(self, plan, box: Optional[Box], report) -> None:
        for issue in report.contradictions:
            plan.diagnostics.append(Diagnostic(
                severity=SEVERITY_ERROR, code="contradiction",
                message=issue.message, node_id=plan.node_id))
        for issue in report.underdetermined:
            plan.diagnostics.append(Diagnostic(
                severity=SEVERITY_WARNING, code="underdetermined",
                message=issue.message, node_id=plan.node_id))
        for issue in report.unsupported:
            plan.diagnostics.append(Diagnostic(
                severity=SEVERITY_INFO, code="unsupported",
                message=issue.message, node_id=plan.node_id))
        for assumption in plan.assumptions:
            plan.diagnostics.append(Diagnostic(
                severity=SEVERITY_INFO, code=f"assumption:{assumption}",
                message=assumption, node_id=plan.node_id))

    # ------------------------------------------------------------- figma
    @staticmethod
    def _figma_box(node: IRNode, parent_node: Optional[IRNode]) -> Optional[Box]:
        """Page-absolute bounds Figma records for the node (canvas coords).

        ``IRPosition`` is already in canvas space, so this is the recorded
        position and size directly — no parent-relative math. The page itself
        has no recorded bounds, so it returns ``None``.
        """
        dims = node.dimensions
        pos = node.position
        if dims is None or pos is None:
            return None
        if pos.x is None and pos.y is None:
            return None  # unpositioned (e.g. the page) — no recorded bounds
        w = dims.width or 0.0
        h = dims.height or 0.0
        x = pos.x or 0.0
        y = pos.y or 0.0
        return Box(x=x, y=y, width=w, height=h)