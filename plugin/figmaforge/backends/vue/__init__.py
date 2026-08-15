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
    collect_component_refs,
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

# Features Vue can only approximate: asset/token/reference plumbing,
# component variants, and prototype links are all outside the common IR
# surface (spec non-goals).  Image fills are SUPPORTED when the assets stage
# resolved a path for the node (real scoped-CSS background); an unresolved
# image fill keeps the marked fallback.
_VUE_PARTIAL = frozenset({
    Feature.CONSTRAINTS,
    Feature.MARGIN,
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
    Feature.FILLS_IMAGE,  # real scoped-CSS background when the asset is resolved
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
        opts = options or {}
        assets: Dict[str, Dict[str, Any]] = opts.get("assets") or {}
        ir_by_id: Dict[str, IRNode] = {n.id: n for n in document.all_nodes()}
        style_gen = CssStyleGenerator()
        node_builder = VNodeBuilder(resolution)
        instance_names = frozenset(
            inst.get("resolved_name") for inst in (resolution.instances if resolution else [])
            if inst.get("status") == "resolved" and inst.get("resolved_name")
        )

        for screen_idx, screen in enumerate(layout_plan.screens):
            component_name = _to_pascal_case(screen.name or f"Screen{screen_idx}")
            root_vnode = node_builder.build(screen)
            vue_content = self._generate_sfc(
                root_vnode, screen, component_name, style_gen, ir_by_id,
                assets=assets, instance_names=instance_names,
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
        assets: Optional[Dict[str, Dict[str, Any]]] = None,
        instance_names: Optional[frozenset] = None,
    ) -> str:
        template_lines = ["<template>"]
        template_lines.append(self._render_template(
            root_vnode, screen, style_gen, ir_by_id, indent=1, assets=assets,
        ))
        template_lines.append("</template>")
        template_lines.append("")
        template_lines.append('<script setup lang="ts">')
        template_lines.append("// FigmaForge generated Vue component")
        # Self-contained fallbacks (Part 21, S2): every referenced component
        # name is registered in setup as a render-function component that
        # renders that node's own subtree — output compiles + renders without
        # the user's component library.
        instance_names = instance_names or frozenset()
        refs = collect_component_refs(root_vnode, screen)
        if refs:
            template_lines.append("import { h } from 'vue';")
            for ref_name, ref_vnode, ref_plan in refs:
                if ref_name in instance_names:
                    template_lines.append(
                        "// fidelity: component_instance approximated (fallback)"
                    )
                template_lines.append(
                    f"const {ref_name} = {{ setup: (_, {{ slots }}) => () => "
                    f"{self._render_h(ref_vnode, ref_plan)} }};"
                )
            template_lines.append("")
        template_lines.append("defineProps<{")
        template_lines.append("  className?: string")
        template_lines.append("}>()")
        template_lines.append("</script>")
        template_lines.append("")
        template_lines.append("<style scoped>")
        rules, media = ScopedCssGenerator(ir_by_id, assets=assets).collect(
            root_vnode, screen,
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
        assets: Optional[Dict[str, Dict[str, Any]]] = None,
        tag_override: Optional[str] = None,
    ) -> str:
        pad = "  " * indent
        tag = tag_override if tag_override is not None else vnode.tag
        # A component reference at the call site is a self-closing tag with
        # identifying attrs only — the styling + subtree live in its fallback.
        is_component_ref = vnode.is_component and tag_override is None
        attrs: List[str] = []
        if vnode.node_id:
            attrs.append(f'data-figma-id="{escape_attr(vnode.node_id)}"')
        if not is_component_ref:
            attrs.append(f'class="{_class_name(vnode.node_id)}"')
        for key, value in vnode.props.items():
            if key == "data-figma-id":
                continue
            attrs.append(f'{key}="{escape_attr(str(value))}"')
        attr_str = (" " + " ".join(attrs)) if attrs else ""

        if is_component_ref:
            element = f"{pad}<{tag}{attr_str}></{tag}>"
        elif vnode.text_content is not None and not vnode.children:
            element = f"{pad}<{tag}{attr_str}>{escape_html(vnode.text_content)}</{tag}>"
        elif vnode.children:
            children_html = "\n".join(
                self._render_template(
                    child_vn, child_plan, style_gen, ir_by_id, indent + 1,
                    assets=assets,
                )
                for child_vn, child_plan in zip(vnode.children, plan_node.children)
            )
            element = f"{pad}<{tag}{attr_str}>\n{children_html}\n{pad}</{tag}>"
        else:
            element = f"{pad}<{tag}{attr_str}></{tag}>"

        # An image fill is only marked when it has no resolved asset; a
        # resolved one emits a real scoped-CSS background (extend_ir_style).
        markers: List[str] = []
        ir = ir_by_id.get(vnode.node_id)
        resolved = (assets or {}).get(vnode.node_id, {}).get("path")
        if ir is not None and ir.style is not None and not resolved:
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

    def _render_h(
        self,
        vnode: VNode,
        plan_node: LayoutNodePlan,
    ) -> str:
        """Emit a Vue render-function call for a node (fallback body).

        ``h('div', { attrs }, children)`` mirroring the template emission —
        the fallback component renders the referenced node's own subtree as a
        plain div carrying its scoped class (Part 21, S2).
        """
        attrs: Dict[str, Any] = {}
        if vnode.node_id:
            attrs["data-figma-id"] = vnode.node_id
        attrs["class"] = _class_name(vnode.node_id)
        for key, value in vnode.props.items():
            if key == "data-figma-id":
                continue
            attrs[key] = str(value)
        inner = ", ".join(f"{k!r}: {v!r}" for k, v in attrs.items())
        if vnode.text_content is not None and not vnode.children:
            return f"h('div', {{ {inner} }}, {vnode.text_content!r})"
        if vnode.children:
            child_calls = ", ".join(
                self._render_h(cv, cp)
                for cv, cp in zip(vnode.children, plan_node.children)
            )
            return f"h('div', {{ {inner} }}, [{child_calls}])"
        return f"h('div', {{ {inner} }}, [])"


def _class_name(node_id: str) -> str:
    return f"n-{node_id.replace(':', '-')}"


def _to_pascal_case(name: str) -> str:
    parts = name.replace("-", " ").replace("_", " ").split()
    return "".join(p.capitalize() for p in parts) if parts else "Component"
