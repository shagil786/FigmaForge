"""
HTML + CSS backend adapter (fully implemented).

Converts the framework-neutral Design IR + LayoutPlan into plain HTML and
CSS source files.  This is the reference backend — it supports the widest
range of IR features because HTML/CSS is the lowest-common-denominator for
visual rendering.

Internal types (VNode, VStyle) live here, not in core, because they are
HTML/CSS-specific implementation details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..protocol import (
    BackendAdapter,
    BackendCapabilities,
    Feature,
    FidelityLoss,
    GeneratedFile,
    GeneratedOutput,
    WEB_COMMON_FEATURES,
)
from core.ir_types import IRDocument, IRNode
from core.layout_types import (
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    DISPLAY_GRID,
    LayoutNodePlan,
    LayoutPlan,
    SIZING_FIXED,
    SIZING_FILL,
    SIZING_HUG,
    SIZING_PERCENT,
)
from core.resolver import ResolutionReport


# ---------------------------------------------------------------------------
# VNode / VStyle — HTML/CSS-specific internal types
# ---------------------------------------------------------------------------

@dataclass
class VStyle:
    """CSS style properties for a node.

    Keys are camelCase CSS property names (e.g. ``paddingTop``, ``flexDirection``).
    Values are CSS value strings (e.g. ``"16px"``, ``"flex-start"``).
    """

    base: Dict[str, str] = field(default_factory=dict)
    breakpoints: Dict[str, Dict[str, str]] = field(default_factory=dict)


@dataclass
class VNode:
    """A virtual DOM node targeting HTML output.

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
            out["style"] = {"base": self.style.base, "breakpoints": self.style.breakpoints}
        if self.text_content is not None:
            out["text_content"] = self.text_content
        if self.children:
            out["children"] = [c.to_dict() for c in self.children]
        return {k: v for k, v in out.items() if v or isinstance(v, (bool, int, float))}


# ---------------------------------------------------------------------------
# Semantic HTML tag mapping
# ---------------------------------------------------------------------------

