"""
React + Tailwind CSS backend adapter (Part 14).

Converts the framework-neutral Design IR + LayoutPlan into React functional
components (TSX) styled with deterministic Tailwind utility classes.  Reuses
the shared web machinery (``web_common.VNodeBuilder`` + ``CssStyleGenerator``)
for structure and layout styles, then lowers every CSS property to a Tailwind
class using **arbitrary values for exactness** (``bg-[#3366cc]``,
``w-[120px]``, ``rounded-[8px]``, ``pt-[24px]``, ``text-[14px]``) with
standard classes only where exact (``flex``, ``flex-col``, ``items-center``,
``justify-between``).  Breakpoints become ``max-[{width}px]:`` variants.
Design tokens resolve into a ``tailwind.config.figmaforge.js`` extension.

Fidelity honesty: features this backend cannot represent (e.g. absolute
positioning) are reported by ``preflight`` and degraded with an inline
``{/* fidelity: ... */}`` marker — never silently.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..protocol import (
    BackendAdapter,
    BackendCapabilities,
    Feature,
    FidelityLoss,
    GeneratedFile,
    GeneratedOutput,
    WEB_COMMON_FEATURES,
)
from ..web_common import (
    CssStyleGenerator,
    VNode,
    VNodeBuilder,
    bp_to_css_prop,
    escape_attr,
)
from core.ir_types import IRDocument, IRNode
from core.layout_types import DISPLAY_ABSOLUTE, LayoutNodePlan, LayoutPlan
from core.resolver import ResolutionReport
from core.token_resolver import SemanticToken

# React+Tailwind supports most web features (minus the ones Tailwind itself
# cannot express — subtract them so ``supports()`` reports them honestly).
_REACT_TW_UNSUPPORTED = frozenset({
    Feature.ABSOLUTE_POSITIONING,  # Tailwind has position-absolute but limited
    Feature.RELATIVE_POSITIONING,  # Supported but not idiomatic Tailwind
    Feature.AUTO_LAYOUT,  # React components, not a Tailwind concept
})

_REACT_TW_SUPPORTED = (WEB_COMMON_FEATURES - _REACT_TW_UNSUPPORTED) | frozenset({
    Feature.GRID,
    Feature.FILLS_GRADIENT,
    Feature.FILLS_IMAGE,
    Feature.SHADOWS,
    Feature.CORNER_RADIUS,
    Feature.OPACITY,
    Feature.TEXT_DECORATION,
    Feature.TEXT_WRAP,
    Feature.OVERFLOW_CLIP,
    Feature.OVERFLOW_SCROLL,
    Feature.IMAGE_ASSETS,
    Feature.SVG_ASSETS,
    Feature.DESIGN_TOKENS,
    Feature.TOKEN_REFERENCES,
    Feature.COMPONENTS,
    Feature.COMPONENT_INSTANCES,
    Feature.BREAKPOINTS,
    Feature.RESPONSIVE_CONSTRAINTS,
    Feature.PROTOTYPE_LINKS,
})

# Tailwind doesn't support some CSS features natively
_REACT_TW_PARTIAL = frozenset({
    Feature.PER_CORNER_RADIUS,  # Tailwind supports via arbitrary values
    Feature.BLUR,  # Tailwind has blur utilities but limited
    Feature.MARGIN,  # Tailwind has margin utilities but not auto-inferred
    Feature.ALIGN_SELF,  # Tailwind supports but not all values
    Feature.TEXT_CASE,  # Tailwind supports via utilities
    Feature.LETTER_SPACING,  # Tailwind has limited tracking utilities
    Feature.INTERACTIONS,  # Requires additional React state management
    Feature.MEDIA_QUERIES,  # Tailwind responsive prefixes, not arbitrary
    Feature.COMPONENT_VARIANTS,  # Requires pattern implementation
    Feature.CONSTRAINTS,  # Mapped to Tailwind positioning
})


# Canonical class emission order (deterministic).
_ORDER = [
    "display", "flexDirection", "justifyContent", "alignItems", "alignSelf",
    "flex", "gap", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
    "width", "height", "minWidth", "maxWidth", "minHeight", "maxHeight",
    "position",
]

_WEIGHT_CLASSES = {
    100: "font-thin",
    200: "font-extralight",
    300: "font-light",
    400: "font-normal",
    500: "font-medium",
    600: "font-semibold",
    700: "font-bold",
    800: "font-extrabold",
    900: "font-black",
}

_ALIGN_CLASSES = {
    "LEFT": "text-left",
    "CENTER": "text-center",
    "RIGHT": "text-right",
}


def _fmt_num(value: Any) -> str:
    """Format a number without trailing ``.0`` noise (24.0 -> '24')."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _hex6(color: Any) -> str:
    """6-digit hex ``#rrggbb`` for an IRColor (drops alpha when opaque)."""
    h = color.to_hex()
    alpha = getattr(color, "a", None)
    if alpha is None or alpha >= 0.999:
        return h[:-2]
    return h


