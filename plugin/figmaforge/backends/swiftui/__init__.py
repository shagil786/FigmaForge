"""
SwiftUI backend adapter (Part 14).

Converts the framework-neutral Design IR + LayoutPlan into SwiftUI view
structs (``.swift``): flex containers lower to ``VStack``/``HStack`` (with
``alignment`` and ``spacing:``), leaves to ``Text(_:)`` / ``Color(...)`` /
``LinearGradient``, and every node's layout + style to a SwiftUI modifier
chain (``.frame(width:height:)``, ``.padding``, ``.background(Color)``,
``.cornerRadius``, ``.opacity``, ``.foregroundColor``, ``.font``,
``.multilineTextAlignment``, ``.kerning``, ``.lineSpacing``, ``.position``).
Hex colors become deterministic ``Color(red: 0.20, green: 0.40, blue: 0.80)``
doubles.  No web machinery — a self-contained lowering.

Fidelity honesty: features this backend cannot represent (e.g. image fills)
are reported by ``preflight`` and degraded with an inline ``// fidelity:``
marker — never silently.
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
)
from core.ir_types import IRDocument, IRNode
from core.layout_types import (
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    LayoutNodePlan,
    LayoutPlan,
    OVERFLOW_CLIP,
    SIZING_FILL,
    SIZING_HUG,
)
from core.resolver import ResolutionReport

# SwiftUI supports a different set of features than web
_SWIFTUI_SUPPORTED = frozenset({
    Feature.FLEX,  # HStack/VStack
    Feature.ABSOLUTE_POSITIONING,  # .position()
    Feature.AUTO_LAYOUT,  # HStack/VStack/LazyVStack
    Feature.FIXED_SIZE,
    Feature.FILL_SIZE,  # .frame(maxWidth: .infinity)
    Feature.HUG_SIZE,
    Feature.PADDING,
    Feature.GAP,  # spacing parameter
    Feature.ALIGN_ITEMS,
    Feature.FILLS_SOLID,  # .background(Color)
    Feature.BORDERS,  # .border() / .overlay
    Feature.SHADOWS,  # .shadow()
    Feature.CORNER_RADIUS,  # .cornerRadius()
    Feature.OPACITY,  # .opacity()
    Feature.FONT_FAMILY,
    Feature.FONT_WEIGHT,
    Feature.FONT_SIZE,
    Feature.LINE_HEIGHT,
    Feature.TEXT_ALIGN,
    Feature.TEXT_WRAP,
    Feature.TEXT_DECORATION,
    Feature.COMPONENTS,  # SwiftUI views (per-screen structs)
    Feature.OVERFLOW_CLIP,  # .clipped()
})

_SWIFTUI_PARTIAL = frozenset({
    Feature.JUSTIFY,  # no main-axis justification in SwiftUI stacks (Spacer idiom)
    Feature.GRID,  # LazyVGrid exists but different semantics
    Feature.FILLS_GRADIENT,  # LinearGradient exists but limited
    Feature.PER_CORNER_RADIUS,  # UnevenRoundedCornerShape (iOS 16+)
    Feature.BLUR,  # .blur() exists but no background blur
    Feature.BREAKPOINTS,  # Size classes, not pixel breakpoints
    Feature.RESPONSIVE_CONSTRAINTS,  # GeometryReader, different model
    Feature.LETTER_SPACING,  # .kerning() exists
    Feature.TEXT_CASE,  # .textCase() modifier
    Feature.MARGIN,  # No direct margin concept
    Feature.COMPONENT_VARIANTS,  # Requires pattern implementation
    Feature.PROTOTYPE_LINKS,  # NavigationLink, different model
    Feature.INTERACTIONS,  # Gestures, different model
    Feature.IMAGE_ASSETS,  # Asset catalog emission (spec non-goal)
    Feature.COMPONENT_INSTANCES,  # Nested subviews (spec non-goal)
    Feature.DESIGN_TOKENS,  # Asset catalog / Color extensions (spec non-goal)
})

_SWIFTUI_UNSUPPORTED = frozenset({
    Feature.PERCENT_SIZE,  # GeometryReader needed, not native
    Feature.MIN_MAX_CONSTRAINTS,  # .frame(min:max:) exists but limited
    Feature.CONSTRAINTS,  # Different constraint model
    Feature.RELATIVE_POSITIONING,  # Not a SwiftUI concept
    Feature.FILLS_IMAGE,  # Image as background requires workaround
    Feature.OVERFLOW_SCROLL,  # ScrollView exists but different semantics
    Feature.MEDIA_QUERIES,
    Feature.SVG_ASSETS,
    Feature.TOKEN_REFERENCES,
})

_STACK_ALIGN_V = {
    "MIN": ".leading",
    "CENTER": None,
    "MAX": ".trailing",
    "STRETCH": ".leading",
}
_STACK_ALIGN_H = {
    "MIN": ".top",
    "CENTER": None,
    "MAX": ".bottom",
    "STRETCH": ".top",
}
_WEIGHT_SWIFT = {
    100: ".ultraLight",
    200: ".thin",
    300: ".light",
    500: ".medium",
    600: ".semibold",
    700: ".bold",
    800: ".heavy",
    900: ".black",
}
_ALIGN_SWIFT = {"LEFT": ".leading", "CENTER": ".center", "RIGHT": ".trailing"}


def _fmt_num(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _swift_color(color: Any) -> str:
    r = color.r if color.r is not None else 0.0
    g = color.g if color.g is not None else 0.0
    b = color.b if color.b is not None else 0.0
    return f"red: {r:.2f}, green: {g:.2f}, blue: {b:.2f}"


def _escape_swift(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


class SwiftUIBackend(BackendAdapter):
    """SwiftUI view backend.

    Generates SwiftUI view structs for iOS/macOS.  Layout maps to
    HStack/VStack/ZStack; styles map to SwiftUI modifiers.
    """

    @property
    def name(self) -> str:
        return "swiftui"

    @property
    def display_name(self) -> str:
        return "SwiftUI"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_features=_SWIFTUI_SUPPORTED,
            unsupported_features=_SWIFTUI_UNSUPPORTED,
            partial_features=_SWIFTUI_PARTIAL,
            styling_system="swiftui_modifiers",
            framework="swiftui",
            renderer="xcode_preview",
            file_extensions=(".swift",),
        )

    def generate(
        self,
        document: IRDocument,
        layout_plan: LayoutPlan,
        resolution: Optional[ResolutionReport] = None,
        viewport: float = 390.0,  # iPhone default
        options: Optional[Dict[str, Any]] = None,
    ) -> GeneratedOutput:
        output = GeneratedOutput()
        ir_by_id: Dict[str, IRNode] = {n.id: n for n in document.all_nodes()}

        for screen_idx, screen in enumerate(layout_plan.screens):
            view_name = _to_pascal_case(screen.name or f"Screen{screen_idx}")
            swift_content = self._generate_view(screen, view_name, ir_by_id)
            node_ids = [n.node_id for n in screen.walk() if n.node_id]

            output.files.append(GeneratedFile(
                path=f"{view_name}View.swift",
                content=swift_content,
                language="swift",
                node_ids=node_ids,
            ))

        # Report fidelity losses — SwiftUI will have many
        output.fidelity_losses.extend(self.preflight(document, layout_plan))
        output.metadata = {
            "backend": self.name,
            "viewport": viewport,
            "screen_count": len(layout_plan.screens),
            "platform": "iOS/macOS",
        }
        return output

    # ---------------------------------------------------------------- emit

    def _generate_view(
        self,
        screen: LayoutNodePlan,
        name: str,
        ir_by_id: Dict[str, IRNode],
    ) -> str:
        root_ir = ir_by_id.get(screen.node_id)
        body = self._render(screen, root_ir, ir_by_id, indent=2)
        return f"""\
