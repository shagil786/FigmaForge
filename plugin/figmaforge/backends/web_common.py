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

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.layout_types import (
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    DISPLAY_GRID,
    LayoutNodePlan,
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
    "header": "header",
    "nav": "nav",
    "hero": "section",
    "main": "main",
    "content": "main",
    "section": "section",
    "card": "section",
    "aside": "aside",
    "footer": "footer",
}


def semantic_tag(name: str) -> Optional[str]:
    """Map a node name to a semantic HTML tag, or None."""
    return SEMANTIC_TAG_BY_NAME.get(name.lower())


# ---------------------------------------------------------------------------
# CSS style generator (shared by every web backend)
# ---------------------------------------------------------------------------

class CssStyleGenerator:
    """Convert a LayoutNodePlan into CSS properties."""

    def generate_style(self, plan: LayoutNodePlan) -> VStyle:
        style = VStyle()
        display = self._map_display(plan)
        style.base["display"] = display

        if plan.box:
            self._apply_sizing(style, plan, display)

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


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------

def camel_to_kebab(s: str) -> str:
    return "".join(f"-{c.lower()}" if c.isupper() else c for c in s)


def escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
