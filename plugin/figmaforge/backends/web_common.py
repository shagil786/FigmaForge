"""
Shared web code-generation machinery (Part 14).

The VNode/VStyle model and the LayoutNodePlan → CSS-style lowering used by
every web backend (html_css, react_tailwind, vue, svelte). Extracted from
the html_css reference backend so all web targets share ONE style-mapping
implementation and cannot drift apart.

Contains:

- ``VStyle`` / ``VNode`` — the intermediate web node model (CSS camelCase
  properties, semantic tags, text, children).
- ``CssStyleGenerator`` — ``LayoutNodePlan`` → ``VStyle`` CSS properties
  (display, sizing, padding, gap, alignment, absolute positioning).
- ``VNodeBuilder`` — ``LayoutNodePlan`` → ``VNode`` tree (semantic tag
  mapping, component resolution, text).
- ``semantic_tag``, ``camel_to_kebab``, ``escape_html``, ``escape_attr`` —
  small deterministic helpers.

Standard library only; deterministic; no behavior differences from the
html_css originals.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.ir_types import IRNode
from core.layout_types import (
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    DISPLAY_GRID,
    LayoutNodePlan,
    OVERFLOW_CLIP,
    OVERFLOW_SCROLL,
    SIZING_FIXED,
    SIZING_FILL,
    SIZING_HUG,
    SIZING_PERCENT,
)
from core.resolver import ResolutionReport


# ---------------------------------------------------------------------------
# VNode / VStyle — web-specific internal types
# ---------------------------------------------------------------------------

@dataclass
class VStyle:
    """CSS style properties for a node.

    Keys are camelCase CSS property names (e.g. ``paddingTop``,
    ``flexDirection``). Values are CSS value strings (e.g. ``"16px"``,
    ``"flex-start"``).
    """

    base: Dict[str, str] = field(default_factory=dict)
    breakpoints: Dict[str, Dict[str, str]] = field(default_factory=dict)


@dataclass
class VNode:
    """A virtual DOM node targeting HTML-family output.

    ``tag`` is an HTML element name or component name.
    ``is_component`` is True when ``tag`` references a reusable component.
    """

    node_id: str
    tag: str = "div"
    is_component: bool = False
    props: Dict[str, Any] = field(default_factory=dict)
    style: VStyle = field(default_factory=VStyle)
    children: List["VNode"] = field(default_factory=list)
    text_content: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"node_id": self.node_id, "tag": self.tag}
        if self.is_component:
            out["is_component"] = True
        if self.props:
            out["props"] = self.props
        if self.style.base or self.style.breakpoints:
            out["style"] = {
                "base": self.style.base,
                "breakpoints": self.style.breakpoints,
            }
        if self.text_content is not None:
            out["text_content"] = self.text_content
        if self.children:
            out["children"] = [c.to_dict() for c in self.children]
        return {k: v for k, v in out.items() if v or isinstance(v, (bool, int, float))}


# ---------------------------------------------------------------------------
# Semantic HTML tag mapping
# ---------------------------------------------------------------------------

SEMANTIC_TAG_BY_NAME = {
    # Structural — only names that are true semantic HTML elements
    "header": "header",
    "nav": "nav",
    "navigation": "nav",
    "hero": "section",
    "main": "main",
    "content": "main",
    "section": "section",
    "aside": "aside",
    "footer": "footer",
    # Additional semantic names that don't change existing behavior
    "navbar": "nav",
    "navigation bar": "nav",
    "navigationbar": "nav",
    "sidebar": "aside",
    "banner": "header",
}


def semantic_tag(name: str) -> Optional[str]:
    """Map a node name to a semantic HTML tag, or None."""
    return SEMANTIC_TAG_BY_NAME.get(name.lower())


# Valid HTML tag names (lowercase, no special chars)
_VALID_HTML_TAGS = frozenset({
    "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "footer", "nav", "main", "section", "article",
    "aside", "figure", "figcaption", "ul", "ol", "li",
    "a", "img", "button", "input", "textarea", "select",
    "table", "tr", "td", "th", "thead", "tbody",
    "form", "fieldset", "label", "small", "strong", "em",
    "blockquote", "pre", "code", "hr", "br", "video", "audio",
    "canvas", "svg", "details", "summary", "dialog",
})


def _sanitize_tag(name: str) -> str:
    """Convert any Figma element name into a valid HTML tag name.

    Strategy: lowercase, replace non-alphanumeric with hyphens,
    collapse multiples, strip leading/trailing hyphens.
    If the result is a valid HTML tag, use it; otherwise fall back to 'div'.
    """
    # Fast path: already a known tag
    lower = name.lower().strip()
    if lower in _VALID_HTML_TAGS:
        return lower
    # Sanitize: keep only alphanumeric and hyphens
    sanitized = "".join(c if c.isalnum() else "-" for c in lower)
    # Collapse multiple hyphens
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    sanitized = sanitized.strip("-")
    # Check if valid
    if sanitized and sanitized in _VALID_HTML_TAGS:
        return sanitized
    return "div"


# ---------------------------------------------------------------------------
# CSS style generator (shared by every web backend)
# ---------------------------------------------------------------------------

class CssStyleGenerator:
    """Convert a LayoutNodePlan into CSS properties."""

    def generate_style(self, plan: LayoutNodePlan) -> VStyle:
        style = VStyle()
        display = self._map_display(plan)
        # ``position: absolute`` (added below) already implies a block-level
        # box; ``display: absolute`` is not valid CSS, so omit it entirely.
        if display != "absolute":
            style.base["display"] = display

        if plan.box:
            self._apply_sizing(style, plan, display)

        # Absolute descendants use this node's local coordinate space.
        if display != "absolute" and _has_absolute_descendant(plan):
            style.base["position"] = "relative"

        if plan.spacing:
            if plan.spacing.padding:
                p = plan.spacing.padding
                if p.top is not None: style.base["paddingTop"] = f"{p.top}px"
                if p.right is not None: style.base["paddingRight"] = f"{p.right}px"
                if p.bottom is not None: style.base["paddingBottom"] = f"{p.bottom}px"
                if p.left is not None: style.base["paddingLeft"] = f"{p.left}px"
            if plan.spacing.gap:
                style.base["gap"] = f"{plan.spacing.gap}px"

        if display == "flex":
            style.base["flexDirection"] = plan.direction or "row"
            if plan.alignment:
                if plan.alignment.justify:
                    style.base["justifyContent"] = self._map_justify(plan.alignment.justify)
                if plan.alignment.align:
                    style.base["alignItems"] = self._map_align(plan.alignment.align)

        if display == "grid":
            if plan.direction:
                style.base["gridAutoFlow"] = "column" if plan.direction == "column" else "row"
            if plan.spacing and plan.spacing.gap:
                style.base["columnGap"] = f"{plan.spacing.gap}px"
                style.base["rowGap"] = f"{plan.spacing.gap}px"
            if plan.alignment:
                if plan.alignment.justify:
                    style.base["justifyItems"] = self._map_justify(plan.alignment.justify)
                if plan.alignment.align:
                    style.base["alignItems"] = self._map_align(plan.alignment.align)

        # Per-item alignment override (layoutAlign) — wins over the
        # fill-derived ``alignSelf: stretch`` set by ``_apply_sizing``.
        if plan.alignment and plan.alignment.align_self and display != "absolute":
            style.base["alignSelf"] = self._map_align(plan.alignment.align_self)

        if display == "absolute" and plan.box:
            style.base["position"] = "absolute"
            if plan.anchors:
                a = plan.anchors
                if a.left is not None:
                    style.base["left"] = f"{a.left}px"
                elif a.right is not None:
                    style.base["right"] = f"{a.right}px"
                if a.top is not None:
                    style.base["top"] = f"{a.top}px"
                elif a.bottom is not None:
                    style.base["bottom"] = f"{a.bottom}px"

        return style

    def _apply_sizing(self, style: VStyle, plan: LayoutNodePlan, display: str) -> None:
        if not plan.sizing:
            if plan.box:
                style.base["width"] = f"{plan.box.width}px"
                style.base["height"] = f"{plan.box.height}px"
            return

        h = plan.sizing.horizontal
        v = plan.sizing.vertical

        if h:
            if h.mode == SIZING_FIXED:
                style.base["width"] = f"{plan.box.width}px"
            elif h.mode == SIZING_FILL:
                style.base["flex"] = "1 1 0%" if display == "flex" else "100%"
            elif h.mode == SIZING_HUG:
                style.base["width"] = "fit-content"
            elif h.mode == SIZING_PERCENT and h.value is not None:
                style.base["width"] = f"{h.value * 100:.2f}%"
            if h.min is not None:
                style.base["minWidth"] = f"{h.min}px"
            if h.max is not None:
                style.base["maxWidth"] = f"{h.max}px"

        if v:
            if v.mode == SIZING_FIXED:
                style.base["height"] = f"{plan.box.height}px"
            elif v.mode == SIZING_FILL:
                if display == "flex":
                    style.base["flex"] = style.base.get("flex", "1 1 0%")
                    style.base["alignSelf"] = "stretch"
                else:
                    style.base["height"] = "100%"
            elif v.mode == SIZING_HUG:
                style.base["height"] = "fit-content"
            elif v.mode == SIZING_PERCENT and v.value is not None:
                style.base["height"] = f"{v.value * 100:.2f}%"
            if v.min is not None:
                style.base["minHeight"] = f"{v.min}px"
            if v.max is not None:
                style.base["maxHeight"] = f"{v.max}px"

    def _map_display(self, plan: LayoutNodePlan) -> str:
        if plan.display == DISPLAY_FLEX: return "flex"
        if plan.display == DISPLAY_GRID: return "grid"
        if plan.display == DISPLAY_ABSOLUTE: return "absolute"
        return "block"

    def _map_justify(self, justify: str) -> str:
        mapping = {
            "MIN": "flex-start",
            "MAX": "flex-end",
            "CENTER": "center",
            "SPACE_BETWEEN": "space-between",
        }
        return mapping.get(justify, "flex-start")

    def _map_align(self, align: str) -> str:
        mapping = {
            "MIN": "flex-start",
            "MAX": "flex-end",
            "CENTER": "center",
            "STRETCH": "stretch",
        }
        return mapping.get(align, "flex-start")


# ---------------------------------------------------------------------------
# VNode tree builder (shared by every web backend)
# ---------------------------------------------------------------------------

class VNodeBuilder:
    """Build a VNode tree from a LayoutPlan."""

    def __init__(self, resolution: Optional[ResolutionReport] = None):
        self._component_names: Dict[str, str] = {}
        if resolution is not None:
            self._index_resolution(resolution)

    def _index_resolution(self, report: ResolutionReport) -> None:
        for match in report.resolved:
            if match.matches:
                self._component_names[match.figma_component] = match.matches[0]
        for inst in report.instances:
            if inst.get("status") == "resolved" and inst.get("resolved_name"):
                self._component_names[inst["node_id"]] = inst["resolved_name"]

    def build(self, plan: LayoutNodePlan) -> VNode:
        return self._build_node(plan)

    def _build_node(self, plan: LayoutNodePlan) -> VNode:
        is_component, tag = self._resolve_tag(plan)
        props: Dict[str, Any] = {}
        if plan.node_id:
            props["data-figma-id"] = plan.node_id
        if plan.name:
            props["name"] = plan.name

        node = VNode(
            node_id=plan.node_id,
            tag=tag,
            is_component=is_component,
            props=props,
        )

        for child in plan.children:
            node.children.append(self._build_node(child))

        if plan.text and plan.text.characters:
            node.text_content = plan.text.characters

        return node

    def _resolve_tag(self, plan: LayoutNodePlan) -> tuple:
        if plan.node_id and plan.node_id in self._component_names:
            # Component names are used as custom tags in fallback definitions
            # (e.g. <Badge>, <ButtonCard>). Keep them as-is for framework
            # compatibility — the fallback defines the component.
            return True, self._component_names[plan.node_id]
        return False, self._get_tag_for(plan)

    def _get_tag_for(self, plan: LayoutNodePlan) -> str:
        if plan.kind == "text":
            return "span"
        if plan.display in (DISPLAY_FLEX, DISPLAY_GRID) and plan.name:
            tag = semantic_tag(plan.name)
            if tag is not None:
                return tag
        return "div"


def collect_component_refs(
    root: VNode,
    plan: LayoutNodePlan,
) -> List[Tuple[str, VNode, LayoutNodePlan]]:
    """Collect unique component references in the tree (Part 21).

    Returns ``[(name, vnode, plan_node), ...]`` for every ``is_component``
    vnode, deduplicated by tag name (first occurrence wins, tree order —
    deterministic).  Web backends use this to emit a self-contained local
    fallback definition for each referenced name, so generated output
    compiles and renders even when the user's component library is absent.
    """
    seen: Dict[str, Tuple[VNode, LayoutNodePlan]] = {}

    def _walk(v: VNode, p: LayoutNodePlan) -> None:
        if v.is_component and v.tag not in seen:
            seen[v.tag] = (v, p)
        for cv, cp in zip(v.children, p.children):
            _walk(cv, cp)

    _walk(root, plan)
    return [(name, v, p) for name, (v, p) in seen.items()]


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------

def camel_to_kebab(s: str) -> str:
    return "".join(f"-{c.lower()}" if c.isupper() else c for c in s)


# ---------------------------------------------------------------------------
# Scoped CSS collector (shared by the vue / svelte backends)
# ---------------------------------------------------------------------------

def _fmt_num(value: Any) -> str:
    """Format a number without trailing ``.0`` noise (24.0 -> '24')."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _clean_value(value: str) -> str:
    """Normalize ``24.0px`` -> ``24px`` and ``50.00%`` -> ``50%``."""
    if value.endswith(".0px"):
        return value[:-4] + "px"
    if value.endswith(".00%"):
        return value[:-4] + "%"
    return value