import SwiftUI

// FigmaForge generated SwiftUI view
// Source: LayoutPlan node {screen.node_id}

struct {name}View: View {{
    var body: some View {{
{body}
    }}
}}

#Preview {{
    {name}View()
}}
"""

    def _render(
        self,
        plan_node: LayoutNodePlan,
        ir: Optional[IRNode],
        ir_by_id: Dict[str, IRNode],
        indent: int,
    ) -> str:
        pad = "  " * indent
        head, children, modifiers, markers = self._lower(plan_node, ir)

        lines: List[str] = []
        for marker in markers:
            lines.append(f"{pad}// {marker}")

        if children:
            lines.append(f"{pad}{head} {{")
            for child in plan_node.children:
                lines.append(self._render(
                    child, ir_by_id.get(child.node_id), ir_by_id, indent + 1,
                ))
            lines.append(f"{pad}}}")
        else:
            lines.append(f"{pad}{head}")

        for modifier in modifiers:
            lines.append(f"{pad}  {modifier}")
        return "\n".join(lines)

    def _lower(
        self,
        plan_node: LayoutNodePlan,
        ir: Optional[IRNode],
    ) -> Tuple[str, bool, List[str], List[str]]:
        """Return (head, is_container, modifiers, fidelity_markers)."""
        modifiers: List[str] = []
        markers: List[str] = []
        fill = _primary_fill(ir)
        is_text = plan_node.kind == "text" and bool(
            plan_node.text and plan_node.text.characters
        )
        is_container = bool(plan_node.children)

        if is_text:
            head = f'Text("{_escape_swift(plan_node.text.characters)}")'
        elif is_container:
            if plan_node.direction == "column":
                head = "VStack" + _stack_params(plan_node, vertical=True)
            else:
                head = "HStack" + _stack_params(plan_node, vertical=False)
            if fill is not None and fill[0] == "solid":
                modifiers.append(f".background(Color({_swift_color(fill[1])}))")
        elif fill is not None and fill[0] == "solid":
            head = f"Color({_swift_color(fill[1])})"
        elif fill is not None and fill[0] == "gradient":
            colors = ", ".join(
                f"Color({_swift_color(st.color)})"
                for st in fill[2]
                if st.color is not None
            )
            head = (
                "LinearGradient(gradient: Gradient(colors: "
                f"[{colors}]), startPoint: .top, endPoint: .bottom)"
            )
        elif fill is not None and fill[0] == "image":
            markers.append("fidelity: fills_image approximated (solid fallback)")
            head = "Rectangle()"
            modifiers.append(".background(Color(red: 0.94, green: 0.94, blue: 0.94))")
        else:
            head = "Rectangle()"

        # Sizing modes -> SwiftUI frames (fixed wins unless fill/hug declared).
        h_fill = v_fill = h_hug = v_hug = False
        if plan_node.sizing is not None:
            h = plan_node.sizing.horizontal
            v = plan_node.sizing.vertical
            h_fill = h is not None and h.mode == SIZING_FILL
            v_fill = v is not None and v.mode == SIZING_FILL
            h_hug = h is not None and h.mode == SIZING_HUG
            v_hug = v is not None and v.mode == SIZING_HUG
        if not is_text and plan_node.box is not None and not (h_fill or v_fill):
            modifiers.append(
                f".frame(width: {_fmt_num(plan_node.box.width)}, "
                f"height: {_fmt_num(plan_node.box.height)})"
            )
        if h_fill:
            modifiers.append(".frame(maxWidth: .infinity)")
        if v_fill:
            modifiers.append(".frame(maxHeight: .infinity)")
        if h_hug or v_hug:
            modifiers.append(".fixedSize()")

        if plan_node.display == DISPLAY_ABSOLUTE and plan_node.box is not None:
            modifiers.append(
                f".position(x: {_fmt_num(plan_node.box.x)}, y: {_fmt_num(plan_node.box.y)})"
            )

        if plan_node.spacing is not None and plan_node.spacing.padding is not None:
            p = plan_node.spacing.padding
            edges = [p.top, p.right, p.bottom, p.left]
            present = [e for e in edges if e is not None]
            if len(present) == 4 and len(set(present)) == 1:
                modifiers.append(f".padding({_fmt_num(present[0])})")
            else:
                if p.top is not None:
                    modifiers.append(f".padding(.top, {_fmt_num(p.top)})")
                if p.right is not None:
                    modifiers.append(f".padding(.trailing, {_fmt_num(p.right)})")
                if p.bottom is not None:
                    modifiers.append(f".padding(.bottom, {_fmt_num(p.bottom)})")
                if p.left is not None:
                    modifiers.append(f".padding(.leading, {_fmt_num(p.left)})")

        if ir is not None and ir.style is not None:
            s = ir.style
            if is_text and fill is not None and fill[0] == "solid":
                modifiers.append(f".foregroundColor(Color({_swift_color(fill[1])}))")
            if s.radius is not None:
                modifiers.append(f".cornerRadius({_fmt_num(s.radius)})")
            if s.opacity is not None and s.opacity < 1.0:
                modifiers.append(f".opacity({_fmt_num(s.opacity)})")
            for border in s.borders:
                if border.visible and border.weight is not None and border.color is not None:
                    modifiers.append(
                        f".border(Color({_swift_color(border.color)}), "
                        f"width: {_fmt_num(border.weight)})"
                    )
                    break

            for shadow in s.shadows:
                if shadow.visible and shadow.color is not None:
                    color = f"Color({_swift_color(shadow.color)})"
                    if shadow.color.a is not None and shadow.color.a < 0.999:
                        color += f".opacity({_fmt_num(shadow.color.a)})"
                    modifiers.append(
                        f".shadow(color: {color}, radius: {_fmt_num(shadow.blur)}, "
                        f"x: {_fmt_num(shadow.x)}, y: {_fmt_num(shadow.y)})"
                    )
                    break

        if ir is not None and ir.opacity < 1.0:
            modifiers.append(f".opacity({_fmt_num(ir.opacity)})")

        if ir is not None and ir.typography is not None:
            t = ir.typography
            if t.font_size is not None:
                size_arg = f"size: {_fmt_num(t.font_size)}"
                weight = _WEIGHT_SWIFT.get(
                    int(round(float(t.font_weight))) if t.font_weight is not None else None
                )
                weight_arg = f", weight: {weight}" if weight else ""
                if t.font_family:
                    modifiers.append(
                        f".font(.custom(\"{_escape_swift(t.font_family)}\", "
                        f"{size_arg}{weight_arg}))"
                    )
                else:
                    modifiers.append(f".font(.system({size_arg}{weight_arg}))")
            if t.line_height is not None:
                modifiers.append(f".lineSpacing({_fmt_num(t.line_height)})")
            if t.letter_spacing is not None:
                modifiers.append(f".kerning({_fmt_num(t.letter_spacing)})")
            if t.text_align:
                modifiers.append(
                    f".multilineTextAlignment({_ALIGN_SWIFT.get(t.text_align, '.center')})"
                )
            if t.text_decoration:
                decoration = {
                    "UNDERLINE": ".underline()",
                    "STRIKETHROUGH": ".strikethrough()",
                }.get(t.text_decoration.upper())
                if decoration:
                    modifiers.append(decoration)

        if plan_node.overflow is not None:
            if (plan_node.overflow.x == OVERFLOW_CLIP
                    or plan_node.overflow.y == OVERFLOW_CLIP):
                modifiers.append(".clipped()")

        return head, is_container, modifiers, markers


def _primary_fill(ir: Optional[IRNode]) -> Optional[Tuple[str, Any, List]]:
    """Return (kind, color, gradient_stops) for the first visible fill, or None."""
    if ir is None or ir.style is None:
        return None
    for fill in ir.style.fills:
        if not fill.visible or fill.kind == "none":
            continue
        if fill.kind == "solid":
            return ("solid", fill.color, [])
        if fill.kind == "gradient":
            return ("gradient", None, fill.gradient_stops)
        return (fill.kind, None, [])
    return None


def _stack_params(plan_node: LayoutNodePlan, vertical: bool) -> str:
    params: List[str] = []
    align = plan_node.alignment.align if plan_node.alignment else None
    mapping = _STACK_ALIGN_V if vertical else _STACK_ALIGN_H
    alignment = mapping.get(align) if align else None
    spacing = plan_node.spacing.gap if plan_node.spacing else None
    if alignment:
        params.append(f"alignment: {alignment}")
    if spacing is not None:
        params.append(f"spacing: {_fmt_num(spacing)}")
    if not params:
        return ""
    return "(" + ", ".join(params) + ")"


def _to_pascal_case(name: str) -> str:
    parts = name.replace("-", " ").replace("_", " ").split()
    return "".join(p.capitalize() for p in parts) if parts else "Component"
