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
    ScopedCssGenerator,
    VNode,
    VNodeBuilder,
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

# Features Vue can only approximate: image fills (solid fallback + inline
# marker), asset/token/reference plumbing, component variants, and prototype
# links are all outside the common IR surface (spec non-goals).
_VUE_PARTIAL = frozenset({
    Feature.CONSTRAINTS,
    Feature.MARGIN,
    Feature.FILLS_IMAGE,
    Feature.IMAGE_ASSETS,
    Feature.SVG_ASSETS,
    Feature.DESIGN_TOKENS,
    Feature.TOKEN_REFERENCES,
    Feature.COMPONENT_VARIANTS,
    Feature.PROTOTYPE_LINKS,
    Feature.INTERACTIONS,
})

_VUE_SUPPORTED = (WEB_COMMON_FEATURES - _VUE_UNSUPPORTED - _VUE_PARTIAL) | frozenset({
    Feature.GRID,
    Feature.FILLS_GRADIENT,
    Feature.SHADOWS,
    Feature.BLUR,
    Feature.CORNER_RADIUS,
    Feature.PER_CORNER_RADIUS,
    Feature.TEXT_DECORATION,
    Feature.TEXT_CASE,
    Feature.LETTER_SPACING,
    Feature.OVERFLOW_CLIP,
    Feature.OVERFLOW_SCROLL,
    Feature.COMPONENTS,
    Feature.COMPONENT_INSTANCES,
    Feature.BREAKPOINTS,
    Feature.MEDIA_QUERIES,
    Feature.RESPONSIVE_CONSTRAINTS,
})


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
        rules, media = ScopedCssGenerator(ir_by_id).collect(root_vnode, screen)
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

        # Image fills are approximated with a solid fallback — always marked.
        markers: List[str] = []
        ir = ir_by_id.get(vnode.node_id)
        if ir is not None and ir.style is not None:
            for fill in ir.style.fills:
                if fill.visible and fill.kind == "image":
                    markers.append(
                        "<!-- fidelity: fills_image approximated (solid fallback) -->"
                    )
                    break
        if plan_node.display == DISPLAY_ABSOLUTE:
            markers.append(
                "<!-- fidelity: absolute_positioning approximated (in-flow) -->"
            )
        if markers:
            marker_lines = "\n".join(f"{pad}{m}" for m in markers)
            return f"{marker_lines}\n{element}"
        return element

def _class_name(node_id: str) -> str:
    return f"n-{node_id.replace(':', '-')}"


def _to_pascal_case(name: str) -> str:
    parts = name.replace("-", " ").replace("_", " ").split()
    return "".join(p.capitalize() for p in parts) if parts else "Component"