def _hex6(color: Any) -> str:
    """6-digit hex ``#rrggbb`` for an IRColor (drops alpha when opaque)."""
    h = color.to_hex()
    alpha = getattr(color, "a", None)
    if alpha is None or alpha >= 0.999:
        return h[:-2]
    return h


def _rgba(color: Any) -> str:
    """``rgba(r, g, b, a)`` string for an IRColor."""
    def _byte(v: Any) -> int:
        return max(0, min(255, int(round((v if v is not None else 0.0) * 255))))
    alpha = getattr(color, "a", None)
    a = 1.0 if alpha is None else alpha
    return f"rgba({_byte(color.r)}, {_byte(color.g)}, {_byte(color.b)}, {_fmt_num(a)})"


def reference_styles_from_plan(
    document: Any,
    layout_plan: Any,
) -> Dict[str, VStyle]:
    """Compute the intended per-node VStyles from a LayoutPlan (Part 19).

    Walks every screen tree and lowers each node through the SAME shared
    machinery the html_css backend uses (``CssStyleGenerator.generate_style``
    + ``extend_ir_style``), keyed by node id.  The result feeds
    ``core.render_html.generate_render_html(document, styles, viewport)`` to
    produce the *reference render* — the baseline ``figmaforge run`` diffs
    generated output against.  Reusing the shared lowering keeps the
    baseline and the generated code on one style rule, so a clean verdict
    measures codegen fidelity, not style drift.
    """
    ir_by_id = {n.id: n for n in document.all_nodes()}
    style_gen = CssStyleGenerator()
    styles: Dict[str, VStyle] = {}

    def _walk(node_plan: LayoutNodePlan) -> None:
        if node_plan.node_id:
            style = style_gen.generate_style(node_plan)
            extend_ir_style(style, node_plan, ir_by_id.get(node_plan.node_id))
            styles[node_plan.node_id] = style
        for child in node_plan.children:
            _walk(child)

    for screen in layout_plan.screens:
        _walk(screen)
    return styles


