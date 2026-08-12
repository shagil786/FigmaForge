"""
React + Tailwind CSS backend adapter.

Generates React components (TSX) styled with Tailwind CSS utility classes.
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
from core.ir_types import IRDocument
from core.layout_types import LayoutPlan
from core.resolver import ResolutionReport

# React+Tailwind supports most web features
_REACT_TW_SUPPORTED = WEB_COMMON_FEATURES | frozenset({
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

# Tailwind cannot express these
_REACT_TW_UNSUPPORTED = frozenset({
    Feature.ABSOLUTE_POSITIONING,  # Tailwind has position-absolute but limited
    Feature.RELATIVE_POSITIONING,  # Supported but not idiomatic Tailwind
    Feature.AUTO_LAYOUT,  # React components, not a Tailwind concept
})


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

        # Generate React components
        for screen_idx, screen in enumerate(layout_plan.screens):
            component_name = _to_pascal_case(screen.name or f"Screen{screen_idx}")
            tsx_content = self._generate_component(screen, component_name)
            node_ids = [n.node_id for n in screen.walk() if n.node_id]

            output.files.append(GeneratedFile(
                path=f"{component_name}.tsx",
                content=tsx_content,
                language="tsx",
                node_ids=node_ids,
            ))

        # Generate Tailwind config extension
        output.files.append(GeneratedFile(
            path="tailwind.config.figmaforge.js",
            content=self._generate_tailwind_config(document),
            language="javascript",
        ))

        # Report fidelity losses
        output.fidelity_losses.extend(self.preflight(document, layout_plan))

        output.metadata = {
            "backend": self.name,
            "viewport": viewport,
            "screen_count": len(layout_plan.screens),
            "options": options or {},
        }
        return output

    def _generate_component(self, plan: Any, name: str) -> str:
        """Generate a React functional component from a LayoutNodePlan."""
        # Stub: generate a placeholder component
        return f"""\
import React from 'react';

interface {name}Props {{
  className?: string;
}}

export const {name}: React.FC<{name}Props> = ({{ className = '' }}) => {{
  return (
    <div className={{className}} data-figma-screen="{plan.name or name}">
      {{/* TODO: Generate from LayoutPlan for node {plan.node_id} */}}
      <div className="p-4">
        <p>{name} — generated by FigmaForge React+Tailwind backend</p>
      </div>
    </div>
  );
}};

export default {name};
"""

    def _generate_tailwind_config(self, document: IRDocument) -> str:
        """Generate a Tailwind config extension from design tokens."""
        return f"""\
// FigmaForge generated Tailwind config extension
// Merge this into your tailwind.config.js

module.exports = {{
  theme: {{
    extend: {{
      // Design tokens from Figma file: {document.file_key}
      colors: {{
        // TODO: Extract from IR tokens
      }},
      spacing: {{
        // TODO: Extract from IR spacing tokens
      }},
      fontFamily: {{
        // TODO: Extract from IR typography tokens
      }},
    }},
  }},
}};
"""


def _to_pascal_case(name: str) -> str:
    """Convert a name to PascalCase for React component naming."""
    parts = name.replace("-", " ").replace("_", " ").split()
    return "".join(p.capitalize() for p in parts) if parts else "Component"
