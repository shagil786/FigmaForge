"""
Flutter backend adapter (Part 14).

Converts the framework-neutral Design IR + LayoutPlan into Flutter widget
trees (``.dart``): flex containers lower to ``Row``/``Column`` (with
``mainAxisAlignment``/``crossAxisAlignment`` and ``SizedBox`` gap
separators), absolute positioning to ``Stack`` + ``Positioned``, leaves to
``Container`` (``BoxDecoration`` with ``Color(0xFF....)``,
``BorderRadius.circular``), fixed sizing to ``SizedBox``, text to ``Text``
with ``TextStyle`` (``fontSize``/``fontWeight``/``color``/``height``/
``textAlign``), single-child alignment to ``Align``, and opacity to an
``Opacity`` wrapper.  No web machinery — a self-contained lowering.

Fidelity honesty: features this backend cannot represent (e.g. media
queries) are reported by ``preflight`` (overridden for more precise
analysis) and degraded with an inline ``// fidelity:`` marker — never
silently.
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
)
from core.ir_types import IRDocument, IRNode
from core.layout_types import (
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    LayoutNodePlan,
    LayoutPlan,
    OVERFLOW_CLIP,
)
from core.resolver import ResolutionReport

# Flutter supports a different feature set
_FLUTTER_SUPPORTED = frozenset({
    Feature.FLEX,  # Row/Column
    Feature.ABSOLUTE_POSITIONING,  # Stack + Positioned
    Feature.AUTO_LAYOUT,  # Row/Column/Wrap
    Feature.FIXED_SIZE,
    Feature.PADDING,
    Feature.GAP,  # SizedBox between children
    Feature.JUSTIFY,  # MainAxisAlignment
    Feature.ALIGN_ITEMS,  # CrossAxisAlignment
    Feature.FILLS_SOLID,  # Container color
    Feature.BORDERS,  # Border decoration
    Feature.SHADOWS,  # BoxShadow
    Feature.CORNER_RADIUS,  # BorderRadius
    Feature.OPACITY,  # Opacity widget
    Feature.FONT_FAMILY,
    Feature.FONT_WEIGHT,
    Feature.FONT_SIZE,
    Feature.LINE_HEIGHT,
    Feature.TEXT_ALIGN,
    Feature.TEXT_WRAP,
    Feature.TEXT_DECORATION,
    Feature.COMPONENTS,  # Flutter widgets (per-screen classes)
    Feature.OVERFLOW_CLIP,  # ClipRect
    Feature.TEXT_CASE,
    Feature.LETTER_SPACING,
})

_FLUTTER_PARTIAL = frozenset({
    Feature.FILL_SIZE,  # lowered as computed box size, not Expanded
    Feature.HUG_SIZE,  # intrinsic by default for text; no IntrinsicWidth emitted
    Feature.PERCENT_SIZE,  # lowered as computed box size, not FractionallySizedBox
    Feature.GRID,  # GridView exists but different semantics
    Feature.FILLS_GRADIENT,  # LinearGradient exists
    Feature.FILLS_IMAGE,  # DecorationImage
    Feature.PER_CORNER_RADIUS,  # BorderRadius.only
    Feature.BLUR,  # BackdropFilter, not simple
    Feature.BREAKPOINTS,  # MediaQuery, different model
    Feature.RESPONSIVE_CONSTRAINTS,  # LayoutBuilder
    Feature.MARGIN,  # EdgeInsets as margin
    Feature.COMPONENT_VARIANTS,
    Feature.PROTOTYPE_LINKS,  # Navigator, different model
    Feature.INTERACTIONS,  # GestureDetector
    Feature.SVG_ASSETS,  # Requires flutter_svg package
    Feature.TOKEN_REFERENCES,
    Feature.MIN_MAX_CONSTRAINTS,  # BoxConstraints
    Feature.ALIGN_SELF,  # Align widget
    Feature.IMAGE_ASSETS,  # Asset plumbing (spec non-goal)
    Feature.COMPONENT_INSTANCES,  # Nested widgets (spec non-goal)
    Feature.DESIGN_TOKENS,  # ThemeData (spec non-goal)
    Feature.OVERFLOW_SCROLL,  # SingleChildScrollView layout model
})

_FLUTTER_UNSUPPORTED = frozenset({
    Feature.CONSTRAINTS,  # Different constraint model
    Feature.RELATIVE_POSITIONING,
    Feature.MEDIA_QUERIES,
})

_MAIN_ALIGN = {
    "MIN": "start",
    "CENTER": "center",
    "MAX": "end",
    "SPACE_BETWEEN": "spaceBetween",
}
_CROSS_ALIGN = {
    "MIN": "start",
    "CENTER": "center",
    "MAX": "end",
    "STRETCH": "stretch",
}
_TEXT_ALIGN = {"LEFT": "left", "CENTER": "center", "RIGHT": "right"}
_ALIGN_WIDGET = {
    "MIN": "topLeft",
    "CENTER": "center",
    "MAX": "bottomRight",
    "STRETCH": "topLeft",
}


def _fmt_num(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _dart_hex(color: Any) -> str:
    def _byte(v: Any) -> int:
        return max(0, min(255, int(round((v if v is not None else 0.0) * 255))))
    return "0xFF{:02X}{:02X}{:02X}".format(
        _byte(color.r), _byte(color.g), _byte(color.b),
    )


def _dart_hex8(color: Any) -> str:
    """``0xAARRGGBB`` with alpha in the leading byte."""
    def _byte(v: Any) -> int:
        return max(0, min(255, int(round((v if v is not None else 0.0) * 255))))
    alpha = color.a if color.a is not None else 1.0
    return "0x{:02X}{:02X}{:02X}{:02X}".format(
        _byte(alpha), _byte(color.r), _byte(color.g), _byte(color.b),
    )


def _escape_dart(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("$", "\\$")
        .replace("\n", "\\n")
    )


def _indent_lines(lines: List[str], extra: int) -> List[str]:
    pad = "  " * extra
    return [f"{pad}{line}" for line in lines]


def _primary_fill(ir: Optional[IRNode]) -> Optional[Any]:
    if ir is None or ir.style is None:
        return None
    for fill in ir.style.fills:
        if fill.visible and fill.kind != "none":
            return fill
    return None


class FlutterBackend(BackendAdapter):
    """Flutter widget backend.

    Generates Flutter widget trees in Dart.  Layout maps to
    Row/Column/Stack; styles map to BoxDecoration and TextStyle.
    """

    @property
    def name(self) -> str:
        return "flutter"

    @property
    def display_name(self) -> str:
        return "Flutter"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_features=_FLUTTER_SUPPORTED,
            unsupported_features=_FLUTTER_UNSUPPORTED,
            partial_features=_FLUTTER_PARTIAL,
            styling_system="flutter_widgets",
            framework="flutter",
            renderer="flutter_simulator",
            file_extensions=(".dart",),
        )

    def generate(
        self,
        document: IRDocument,
        layout_plan: LayoutPlan,
        resolution: Optional[ResolutionReport] = None,
        viewport: float = 390.0,  # Phone default
        options: Optional[Dict[str, Any]] = None,
    ) -> GeneratedOutput:
        output = GeneratedOutput()
        ir_by_id: Dict[str, IRNode] = {n.id: n for n in document.all_nodes()}

        for screen_idx, screen in enumerate(layout_plan.screens):
            widget_name = _to_pascal_case(screen.name or f"Screen{screen_idx}")
            dart_content = self._generate_widget(screen, widget_name, ir_by_id)
            node_ids = [n.node_id for n in screen.walk() if n.node_id]

            output.files.append(GeneratedFile(
                path=f"{_to_snake_case(widget_name)}_screen.dart",
                content=dart_content,
                language="dart",
                node_ids=node_ids,
            ))

        # Report fidelity losses
        output.fidelity_losses.extend(self.preflight(document, layout_plan))
        output.metadata = {
            "backend": self.name,
            "viewport": viewport,
            "screen_count": len(layout_plan.screens),
            "platform": "iOS/Android/Web/Desktop",
        }
        return output

    def preflight(
        self,
        document: IRDocument,
        layout_plan: LayoutPlan,
    ) -> List[FidelityLoss]:
        """Default checks + media queries (responsive evidence is a loss)."""
        losses = super().preflight(document, layout_plan)
        if self.capabilities.supports(Feature.MEDIA_QUERIES) == "unsupported":
            for node in document.all_nodes():
                if node.responsive is not None:
                    losses.append(FidelityLoss(
                        feature=Feature.MEDIA_QUERIES,
                        node_id=node.id,
                        message=f"Media queries not supported by {self.name}",
                    ))
        return losses

    # ---------------------------------------------------------------- emit

    def _generate_widget(
        self,
        screen: LayoutNodePlan,
        name: str,
        ir_by_id: Dict[str, IRNode],
    ) -> str:
        root_ir = ir_by_id.get(screen.node_id)
        body = self._render(screen, root_ir, ir_by_id, indent=4)
        body_lines = body.split("\n")
        first, rest = body_lines[0].lstrip(), body_lines[1:]
        return f"""\