def extend_ir_style(
    style: VStyle,
    plan_node: LayoutNodePlan,
    ir: Optional[IRNode],
    assets: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    """Add IR-sourced style (fills/radius/borders/opacity/shadows/blur/typography)
    plus overflow behavior and breakpoint changes to a VStyle.

    Shared by html_css, vue, and svelte so all web backends lower the IR
    style surface identically.  Image fills lower to a real CSS background
    (``url(<path>)`` with Figma's default cover/center fit) when the node has
    a resolved asset in ``assets`` (``options["assets"]`` from the assets
    stage); otherwise they degrade to a named fallback (the calling backend
    emits the ``fidelity:`` marker in its markup) — never silent.
    Breakpoints fold into ``style.breakpoints`` for ``@media`` emission.
    """
    if ir is not None and ir.style is not None:
        s = ir.style
        for fill in s.fills:
            if not fill.visible or fill.kind == "none":
                continue
            if fill.kind == "solid" and fill.color is not None:
                # Figma text fills are foreground color, not a painted box.
                style.base["color" if ir.kind == "text" else "background"] = _hex6(fill.color)
                break
            if fill.kind == "gradient" and fill.gradient_stops:
                stops = ", ".join(
                    f"{_hex6(st.color)} {_fmt_num(st.position * 100)}%"
                    for st in fill.gradient_stops
                    if st.color is not None
                )
                direction = "to bottom"
                if len(fill.gradient_handles) >= 2:
                    start, end = fill.gradient_handles[0], fill.gradient_handles[1]
                    dx = end["x"] - start["x"]
                    dy = end["y"] - start["y"]
                    if abs(dx) + abs(dy) > 1e-6:
                        angle = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0
                        direction = f"{_fmt_num(angle)}deg"
                style.base["background"] = f"linear-gradient({direction}, {stops})"
                break
            if fill.kind == "image":
                asset = (assets or {}).get(ir.id) if ir is not None else None
                if asset and asset.get("path"):
                    style.base["backgroundImage"] = f"url({asset['path']})"
                    style.base["backgroundSize"] = "cover"
                    style.base["backgroundPosition"] = "center"
                    transform = fill.image_transform or {}
                    if transform.get("cssBackgroundSize"):
                        style.base["backgroundSize"] = str(transform["cssBackgroundSize"])
                    if transform.get("cssBackgroundPosition"):
                        style.base["backgroundPosition"] = str(transform["cssBackgroundPosition"])
                else:
                    # unresolved image fill -> named fallback, never silent
                    style.base["background"] = "#f0f0f0"
                break

        if s.radius is not None:
            style.base["borderRadius"] = f"{_fmt_num(s.radius)}px"
        elif s.corner_radii:
            radii = s.corner_radii
            if len(radii) == 4 and len(set(radii)) == 1:
                style.base["borderRadius"] = f"{_fmt_num(radii[0])}px"
            else:
                for css_prop, r in zip(
                    ("borderTopLeftRadius", "borderTopRightRadius",
                     "borderBottomRightRadius", "borderBottomLeftRadius"),
                    radii,
                ):
                    if r is not None:
                        style.base[css_prop] = f"{_fmt_num(r)}px"

        for border in s.borders:
            if border.visible and border.weight is not None and border.color is not None:
                style.base["border"] = (
                    f"{_fmt_num(border.weight)}px solid {_hex6(border.color)}"
                )
                break

        if s.opacity is not None and s.opacity < 1.0:
            style.base["opacity"] = _fmt_num(s.opacity)
        if ir.opacity < 1.0:
            style.base["opacity"] = _fmt_num(ir.opacity)

        for shadow in s.shadows:
            if not shadow.visible or shadow.color is None:
                continue
            spread = (
                f" {_fmt_num(shadow.spread)}px"
                if shadow.spread
                else ""
            )
            style.base["boxShadow"] = (
                f"{_fmt_num(shadow.x)}px {_fmt_num(shadow.y)}px "
                f"{_fmt_num(shadow.blur)}px{spread} "
                f"{_rgba(shadow.color)}"
            )
            break

        for blur in s.blurs:
            if blur.visible and blur.radius:
                style.base["filter"] = f"blur({_fmt_num(blur.radius)}px)"
                break

    if ir is not None and ir.typography is not None:
        t = ir.typography
        if t.font_size is not None:
            style.base["fontSize"] = f"{_fmt_num(t.font_size)}px"
        if t.font_weight is not None:
            style.base["fontWeight"] = _fmt_num(t.font_weight)
        if t.font_family:
            style.base["fontFamily"] = t.font_family
        if t.line_height is not None:
            style.base["lineHeight"] = f"{_fmt_num(t.line_height)}px"
        if t.letter_spacing is not None:
            style.base["letterSpacing"] = f"{_fmt_num(t.letter_spacing)}px"
        if t.text_align:
            style.base["textAlign"] = {
                "LEFT": "left", "CENTER": "center", "RIGHT": "right",
            }.get(t.text_align, "left")
        if t.text_decoration:
            style.base["textDecoration"] = {
                "UNDERLINE": "underline",
                "STRIKETHROUGH": "line-through",
            }.get(t.text_decoration.upper())
        if t.text_case:
            style.base["textTransform"] = {
                "UPPER": "uppercase",
                "LOWER": "lowercase",
                "TITLE": "capitalize",
            }.get(t.text_case.upper())

    # Overflow behavior (clip / scroll) is representable in CSS.
    if plan_node.overflow is not None:
        if plan_node.overflow.x == OVERFLOW_CLIP or plan_node.overflow.y == OVERFLOW_CLIP:
            style.base["overflow"] = "hidden"
        elif plan_node.overflow.x == OVERFLOW_SCROLL or plan_node.overflow.y == OVERFLOW_SCROLL:
            style.base["overflow"] = "auto"

    # Breakpoints -> @media rules.
    for bp in plan_node.breakpoints:
        mapped = bp_to_css_prop(bp)
        if mapped is None:
            continue
        css_prop, value = mapped
        style.breakpoints.setdefault(f"{_fmt_num(bp.width)}px", {})[css_prop] = value


def reference_styles_from_plan(
    document: Any,
    layout_plan: Any,
    assets: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, VStyle]:
    """Compute the intended per-node VStyles from a LayoutPlan (Part 20).

    Walks every screen tree and lowers each node through the SAME shared
    machinery the html_css backend uses (``CssStyleGenerator.generate_style``
    + ``extend_ir_style``), keyed by node id.  The result feeds
    ``core.render_html.generate_render_html(document, styles, viewport)`` to
    produce the *reference render* — and, in the repair stage, is the style
    layer the loop mutates.  Reusing the shared lowering keeps the reference
    and the generated code on one style rule.

    ``assets`` (Part 22) threads the run's resolved asset paths into the
    image-fill lowering so the style layer matches what the backends compute
    WITH assets — the repair-loop ``styles_override`` serialization of an
    un-repaired image node then carries the real ``backgroundImage`` (an
    idempotent union) instead of the unresolved-fill fallback color.
    """
    ir_by_id = {n.id: n for n in document.all_nodes()}
    style_gen = CssStyleGenerator()
    styles: Dict[str, VStyle] = {}

    def _walk(node_plan: LayoutNodePlan) -> None:
        if node_plan.node_id:
            style = style_gen.generate_style(node_plan)
            extend_ir_style(
                style, node_plan, ir_by_id.get(node_plan.node_id), assets=assets,
            )
            styles[node_plan.node_id] = style
        for child in node_plan.children:
            _walk(child)

    for screen in layout_plan.screens:
        _walk(screen)
    return styles


def styles_to_dict(styles: Dict[str, VStyle]) -> Dict[str, Dict[str, Any]]:
    """Serialize VStyles (base + breakpoints) for the generate
    ``styles_override`` seam (Part 20): ``{node_id: {base, breakpoints}}``.
    """
    return {
        node_id: {
            "base": dict(style.base),
            "breakpoints": {
                bp: dict(props) for bp, props in style.breakpoints.items()
            },
        }
        for node_id, style in styles.items()
    }


class ScopedCssGenerator:
    """Collect per-node scoped CSS rules + ``@media`` breakpoint rules.

    Shared by the vue and svelte backends: builds on :class:`CssStyleGenerator`
    for layout props, extends each node's style with IR-sourced fills/radius/
    opacity/typography, preserves absolute positioning, and folds
    LayoutPlan breakpoint changes into ``@media (max-width: …)`` rules.
    """

    def __init__(
        self,
        ir_by_id: Dict[str, IRNode],
        assets: Optional[Dict[str, Dict[str, Any]]] = None,
        overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        self._ir_by_id = ir_by_id
        self._assets = assets
        self._overrides = overrides or {}
        self._style_gen = CssStyleGenerator()

    def collect(
        self,
        vnode: VNode,
        plan_node: LayoutNodePlan,
    ) -> Tuple[List[str], Dict[str, List[Tuple[str, str]]]]:
        """Return (rule_lines, media_rules) for the whole subtree."""
        rules: List[str] = []
        media: Dict[str, List[Tuple[str, str]]] = {}
        self._node(vnode, plan_node, rules, media)
        for child_vn, child_plan in zip(vnode.children, plan_node.children):
            child_rules, child_media = self.collect(child_vn, child_plan)
            rules.extend(child_rules)
            for bp_width, entries in child_media.items():
                media.setdefault(bp_width, []).extend(entries)
        return rules, media

    def _node(
        self,
        vnode: VNode,
        plan_node: LayoutNodePlan,
        rules: List[str],
        media: Dict[str, List[Tuple[str, str]]],
    ) -> None:
        style = self._style_gen.generate_style(plan_node)
        self._extend(style, plan_node, self._ir_by_id.get(vnode.node_id))

        # Repair override (Part 22): the html_css ``styles_override`` union —
        # ``{node_id: {base, breakpoints}}`` applied ON TOP of the computed
        # style.  Must run BEFORE the absolute-position pop below: the
        # serialized layer carries ``position: absolute`` for absolute nodes
        # (generate_style adds it), so applying the union after the pop would
        # re-attach absolute positioning (design review F6).  Absent/empty
        # override is a no-op (byte-identical output).
        override = self._overrides.get(vnode.node_id) or {}
        if override.get("base"):
            style.base.update(override["base"])
        if override.get("breakpoints"):
            style.breakpoints.update(override["breakpoints"])

        selector = f".n-{vnode.node_id.replace(':', '-')}"
        if style.base:
            props = "; ".join(
                f"{camel_to_kebab(k)}: {_clean_value(v)}"
                for k, v in style.base.items()
            )
            rules.append(f"{selector} {{ {props} }}")

        for bp_width, bp_styles in style.breakpoints.items():
            props = "; ".join(
                f"{camel_to_kebab(k)}: {_clean_value(v)}"
                for k, v in bp_styles.items()
            )
            media.setdefault(bp_width, []).append((selector, props))

    def _extend(
        self,
        style: VStyle,
        plan_node: LayoutNodePlan,
        ir: Optional[IRNode],
    ) -> None:
        """Add IR-sourced style (fills/radius/opacity/typography) + breakpoints."""
        extend_ir_style(style, plan_node, ir, assets=self._assets)


def _has_absolute_descendant(plan: LayoutNodePlan) -> bool:
    return any(
        child.display == DISPLAY_ABSOLUTE or _has_absolute_descendant(child)
        for child in plan.children
    )


# Breakpoint props whose ``after`` value is a length in px (not a keyword
# like ``row``/``column``) and so must carry a unit in emitted CSS.
_PX_BP_PROPS = frozenset({
    "gap", "width", "height",
    "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
})


def bp_to_css_prop(bp: Any) -> Optional[Tuple[str, str]]:
    """Map a LayoutPlan breakpoint change to ``(camelCase css prop, value)``.

    Returns None for properties the web backends do not lower.  Shared by the
    vue/svelte scoped-CSS backends and the react_tailwind class mapper so all
    web backends agree on breakpoint semantics.
    """
    prop_map = {
        "direction": "flexDirection",
        "gap": "gap",
        "width": "width",
        "height": "height",
        "paddingTop": "paddingTop",
        "paddingRight": "paddingRight",
        "paddingBottom": "paddingBottom",
        "paddingLeft": "paddingLeft",
    }
    css_prop = prop_map.get(getattr(bp, "property", ""))
    after = getattr(bp, "after", None)
    if css_prop is None or after is None:
        return None
    if css_prop in _PX_BP_PROPS and isinstance(after, (int, float)):
        return (css_prop, f"{_fmt_num(after)}px")
    return (css_prop, str(after))


def escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
