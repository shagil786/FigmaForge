"""
Flutter backend adapter.

Generates Flutter widget trees (.dart files) for cross-platform targets.
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
from core.ir_types import IRDocument
from core.layout_types import LayoutPlan
from core.resolver import ResolutionReport

# Flutter supports a different feature set
_FLUTTER_SUPPORTED = frozenset({
    Feature.FLEX,  # Row/Column
    Feature.ABSOLUTE_POSITIONING,  # Stack + Positioned
    Feature.AUTO_LAYOUT,  # Row/Column/Wrap
    Feature.FIXED_SIZE,
    Feature.FILL_SIZE,  # Expanded
    Feature.HUG_SIZE,  # IntrinsicWidth/Height
    Feature.PERCENT_SIZE,  # FractionallySizedBox
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
    Feature.IMAGE_ASSETS,
    Feature.COMPONENTS,  # Flutter widgets
    Feature.COMPONENT_INSTANCES,
    Feature.DESIGN_TOKENS,  # ThemeData
    Feature.OVERFLOW_CLIP,  # ClipRect
    Feature.OVERFLOW_SCROLL,  # SingleChildScrollView
    Feature.TEXT_CASE,
    Feature.LETTER_SPACING,
})

_FLUTTER_PARTIAL = frozenset({
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
})

_FLUTTER_UNSUPPORTED = frozenset({
    Feature.CONSTRAINTS,  # Different constraint model
    Feature.RELATIVE_POSITIONING,
    Feature.MEDIA_QUERIES,
})


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

        for screen_idx, screen in enumerate(layout_plan.screens):
            widget_name = _to_pascal_case(screen.name or f"Screen{screen_idx}")
            dart_content = self._generate_widget(screen, widget_name)
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

    def _generate_widget(self, plan: Any, name: str) -> str:
        return f"""\
import 'package:flutter/material.dart';

// FigmaForge generated Flutter widget
// Source: LayoutPlan node {plan.node_id}

class {name}Screen extends StatelessWidget {{
  const {name}Screen({{super.key}});

  @override
  Widget build(BuildContext context) {{
    return Scaffold(
      body: SizedBox(
        width: 390,
        child: Column(
          children: [
            // TODO: Generate from LayoutPlan for node {plan.node_id}
            const Text(
              '{name}',
              style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
            ),
          ],
        ),
      ),
    );
  }}
}}
"""


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