import 'package:flutter/material.dart';

// FigmaForge generated Flutter widget
// Source: LayoutPlan node {screen.node_id}

class {name}Screen extends StatelessWidget {{
  const {name}Screen({{super.key}});

  @override
  Widget build(BuildContext context) {{
    return Scaffold(
      body: SizedBox(
        width: 390,
        child: {first}
{"\n".join(rest)}
      ),
    );
  }}
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
        lines: List[str] = []

        if ir is not None and ir.responsive is not None:
            lines.append(f"{pad}// fidelity: media_queries approximated (static layout)")

        is_text = plan_node.kind == "text" and bool(
            plan_node.text and plan_node.text.characters
        )
        if is_text:
            widget_lines = self._text_widget(plan_node, ir)
        elif plan_node.children:
            widget_lines = self._container_widget(plan_node, ir, ir_by_id)
        else:
            widget_lines = self._leaf_widget(plan_node, ir)

        # Single-child alignment -> Align wrapper (non-flex nodes).
        if plan_node.alignment is not None and plan_node.alignment.align:
            if not plan_node.children or plan_node.display != DISPLAY_FLEX:
                widget_lines = _wrap(
                    "Align(",
                    [f"alignment: Alignment."
                     f"{_ALIGN_WIDGET.get(plan_node.alignment.align, 'center')},"],
                    widget_lines,
                )