def _hex_from_rgba(value: Dict[str, Any]) -> str:
    def _byte(v: Any) -> int:
        return max(0, min(255, int(round(float(v if v is not None else 0.0) * 255))))
    alpha = value.get("a", 1)
    if alpha is None or float(alpha) >= 0.999:
        return "#{:02x}{:02x}{:02x}".format(
            _byte(value.get("r")), _byte(value.get("g")), _byte(value.get("b")),
        )
    return "#{:02x}{:02x}{:02x}{:02x}".format(
        _byte(value.get("r")), _byte(value.get("g")),
        _byte(value.get("b")), _byte(alpha),
    )


def _px_class(prefix: str, value: str) -> Optional[str]:
    """``24.0px`` -> ``pt-[24px]``; ``fit-content`` -> ``w-[fit-content]``."""
    if value.endswith("px"):
        num = value[:-2]
        if num.endswith(".0"):
            num = num[:-2]
        return f"{prefix}-[{num}px]"
    return f"{prefix}-[{value}]"


def _size_class(prefix: str, value: str) -> Optional[str]:
    if value.endswith("%"):
        num = value[:-1]
        try:
            fval = float(num)
            num = _fmt_num(fval)
        except ValueError:
            pass
        return f"{prefix}-[{num}%]"
    return _px_class(prefix, value)


def _css_class(prop: str, value: str) -> Optional[str]:
    """Map one camelCase CSS property + value to a Tailwind class (or None)."""
    if prop == "display":
        return {"flex": "flex", "grid": "grid", "block": "block",
                "none": "hidden"}.get(value)
    if prop == "flexDirection":
        return {"row": "flex-row", "column": "flex-col"}.get(value)
    if prop == "justifyContent":
        return {"flex-start": "justify-start", "flex-end": "justify-end",
                "center": "justify-center", "space-between": "justify-between",
                "stretch": "justify-stretch"}.get(value)
    if prop == "alignItems":
        return {"flex-start": "items-start", "flex-end": "items-end",
                "center": "items-center", "stretch": "items-stretch"}.get(value)
    if prop == "alignSelf":
        return {"stretch": "self-stretch", "flex-start": "self-start",
                "flex-end": "self-end", "center": "self-center"}.get(value)
    if prop == "flex" and value == "1 1 0%":
        return "flex-1"
    if prop == "gap":
        return _px_class("gap", value)
    if prop in ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft"):
        return _px_class(
            {"paddingTop": "pt", "paddingRight": "pr",
             "paddingBottom": "pb", "paddingLeft": "pl"}[prop],
            value,
        )
    if prop == "width":
        return _size_class("w", value)
    if prop == "height":
        return _size_class("h", value)
    if prop in ("minWidth", "maxWidth", "minHeight", "maxHeight"):
        prefix = {"minWidth": "min-w", "maxWidth": "max-w",
                  "minHeight": "min-h", "maxHeight": "max-h"}[prop]
        return _px_class(prefix, value)
    if prop == "position" and value == "relative":
        return "relative"
    return None  # unmapped (e.g. absolute positioning) -> handled via markers


