"""
SwiftUI backend adapter.

Generates SwiftUI view structs (.swift files) for iOS/macOS targets.
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
    Feature.JUSTIFY,  # alignment on stacks
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
    Feature.IMAGE_ASSETS,
    Feature.COMPONENTS,  # SwiftUI views
    Feature.COMPONENT_INSTANCES,
    Feature.DESIGN_TOKENS,  # Asset catalog / Color extensions
    Feature.OVERFLOW_CLIP,  # .clipped()
})

_SWIFTUI_PARTIAL = frozenset({
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
})

_SWIFTUI_UNSUPPORTED = frozenset({
    Feature.FILL_SIZE,  # Partial — no CSS-equivalent flex: 1
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

        for screen_idx, screen in enumerate(layout_plan.screens):
            view_name = _to_pascal_case(screen.name or f"Screen{screen_idx}")
            swift_content = self._generate_view(screen, view_name)
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

    def _generate_view(self, plan: Any, name: str) -> str:
        return f"""\
import SwiftUI

// FigmaForge generated SwiftUI view
// Source: LayoutPlan node {plan.node_id}

struct {name}View: View {{
    var body: some View {{
        VStack(spacing: 0) {{
            // TODO: Generate from LayoutPlan for node {plan.node_id}
            Text("{name}")
                .font(.title)
                .padding()
        }}
        .frame(width: 390)
    }}
}}

#Preview {{
    {name}View()
}}
"""


def _to_pascal_case(name: str) -> str:
    parts = name.replace("-", " ").replace("_", " ").split()
    return "".join(p.capitalize() for p in parts) if parts else "Component"