        # Opacity wrapper.
        opacity = None
        if ir is not None and ir.style is not None:
            if ir.style.opacity is not None and ir.style.opacity < 1.0:
                opacity = ir.style.opacity
        if opacity is None and ir is not None and ir.opacity < 1.0:
            opacity = ir.opacity
        if opacity is not None:
            widget_lines = _wrap(
                "Opacity(",
                [f"opacity: {_fmt_num(opacity)},"],
                widget_lines,
            )

        # Absolute positioning -> Positioned (declared supported).
        if plan_node.display == DISPLAY_ABSOLUTE and plan_node.box is not None:
            widget_lines = _wrap(
                "Positioned(",
                [f"left: {_fmt_num(plan_node.box.x)},"
                 f" top: {_fmt_num(plan_node.box.y)},"],
                widget_lines,
            )

        for wl in widget_lines:
            lines.append(f"{pad}{wl}")
        return "\n".join(lines)

    def _text_widget(self, plan_node: LayoutNodePlan, ir: Optional[IRNode]) -> List[str]:
        text = plan_node.text.characters
        typo = ir.typography if ir is not None else None
        if typo is not None and typo.text_case:
            text_case = typo.text_case.upper()
            if text_case == "UPPER":
                text = text.upper()
            elif text_case == "LOWER":
                text = text.lower()
            elif text_case == "TITLE":
                text = text.title()
        lines = ["Text("]
        lines.append(f"  '{_escape_dart(text)}',")
        style_args: List[str] = []
        if typo is not None:
            if typo.font_family:
                style_args.append(f"fontFamily: '{typo.font_family}',")
            if typo.font_size is not None:
                style_args.append(f"fontSize: {_fmt_num(typo.font_size)},")
            if typo.font_weight is not None:
                style_args.append(
                    f"fontWeight: FontWeight.w{int(round(float(typo.font_weight)))},"
                )
            if typo.line_height is not None and typo.font_size:
                ratio = round(typo.line_height / typo.font_size, 2)
                style_args.append(f"height: {_fmt_num(ratio)},")
            if typo.letter_spacing is not None:
                style_args.append(f"letterSpacing: {_fmt_num(typo.letter_spacing)},")
            if typo.text_decoration:
                decoration = {
                    "UNDERLINE": "TextDecoration.underline",
                    "STRIKETHROUGH": "TextDecoration.lineThrough",
                }.get(typo.text_decoration.upper())
                if decoration:
                    style_args.append(f"decoration: {decoration},")
        fill = _primary_fill(ir)
        if fill is not None and fill.kind == "solid" and fill.color is not None:
            style_args.append(f"color: Color({_dart_hex(fill.color)}),")
        if style_args:
            lines.append("  style: TextStyle(")
            for arg in style_args:
                lines.append(f"    {arg}")
            lines.append("  ),")
        if typo is not None and typo.text_align:
            lines.append(
                f"  textAlign: TextAlign.{_TEXT_ALIGN.get(typo.text_align, 'left')},"
            )
        lines.append("),")
        return lines