def _breakpoint_class(bp: Any) -> Optional[str]:
    """LayoutPlan breakpoint change -> ``max-[{width}px]:{class}`` variant."""
    mapped = bp_to_css_prop(bp)
    if mapped is None:
        return None
    css_prop, value = mapped
    cls = _css_class(css_prop, value)
    if cls is None:
        return None
    return f"max-[{_fmt_num(bp.width)}px]:{cls}"


class ReactTailwindBackend(BackendAdapter):
    """React + Tailwind CSS backend.

    Generates React functional components (TSX) with Tailwind utility
    classes for styling.  Components map to React elements; design tokens
    map to a Tailwind config extension.
    """

    @property
    def name(self) -> str:
        return "react_tailwind"

    @property
    def display_name(self) -> str:
        return "React + Tailwind"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_features=_REACT_TW_SUPPORTED,
            unsupported_features=_REACT_TW_UNSUPPORTED,
            partial_features=_REACT_TW_PARTIAL,
            styling_system="tailwind",
            framework="react",
            renderer="browser",
            file_extensions=(".tsx",),
        )

    def generate(
        self,
        document: IRDocument,
        layout_plan: LayoutPlan,
        resolution: Optional[ResolutionReport] = None,
        viewport: float = 1440.0,
        options: Optional[Dict[str, Any]] = None,
    ) -> GeneratedOutput:
        output = GeneratedOutput()
        opts = options or {}
        ir_by_id: Dict[str, IRNode] = {n.id: n for n in document.all_nodes()}
        style_gen = CssStyleGenerator()
        node_builder = VNodeBuilder(resolution)

        # Generate one React component per screen.
        for screen_idx, screen in enumerate(layout_plan.screens):
            component_name = _to_pascal_case(screen.name or f"Screen{screen_idx}")
            root_vnode = node_builder.build(screen)
            tsx_content = self._render_component(
                root_vnode, screen, component_name, style_gen, ir_by_id,
            )
            node_ids = [n.node_id for n in screen.walk() if n.node_id]

            output.files.append(GeneratedFile(
                path=f"{component_name}.tsx",
                content=tsx_content,
                language="tsx",
                node_ids=node_ids,
            ))

        # Tailwind config extension from real design tokens.
        output.files.append(GeneratedFile(
            path="tailwind.config.figmaforge.js",
            content=self._generate_tailwind_config(document, resolution),
            language="javascript",
        ))

        # Report fidelity losses.
        output.fidelity_losses.extend(self.preflight(document, layout_plan))

        output.metadata = {
            "backend": self.name,
            "viewport": viewport,
            "screen_count": len(layout_plan.screens),
            "options": opts,
        }
        return output

    # ------------------------------------------------------------------ emit

    def _render_component(
        self,
        root_vnode: VNode,
        screen: LayoutNodePlan,
        name: str,
        style_gen: CssStyleGenerator,
        ir_by_id: Dict[str, IRNode],
    ) -> str:
        lines = ["import React from 'react';", ""]
        lines.append(
            f"export function {name}({{ className = '' }}: {{ className?: string }}) {{"
        )
        lines.append("  return (")
        lines.append(self._render_node(root_vnode, screen, style_gen, ir_by_id, indent=3))
        lines.append("  );")
        lines.append("}")
        lines.append("")
        lines.append(f"export default {name};")
        return "\n".join(lines) + "\n"

    def _render_node(
        self,
        vnode: VNode,
        plan_node: LayoutNodePlan,
        style_gen: CssStyleGenerator,
        ir_by_id: Dict[str, IRNode],
        indent: int,
    ) -> str:
        pad = "  " * indent
        ir = ir_by_id.get(vnode.node_id)
        classes, markers = self._classes_for(vnode, plan_node, style_gen, ir)

        attrs: List[str] = []
        if vnode.node_id:
            attrs.append(f'data-figma-id="{escape_attr(vnode.node_id)}"')
        for key, value in vnode.props.items():
            if key == "data-figma-id":
                continue
            attrs.append(f'{key}="{escape_attr(str(value))}"')
        if classes:
            attrs.append(f'className="{escape_attr(" ".join(classes))}"')
        attr_str = (" " + " ".join(attrs)) if attrs else ""

        if vnode.text_content is not None and not vnode.children:
            element = f"{pad}<{vnode.tag}{attr_str}>{_escape_jsx(vnode.text_content)}</{vnode.tag}>"
        elif vnode.children:
            children_html = "\n".join(
                self._render_node(child_vn, child_plan, style_gen, ir_by_id, indent + 1)
                for child_vn, child_plan in zip(vnode.children, plan_node.children)
            )
            element = f"{pad}<{vnode.tag}{attr_str}>\n{children_html}\n{pad}</{vnode.tag}>"
        else:
            element = f"{pad}<{vnode.tag}{attr_str}></{vnode.tag}>"

        if markers:
            marker_lines = "\n".join(f"{pad}{{/* {m} */}}" for m in markers)
            return f"{marker_lines}\n{element}"
        return element

    def _classes_for(
        self,
        vnode: VNode,
        plan_node: LayoutNodePlan,
        style_gen: CssStyleGenerator,
        ir: Optional[IRNode],
    ) -> Tuple[List[str], List[str]]:
        """Return (classes, fidelity markers) for one node."""
        classes: List[str] = []
        markers: List[str] = []
        style = style_gen.generate_style(plan_node)

        for prop in _ORDER:
            value = style.base.get(prop)
            if value is None:
                continue
            cls = _css_class(prop, value)
            if cls is None:
                continue
            classes.append(cls)

        if plan_node.display == DISPLAY_ABSOLUTE or style.base.get("position") == "absolute":
            markers.append("fidelity: absolute_positioning approximated (in-flow)")

        if ir is not None:
            self._ir_style_classes(ir, classes, markers)
            self._ir_typography_classes(ir, classes)

        for bp in plan_node.breakpoints:
            cls = _breakpoint_class(bp)
            if cls is not None:
                classes.append(cls)

        return classes, markers

    def _ir_style_classes(
        self,
        ir: IRNode,
        classes: List[str],
        markers: List[str],
    ) -> None:
        """Solid fills, borders, radius, and opacity from the IR node."""
        if ir.style is None:
            return
        style = ir.style

        for fill in style.fills:
            if not fill.visible or fill.kind == "none":
                continue
            if fill.kind == "solid" and fill.color is not None:
                classes.append(f"bg-[{_hex6(fill.color)}]")
                break
            if fill.kind == "gradient" and fill.gradient_stops:
                first = fill.gradient_stops[0].color
                if first is not None:
                    classes.append(f"bg-[{_hex6(first)}]")
                markers.append("fidelity: fills_gradient approximated (solid fallback)")
                break
            markers.append(f"fidelity: {fill.kind}_fill approximated (omitted)")
            break

        if style.radius is not None:
            classes.append(f"rounded-[{_fmt_num(style.radius)}px]")
        elif style.corner_radii:
            radii = style.corner_radii
            if len(radii) == 4 and len(set(radii)) == 1:
                classes.append(f"rounded-[{_fmt_num(radii[0])}px]")
            else:
                for corner, r in zip(
                    ("rounded-tl", "rounded-tr", "rounded-br", "rounded-bl"),
                    radii,
                ):
                    if r is not None:
                        classes.append(f"{corner}-[{_fmt_num(r)}px]")

        for border in style.borders:
            if border.visible and border.weight is not None and border.color is not None:
                classes.append(f"border-[{_fmt_num(border.weight)}px]")
                classes.append(f"border-[{_hex6(border.color)}]")
                break

        if style.opacity is not None and style.opacity < 1.0:
            classes.append(f"opacity-[{_fmt_num(style.opacity)}]")
        if ir.opacity < 1.0:
            classes.append(f"opacity-[{_fmt_num(ir.opacity)}]")

    def _ir_typography_classes(self, ir: IRNode, classes: List[str]) -> None:
        if ir.typography is None:
            return
        t = ir.typography
        if t.font_size is not None:
            classes.append(f"text-[{_fmt_num(t.font_size)}px]")
        if t.font_weight is not None:
            weight = int(round(float(t.font_weight)))
            classes.append(_WEIGHT_CLASSES.get(weight, f"font-[{_fmt_num(t.font_weight)}]"))
        if t.font_family:
            classes.append(f"font-['{t.font_family.replace(' ', '_')}']")
        if t.line_height is not None:
            classes.append(f"leading-[{_fmt_num(t.line_height)}px]")
        if t.letter_spacing is not None:
            classes.append(f"tracking-[{_fmt_num(t.letter_spacing)}px]")
        if t.text_align:
            classes.append(_ALIGN_CLASSES.get(t.text_align, "text-left"))

    # ---------------------------------------------------------------- tokens

    def _generate_tailwind_config(
        self,
        document: IRDocument,
        resolution: Optional[ResolutionReport] = None,
    ) -> str:
        semantic: List[SemanticToken] = []
        if resolution is not None and resolution.tokens is not None:
            semantic = [t for t in resolution.tokens.semantic if t.resolved]
        else:
            semantic = self._tokens_from_ir(document)

        colors: Dict[str, str] = {}
        spacing: Dict[str, str] = {}
        families: Dict[str, str] = {}
        for token in semantic:
            if token.category == "color" and isinstance(token.value, dict):
                colors[token.name] = _hex_from_rgba(token.value)
            elif token.category == "spacing" and isinstance(token.value, (int, float)):
                spacing[token.name] = f"{_fmt_num(token.value)}px"
            elif token.category == "typography" and isinstance(token.value, dict):
                family = token.value.get("family")
                if family:
                    families[token.name] = str(family)

        def _entries(mapping: Dict[str, str], quote: str = '"') -> str:
            if not mapping:
                return "      // no tokens resolved"
            lines = []
            for key in sorted(mapping):
                lines.append(f"        {key}: {quote}{mapping[key]}{quote},")
            return "\n".join(lines)

        return f"""\
// FigmaForge generated Tailwind config extension
// Merge this into your tailwind.config.js

module.exports = {{
  theme: {{
    extend: {{
      // Design tokens from Figma file: {document.file_key}
      colors: {{
{_entries(colors)}
      }},
      spacing: {{
{_entries(spacing)}
      }},
      fontFamily: {{
{_entries(families, "'")}
      }},
    }},
  }},
}};
"""

    def _tokens_from_ir(self, document: IRDocument) -> List[SemanticToken]:
        """Fallback token source when no resolution report is available."""
        out: List[SemanticToken] = []
        for var in document.variables.values():
            vt = (var.resolved_type or "").upper()
            if vt == "COLOR" and isinstance(var.value, dict):
                out.append(SemanticToken(
                    key=f"color/{var.name}", category="color", name=var.name,
                    value=var.value, source=f"figma:variable:{var.key}",
                ))
            elif vt in ("FLOAT", "WIDTH", "HEIGHT", "SPACING") and isinstance(
                var.value, (int, float)
            ):
                out.append(SemanticToken(
                    key=f"spacing/{var.name}", category="spacing", name=var.name,
                    value=var.value, source=f"figma:variable:{var.key}",
                ))
        for style in document.styles.values():
            if style.token_type == "FILL" and isinstance(style.value, dict):
                out.append(SemanticToken(
                    key=f"color/{style.name}", category="color", name=style.name,
                    value=style.value, source=f"figma:style:{style.key}",
                ))
        return out


def _escape_jsx(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
    )


def _to_pascal_case(name: str) -> str:
    """Convert a name to PascalCase for React component naming."""
    parts = name.replace("-", " ").replace("_", " ").split()
    return "".join(p.capitalize() for p in parts) if parts else "Component"
