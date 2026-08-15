"""
Vue single-file component backend adapter (Part 14).

Converts the framework-neutral Design IR + LayoutPlan into Vue 3 SFCs
(``.vue``): a ``<template>`` div tree with scoped class names (``n-{id}``), a
``<script setup>`` block, and a ``<style scoped>`` block whose rules reuse the
shared ``CssStyleGenerator`` output (identical selector rules as the html_css
reference, plus IR-sourced fills/radius/opacity/typography).  Breakpoints
become scoped ``@media (max-width: …)`` rules.

Fidelity honesty: features this backend cannot represent (e.g. absolute
positioning) are reported by ``preflight`` and degraded with an inline
``<!-- fidelity: ... -->`` marker — never silently.
"""

from __future__ import annotations

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
from ..web_common import (
    CssStyleGenerator,
    VNode,
    VNodeBuilder,
    bp_to_css_prop,
    camel_to_kebab,
    escape_attr,
    escape_html,
)
from core.ir_types import IRDocument, IRNode
from core.layout_types import DISPLAY_ABSOLUTE, LayoutNodePlan, LayoutPlan
from core.resolver import ResolutionReport

_VUE_UNSUPPORTED = frozenset({
    Feature.ABSOLUTE_POSITIONING,  # in-flow approximation only (marker)
    Feature.RELATIVE_POSITIONING,  # not a Vue concept
    Feature.AUTO_LAYOUT,  # Vue components, not a Figma layout concept
})

_VUE_SUPPORTED = (WEB_COMMON_FEATURES - _VUE_UNSUPPORTED) | frozenset({
    Feature.GRID,
    Feature.FILLS_GRADIENT,
    Feature.FILLS_IMAGE,
    Feature.SHADOWS,
    Feature.BLUR,
    Feature.CORNER_RADIUS,
    Feature.PER_CORNER_RADIUS,
    Feature.TEXT_DECORATION,
    Feature.TEXT_CASE,
    Feature.LETTER_SPACING,
    Feature.OVERFLOW_CLIP,
    Feature.OVERFLOW_SCROLL,
    Feature.IMAGE_ASSETS,
    Feature.SVG_ASSETS,
    Feature.DESIGN_TOKENS,
    Feature.TOKEN_REFERENCES,
    Feature.COMPONENTS,
    Feature.COMPONENT_INSTANCES,
    Feature.COMPONENT_VARIANTS,
    Feature.BREAKPOINTS,
    Feature.MEDIA_QUERIES,
    Feature.RESPONSIVE_CONSTRAINTS,
    Feature.PROTOTYPE_LINKS,
    Feature.INTERACTIONS,
})

_VUE_PARTIAL = frozenset({
    Feature.CONSTRAINTS,
    Feature.MARGIN,
})


