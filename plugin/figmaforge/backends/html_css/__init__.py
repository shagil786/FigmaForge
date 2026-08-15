"""
HTML + CSS backend adapter (fully implemented).

Converts the framework-neutral Design IR + LayoutPlan into plain HTML and
CSS source files.  This is the reference backend — it supports the widest
range of IR features because HTML/CSS is the lowest-common-denominator for
visual rendering.

The shared web machinery (VNode/VStyle, the CSS style generator, the VNode
tree builder, escaping helpers) lives in ``backends.web_common`` so every
web backend uses ONE style-mapping implementation.
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
    VNode,
    VStyle,
    CssStyleGenerator,
    VNodeBuilder,
    camel_to_kebab,
    escape_attr,
    escape_html,
)
from core.ir_types import IRDocument
from core.layout_types import LayoutNodePlan, LayoutPlan
from core.resolver import ResolutionReport


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
            return f"{pad}<{tag}{attrs}>{escape_html(node.text_content)}</{tag}>"

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
            parts.append(f'{key}="{escape_attr(str(value))}"')

        if node.style.base:
            class_name = f"n-{node.node_id.replace(':', '-')}"
            parts.append(f'class="{class_name}"')
            selector = f".{class_name}"
            props_str = "; ".join(
                f"{camel_to_kebab(k)}: {v}" for k, v in node.style.base.items()
            )
            css_rules.append(f"{selector} {{ {props_str} }}")

            for bp, bp_styles in node.style.breakpoints.items():
                bp_props = "; ".join(
                    f"{camel_to_kebab(k)}: {v}" for k, v in bp_styles.items()
                )
                css_rules.append(f"@media (max-width: {bp}) {{ {selector} {{ {bp_props} }} }}")

        return (" " + " ".join(parts)) if parts else ""


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
        style_gen = CssStyleGenerator()
        node_builder = VNodeBuilder(resolution)
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
        style_gen: CssStyleGenerator,
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
  <title>{escape_html(title)}</title>
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
</html>
"""