    def _container_widget(
        self,
        plan_node: LayoutNodePlan,
        ir: Optional[IRNode],
        ir_by_id: Dict[str, IRNode],
    ) -> List[str]:
        has_absolute = any(
            child.display == DISPLAY_ABSOLUTE for child in plan_node.children
        )
        if has_absolute:
            head = "Stack("
        elif plan_node.direction == "column":
            head = "Column("
        else:
            head = "Row("

        flex_args: List[str] = []
        if not has_absolute and plan_node.alignment is not None:
            if plan_node.alignment.justify:
                flex_args.append(
                    f"mainAxisAlignment: MainAxisAlignment."
                    f"{_MAIN_ALIGN.get(plan_node.alignment.justify, 'start')},"
                )
            if plan_node.alignment.align:
                flex_args.append(
                    f"crossAxisAlignment: CrossAxisAlignment."
                    f"{_CROSS_ALIGN.get(plan_node.alignment.align, 'start')},"
                )

        # Children + gap separators (every child ends with a list separator).
        gap = plan_node.spacing.gap if plan_node.spacing else None
        blocks: List[List[str]] = []
        placed = False
        for child in plan_node.children:
            child_lines = self._render(
                child, ir_by_id.get(child.node_id), ir_by_id, indent=0,
            ).split("\n")
            if child.display == DISPLAY_ABSOLUTE:
                blocks.append(child_lines)
                continue
            if gap is not None and placed:
                if plan_node.direction == "column":
                    blocks.append([f"SizedBox(height: {_fmt_num(gap)}),"])
                else:
                    blocks.append([f"SizedBox(width: {_fmt_num(gap)}),"])
            blocks.append(child_lines)
            placed = True

        body: List[str] = []
        for block in blocks:
            body.extend(block)
            if not body[-1].rstrip().endswith(","):
                body[-1] = body[-1].rstrip() + ","

        flex_lines = [head]
        flex_lines.extend(f"  {arg}" for arg in flex_args)
        flex_lines.append("  children: [")
        flex_lines.extend(_indent_lines(body, 2))
        flex_lines.append("  ],")
        flex_lines.append("),")

        # Container wrapper for sizing/padding/decoration.
        container_args = self._container_args(plan_node, ir)
        if not container_args:
            return flex_lines
        lines = ["Container("]
        lines.extend(f"  {arg}" for arg in container_args)
        lines.append("  child: " + flex_lines[0])
        lines.extend(_indent_lines(flex_lines[1:], 1))
        lines.append("),")
        return lines

    def _leaf_widget(self, plan_node: LayoutNodePlan, ir: Optional[IRNode]) -> List[str]:
        container_args = self._container_args(plan_node, ir)
        if container_args:
            lines = ["Container("]
            lines.extend(f"  {arg}" for arg in container_args)
            lines.append("),")
            return lines
        if plan_node.box is not None:
            return [f"SizedBox(width: {_fmt_num(plan_node.box.width)}, "
                    f"height: {_fmt_num(plan_node.box.height)})"]
        return ["SizedBox.shrink()"]