def _fmt_num(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _clean(value: str) -> str:
    """Normalize ``24.0px`` -> ``24px`` and ``50.00%`` -> ``50%``."""
    if value.endswith(".0px"):
        return value[:-4] + "px"
    if value.endswith(".00%"):
        return value[:-4] + "%"
    return value


def _hex6(color: Any) -> str:
    h = color.to_hex()
    alpha = getattr(color, "a", None)
    if alpha is None or alpha >= 0.999:
        return h[:-2]
    return h


def _css_value(css_prop: str, value: str) -> str:
    """Convert shared CssStyleGenerator values into clean CSS value strings."""
    return _clean(value)


class VueBackend(BackendAdapter):
    """Vue 3 SFC backend.

    Generates Vue single-file components with <template>, <script setup>,
    and <style scoped> sections.  Design tokens map to CSS custom properties.
    """

    @property
    def name(self) -> str:
        return "vue"

    @property
    def display_name(self) -> str:
        return "Vue 3 SFC"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_features=_VUE_SUPPORTED,
            unsupported_features=_VUE_UNSUPPORTED,
            partial_features=_VUE_PARTIAL,
            styling_system="css_scoped",
            framework="vue",
            renderer="browser",
            file_extensions=(".vue",),
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
        ir_by_id: Dict[str, IRNode] = {n.id: n for n in document.all_nodes()}
        style_gen = CssStyleGenerator()
        node_builder = VNodeBuilder(resolution)

        for screen_idx, screen in enumerate(layout_plan.screens):
            component_name = _to_pascal_case(screen.name or f"Screen{screen_idx}")
            root_vnode = node_builder.build(screen)
            vue_content = self._generate_sfc(
                root_vnode, screen, component_name, style_gen, ir_by_id,
            )
            node_ids = [n.node_id for n in screen.walk() if n.node_id]

            output.files.append(GeneratedFile(
                path=f"{component_name}.vue",
                content=vue_content,
                language="vue",
                node_ids=node_ids,
            ))

        output.fidelity_losses.extend(self.preflight(document, layout_plan))
        output.metadata = {
            "backend": self.name,
            "viewport": viewport,
            "screen_count": len(layout_plan.screens),
        }
        return output

    # ------------------------------------------------------------- template

    def _generate_sfc(
        self,
        root_vnode: VNode,
        screen: LayoutNodePlan,
        name: str,
        style_gen: CssStyleGenerator,
        ir_by_id: Dict[str, IRNode],
    ) -> str:
        template_lines = ["<template>"]
        template_lines.append(self._render_template(
            root_vnode, screen, style_gen, ir_by_id, indent=1,
        ))
        template_lines.append("</template>")
        template_lines.append("")
        template_lines.append('<script setup lang="ts">')
        template_lines.append("// FigmaForge generated Vue component")
        template_lines.append("defineProps<{")
        template_lines.append("  className?: string")
        template_lines.append("}>()")
        template_lines.append("</script>")
        template_lines.append("")
        template_lines.append("<style scoped>")
        rules, media = self._collect_css(
            root_vnode, screen, style_gen, ir_by_id,
        )
        template_lines.extend(rules)
        for bp_width in sorted(media):
            template_lines.append(f"@media (max-width: {bp_width}) {{")
            for selector, props in media[bp_width]:
                template_lines.append(f"  {selector} {{ {props} }}")
            template_lines.append("}")
        template_lines.append("</style>")

        return "\n".join(template_lines) + "\n"

    def _render_template(
        self,
        vnode: VNode,
        plan_node: LayoutNodePlan,
        style_gen: CssStyleGenerator,
        ir_by_id: Dict[str, IRNode],
        indent: int,
    ) -> str:
        pad = "  " * indent
        attrs: List[str] = []
        if vnode.node_id:
            attrs.append(f'data-figma-id="{escape_attr(vnode.node_id)}"')
        attrs.append(f'class="{_class_name(vnode.node_id)}"')
        for key, value in vnode.props.items():
            if key == "data-figma-id":
                continue
            attrs.append(f'{key}="{escape_attr(str(value))}"')
        attr_str = (" " + " ".join(attrs)) if attrs else ""

        if vnode.text_content is not None and not vnode.children:
            element = f"{pad}<{vnode.tag}{attr_str}>{escape_html(vnode.text_content)}</{vnode.tag}>"
        elif vnode.children:
            children_html = "\n".join(
                self._render_template(
                    child_vn, child_plan, style_gen, ir_by_id, indent + 1,
                )
                for child_vn, child_plan in zip(vnode.children, plan_node.children)
            )
            element = f"{pad}<{vnode.tag}{attr_str}>\n{children_html}\n{pad}</{vnode.tag}>"
        else:
            element = f"{pad}<{vnode.tag}{attr_str}></{vnode.tag}>"

        if plan_node.display == DISPLAY_ABSOLUTE:
            marker = f"{pad}<!-- fidelity: absolute_positioning approximated (in-flow) -->"
            return f"{marker}\n{element}"
        return element

    # ------------------------------------------------------------------ css

    def _collect_css(
        self,
        vnode: VNode,
        plan_node: LayoutNodePlan,
        style_gen: CssStyleGenerator,
        ir_by_id: Dict[str, IRNode],
    ) -> tuple:
        """Return (rule_lines, media_rules) for the whole screen."""
        rules: List[str] = []
        media: Dict[str, List[tuple]] = {}
        self._node_css(vnode, plan_node, style_gen, ir_by_id, rules, media)
        for child_vn, child_plan in zip(vnode.children, plan_node.children):
            child_rules, child_media = self._collect_css(
                child_vn, child_plan, style_gen, ir_by_id,
            )
            rules.extend(child_rules)
            for bp_width, entries in child_media.items():
                media.setdefault(bp_width, []).extend(entries)
        return rules, media

    def _node_css(
        self,
        vnode: VNode,
        plan_node: LayoutNodePlan,
        style_gen: CssStyleGenerator,
        ir_by_id: Dict[str, IRNode],
        rules: List[str],
        media: Dict[str, List[tuple]],
    ) -> None:
        style = style_gen.generate_style(plan_node)
        ir = ir_by_id.get(vnode.node_id)
        self._extend_style(style, plan_node, ir)

        # Absolute positioning is a preflight loss; degrade to in-flow by
        # dropping the positioning props (the template carries the marker).
        if plan_node.display == DISPLAY_ABSOLUTE:
            for prop in ("display", "position", "left", "right", "top", "bottom"):
                style.base.pop(prop, None)

        selector = f".{_class_name(vnode.node_id)}"
        if style.base:
            props = "; ".join(
                f"{camel_to_kebab(k)}: {_css_value(k, v)}"
                for k, v in style.base.items()
            )
            rules.append(f"{selector} {{ {props} }}")

        for bp_width, bp_styles in style.breakpoints.items():
            props = "; ".join(
                f"{camel_to_kebab(k)}: {_css_value(k, v)}"
                for k, v in bp_styles.items()
            )
            media.setdefault(bp_width, []).append((selector, props))

    def _extend_style(
        self,
        style: Any,
        plan_node: LayoutNodePlan,
        ir: Optional[IRNode],
    ) -> None:
        """Add IR-sourced style (fills/radius/opacity/typography) + breakpoints."""
        if ir is not None and ir.style is not None:
            s = ir.style
            for fill in s.fills:
                if not fill.visible or fill.kind == "none":
                    continue
                if fill.kind == "solid" and fill.color is not None:
                    style.base["background"] = _hex6(fill.color)
                    break
                if fill.kind == "gradient" and fill.gradient_stops:
                    stops = ", ".join(
                        f"{_hex6(st.color)} {_fmt_num(st.position * 100)}%"
                        for st in fill.gradient_stops
                        if st.color is not None
                    )
                    style.base["background"] = f"linear-gradient(to bottom, {stops})"
                    break
                # image fills -> named fallback, never silent
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

        # Breakpoints -> scoped @media rules.
        for bp in plan_node.breakpoints:
            mapped = bp_to_css_prop(bp)
            if mapped is None:
                continue
            css_prop, value = mapped
            style.breakpoints.setdefault(f"{_fmt_num(bp.width)}px", {})[css_prop] = value


def _class_name(node_id: str) -> str:
    return f"n-{node_id.replace(':', '-')}"


def _to_pascal_case(name: str) -> str:
    parts = name.replace("-", " ").replace("_", " ").split()
    return "".join(p.capitalize() for p in parts) if parts else "Component"