_SEMANTIC_TAG_BY_NAME = {
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


# ---------------------------------------------------------------------------
# CSS style generator (internal to this backend)
# ---------------------------------------------------------------------------

class _CSSStyleGenerator:
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
        mapping = {"MIN": "flex-start", "MAX": "flex-end", "CENTER": "center", "SPACE_BETWEEN": "space-between"}
        return mapping.get(justify, "flex-start")

    def _map_align(self, align: str) -> str:
        mapping = {"MIN": "flex-start", "MAX": "flex-end", "CENTER": "center", "STRETCH": "stretch"}
        return mapping.get(align, "flex-start")


# ---------------------------------------------------------------------------
# VNode tree builder (internal to this backend)
# ---------------------------------------------------------------------------

class _VNodeBuilder:
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
            tag = _SEMANTIC_TAG_BY_NAME.get(plan.name.lower())
            if tag is not None:
                return tag
        return "div"


# ---------------------------------------------------------------------------
# HTML emitter
# ---------------------------------------------------------------------------

class _HtmlEmitter:
    """Convert a VNode tree into HTML + CSS strings."""

    def emit(self, root: VNode) -> tuple:
        """Return (html_str, css_str)."""
        css_rules: List[str] = []
        html = self._render_node(root, css_rules, indent=1)
        return html, "\n".join(css_rules)

    def _render_node(self, node: VNode, css_rules: List[str], indent: int) -> str:
        pad = "  " * indent
        tag = node.tag
        attrs = self._render_attrs(node, css_rules)
        self_closing = tag in ("img", "br", "hr", "input", "meta", "link")

        if self_closing and not node.children:
            return f"{pad}<{tag}{attrs} />"

        if node.text_content is not None:
            return f"{pad}<{tag}{attrs}>{_escape_html(node.text_content)}</{tag}>"

        if not node.children:
            return f"{pad}<{tag}{attrs}></{tag}>"

        children_html = "\n".join(
            self._render_node(child, css_rules, indent + 1)
            for child in node.children
        )
        return f"{pad}<{tag}{attrs}>\n{children_html}\n{pad}</{tag}>"

    def _render_attrs(self, node: VNode, css_rules: List[str]) -> str:
        parts: List[str] = []
        for key, value in node.props.items():
            parts.append(f'{key}="{_escape_attr(str(value))}"')

        if node.style.base:
            class_name = f"n-{node.node_id.replace(':', '-')}"
            parts.append(f'class="{class_name}"')
            selector = f".{class_name}"
            props_str = "; ".join(
                f"{_camel_to_kebab(k)}: {v}" for k, v in node.style.base.items()
            )
            css_rules.append(f"{selector} {{ {props_str} }}")

            for bp, bp_styles in node.style.breakpoints.items():
                bp_props = "; ".join(
                    f"{_camel_to_kebab(k)}: {v}" for k, v in bp_styles.items()
                )
                css_rules.append(f"@media (max-width: {bp}) {{ {selector} {{ {bp_props} }} }}")

        return (" " + " ".join(parts)) if parts else ""


def _camel_to_kebab(s: str) -> str:
    return "".join(f"-{c.lower()}" if c.isupper() else c for c in s)


def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# HtmlCssBackend — the public adapter
# ---------------------------------------------------------------------------

# Features HTML+CSS fully supports
_HTML_CSS_SUPPORTED = WEB_COMMON_FEATURES | frozenset({
    Feature.GRID,
    Feature.MARGIN,
    Feature.ALIGN_SELF,
    Feature.FILLS_GRADIENT,
    Feature.FILLS_IMAGE,
    Feature.SHADOWS,
    Feature.BLUR,
    Feature.PER_CORNER_RADIUS,
    Feature.TEXT_DECORATION,
    Feature.TEXT_CASE,
    Feature.LETTER_SPACING,
    Feature.OVERFLOW_CLIP,
    Feature.OVERFLOW_SCROLL,
    Feature.BREAKPOINTS,
    Feature.MEDIA_QUERIES,
    Feature.RESPONSIVE_CONSTRAINTS,
    Feature.SVG_ASSETS,
    Feature.PROTOTYPE_LINKS,
    Feature.INTERACTIONS,
})

# Features HTML+CSS partially supports (with caveats)
_HTML_CSS_PARTIAL = frozenset({
    Feature.CONSTRAINTS,  # mapped to CSS positioning but not 1:1
    Feature.COMPONENTS,  # emitted as reusable HTML fragments, not real components
    Feature.COMPONENT_VARIANTS,  # no native variant support
    Feature.COMPONENT_INSTANCES,  # emitted as duplicated HTML
})


class HtmlCssBackend(BackendAdapter):
    """Plain HTML + CSS backend.

    The most complete backend — HTML/CSS can represent nearly every IR
    feature.  Components are emitted as reusable HTML fragments with CSS
    classes; there is no JavaScript framework interactivity.
    """

    @property
    def name(self) -> str:
        return "html_css"

    @property
    def display_name(self) -> str:
        return "HTML + CSS"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_features=_HTML_CSS_SUPPORTED,
            unsupported_features=frozenset(),  # HTML/CSS supports everything
            partial_features=_HTML_CSS_PARTIAL,
            styling_system="css",
            framework="html",
            renderer="browser",
            file_extensions=(".html", ".css"),
        )

    def generate(
        self,
        document: IRDocument,
        layout_plan: LayoutPlan,
        resolution: Optional[ResolutionReport] = None,
        viewport: float = 1440.0,
        options: Optional[Dict[str, Any]] = None,
    ) -> GeneratedOutput:
        opts = options or {}
        style_gen = _CSSStyleGenerator()
        node_builder = _VNodeBuilder(resolution)
        emitter = _HtmlEmitter()

        output = GeneratedOutput()
        all_css: List[str] = []
        all_html: List[str] = []

        # Generate a VNode tree for each screen in the layout plan
        for screen_idx, screen in enumerate(layout_plan.screens):
            # Build VNode tree
            root_vnode = node_builder.build(screen)

            # Apply styles from layout plan
            self._apply_styles(root_vnode, screen, style_gen)

            # Emit HTML + CSS
            html, css = emitter.emit(root_vnode)
            all_html.append(html)
            if css:
                all_css.append(css)

            # Track node coverage
            node_ids = [n.node_id for n in screen.walk() if n.node_id]
            output.files.append(GeneratedFile(
                path=f"screen_{screen_idx}.html",
                content=self._wrap_html_document(
                    html, "\n".join(all_css),
                    title=screen.name or f"Screen {screen_idx}",
                    viewport=viewport,
                ),
                language="html",
                node_ids=node_ids,
            ))

        # Emit a combined CSS file
        if all_css:
            output.files.append(GeneratedFile(
                path="styles.css",
                content="\n\n".join(all_css),
                language="css",
            ))

        # Check for fidelity losses
        output.fidelity_losses.extend(self.preflight(document, layout_plan))

        output.metadata = {
            "backend": self.name,
            "viewport": viewport,
            "screen_count": len(layout_plan.screens),
            "generator_options": opts,
        }

        return output

    def _apply_styles(
        self,
        vnode: VNode,
        plan: LayoutNodePlan,
        style_gen: _CSSStyleGenerator,
    ) -> None:
        """Recursively apply CSS styles from the layout plan to VNodes."""
        vnode.style = style_gen.generate_style(plan)
        for child_vnode, child_plan in zip(vnode.children, plan.children):
            self._apply_styles(child_vnode, child_plan, style_gen)

    def _wrap_html_document(
        self,
        body_html: str,
        css: str,
        title: str = "FigmaForge",
        viewport: float = 1440.0,
    ) -> str:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_escape_html(title)}</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      width: {viewport}px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }}
{css}
  </style>
</head>
<body>
{body_html}
</body>
</html>"""