    def _container_args(
        self,
        plan_node: LayoutNodePlan,
        ir: Optional[IRNode],
    ) -> List[str]:
        args: List[str] = []
        if plan_node.box is not None:
            args.append(f"width: {_fmt_num(plan_node.box.width)},")
            args.append(f"height: {_fmt_num(plan_node.box.height)},")
        if (plan_node.overflow is not None
                and (plan_node.overflow.x == OVERFLOW_CLIP
                     or plan_node.overflow.y == OVERFLOW_CLIP)):
            args.append("clipBehavior: Clip.hardEdge,")
        if plan_node.spacing is not None and plan_node.spacing.padding is not None:
            p = plan_node.spacing.padding
            edges = [p.top, p.right, p.bottom, p.left]
            present = [e for e in edges if e is not None]
            if len(present) == 4 and len(set(present)) == 1:
                args.append(f"padding: EdgeInsets.all({_fmt_num(present[0])}),")
            else:
                parts = []
                if p.top is not None:
                    parts.append(f"top: {_fmt_num(p.top)}")
                if p.right is not None:
                    parts.append(f"right: {_fmt_num(p.right)}")
                if p.bottom is not None:
                    parts.append(f"bottom: {_fmt_num(p.bottom)}")
                if p.left is not None:
                    parts.append(f"left: {_fmt_num(p.left)}")
                args.append(f"padding: EdgeInsets.only({', '.join(parts)}),")
        deco = self._decoration_args(ir)
        if deco:
            args.append("decoration: BoxDecoration(")
            args.extend(f"  {d}" for d in deco)
            args.append("),")
        return args

    def _decoration_args(self, ir: Optional[IRNode]) -> List[str]:
        if ir is None or ir.style is None:
            return []
        style = ir.style
        args: List[str] = []
        fill = _primary_fill(ir)
        if fill is not None:
            if fill.kind == "solid" and fill.color is not None:
                args.append(f"color: Color({_dart_hex(fill.color)}),")
            elif fill.kind == "gradient" and fill.gradient_stops:
                colors = ", ".join(
                    f"Color({_dart_hex(st.color)})"
                    for st in fill.gradient_stops
                    if st.color is not None
                )
                args.append("gradient: LinearGradient(")
                args.append("  begin: Alignment.topCenter,")
                args.append("  end: Alignment.bottomCenter,")
                args.append(f"  colors: [{colors}],")
                args.append("),")
            elif fill.kind == "image":
                args.append("color: Color(0xFFF0F0F0),")
        if style.radius is not None:
            args.append(f"borderRadius: BorderRadius.circular({_fmt_num(style.radius)}),")
        elif style.corner_radii:
            radii = style.corner_radii
            if len(radii) == 4 and len(set(radii)) == 1:
                args.append(f"borderRadius: BorderRadius.circular({_fmt_num(radii[0])}),")
            else:
                args.append("borderRadius: BorderRadius.only(")
                for corner, r in zip(
                    ("topLeft", "topRight", "bottomRight", "bottomLeft"), radii,
                ):
                    if r is not None:
                        args.append(f"  {corner}: Radius.circular({_fmt_num(r)}),")
                args.append("),")
        for border in style.borders:
            if border.visible and border.weight is not None and border.color is not None:
                args.append(
                    f"border: Border.all(color: Color({_dart_hex(border.color)}), "
                    f"width: {_fmt_num(border.weight)}),"
                )
                break
        for shadow in style.shadows:
            if shadow.visible and shadow.color is not None:
                args.append("boxShadow: [BoxShadow(")
                args.append(f"  color: Color({_dart_hex8(shadow.color)}),")
                args.append(f"  blurRadius: {_fmt_num(shadow.blur)},")
                if shadow.spread:
                    args.append(f"  spreadRadius: {_fmt_num(shadow.spread)},")
                args.append(f"  offset: Offset({_fmt_num(shadow.x)}, {_fmt_num(shadow.y)}),")
                args.append("],")
                break
        return args


def _wrap(
    open_line: str,
    arg_lines: List[str],
    child_lines: List[str],
) -> List[str]:
    """Wrap a multi-line widget with an outer widget + child."""
    lines = [open_line]
    lines.extend(f"  {arg}" for arg in arg_lines)
    lines.append(f"  child: {child_lines[0]}")
    rest = _indent_lines(child_lines[1:], 2)
    # Align the widget's own closing paren with its args (child: + 2).
    if rest and rest[-1].rstrip().endswith("),"):
        rest[-1] = "  " + rest[-1]
    lines.extend(rest)
    lines.append(")")  # wrapped widgets always close with a child: arg
    return lines


def _to_pascal_case(name: str) -> str:
    parts = name.replace("-", " ").replace("_", " ").split()
    return "".join(p.capitalize() for p in parts) if parts else "Component"


def _to_snake_case(name: str) -> str:
    result: List[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            result.append("_")
        result.append(ch.lower())
    return "".join(result)
