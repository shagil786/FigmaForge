"""
Backend adapter protocol and capability model.

Defines the contract every code-generation backend must implement.  The core
pipeline (ingest → normalize → resolve → layout) is framework-neutral; a
backend adapter is the *target-specific lowering* step that converts a
LayoutPlan + Design IR into generated source code for a particular framework
and styling system.

Architecture
------------

    Figma  →  Design IR  →  LayoutPlan  →  BackendAdapter.generate()
                                                ↓
                                        Generated code (target-specific)
                                                ↓
                                        Target renderer (browser / simulator)
                                                ↓
                                        Visual comparison  →  Repair

Key rules
---------

- A backend MUST declare its capabilities and limitations explicitly.
- A backend MUST NOT silently approximate a feature it cannot represent.
  When a feature cannot be expressed in the target, the backend records a
  ``FidelityLoss`` entry — the caller decides whether to proceed.
- A backend consumes the framework-neutral IR and LayoutPlan; it never
  mutates them.
- Standard library only; no external dependencies.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.ir_types import IRDocument
from core.layout_types import LayoutPlan
from core.resolver import ResolutionReport


# ---------------------------------------------------------------------------
# Feature vocabulary — framework-neutral capabilities the IR can express
# ---------------------------------------------------------------------------

class Feature:
    """Canonical feature identifiers the IR may require.

    Backends declare which of these they support.  Anything not in
    ``supported_features`` is reported as a fidelity loss when encountered.
    """

    # Layout
    FLEX = "flex"
    GRID = "grid"
    ABSOLUTE_POSITIONING = "absolute_positioning"
    RELATIVE_POSITIONING = "relative_positioning"
    AUTO_LAYOUT = "auto_layout"
    CONSTRAINTS = "constraints"

    # Sizing
    FIXED_SIZE = "fixed_size"
    FILL_SIZE = "fill_size"
    HUG_SIZE = "hug_size"
    PERCENT_SIZE = "percent_size"
    MIN_MAX_CONSTRAINTS = "min_max_constraints"

    # Spacing
    PADDING = "padding"
    GAP = "gap"
    MARGIN = "margin"

    # Alignment
    JUSTIFY = "justify"
    ALIGN_ITEMS = "align_items"
    ALIGN_SELF = "align_self"

    # Visual style
    FILLS_SOLID = "fills_solid"
    FILLS_GRADIENT = "fills_gradient"
    FILLS_IMAGE = "fills_image"
    BORDERS = "borders"
    SHADOWS = "shadows"
    BLUR = "blur"
    CORNER_RADIUS = "corner_radius"
    PER_CORNER_RADIUS = "per_corner_radius"
    OPACITY = "opacity"

    # Typography
    FONT_FAMILY = "font_family"
    FONT_WEIGHT = "font_weight"
    FONT_SIZE = "font_size"
    LINE_HEIGHT = "line_height"
    LETTER_SPACING = "letter_spacing"
    TEXT_ALIGN = "text_align"
    TEXT_DECORATION = "text_decoration"
    TEXT_CASE = "text_case"

    # Responsive
    BREAKPOINTS = "breakpoints"
    MEDIA_QUERIES = "media_queries"
    RESPONSIVE_CONSTRAINTS = "responsive_constraints"

    # Components
    COMPONENTS = "components"
    COMPONENT_VARIANTS = "component_variants"
    COMPONENT_INSTANCES = "component_instances"

    # Tokens
    DESIGN_TOKENS = "design_tokens"
    TOKEN_REFERENCES = "token_references"

    # Assets
    IMAGE_ASSETS = "image_assets"
    SVG_ASSETS = "svg_assets"

    # Interactions
    PROTOTYPE_LINKS = "prototype_links"
    INTERACTIONS = "interactions"

    # Overflow
    OVERFLOW_CLIP = "overflow_clip"
    OVERFLOW_SCROLL = "overflow_scroll"
    TEXT_WRAP = "text_wrap"


# ---------------------------------------------------------------------------
# Fidelity loss — explicit reporting of unsupported features
# ---------------------------------------------------------------------------

@dataclass
class FidelityLoss:
    """A feature the IR requires but the backend cannot represent.

    Backends MUST emit one of these per unsupported feature occurrence
    rather than silently approximating.
    """

    feature: str  # Feature constant
    node_id: str  # IR node that needs it
    message: str  # Human-readable explanation
    severity: str = "warning"  # "info" | "warning" | "error"
    fallback_applied: Optional[str] = None  # What the backend did instead

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "feature": self.feature,
            "node_id": self.node_id,
            "message": self.message,
            "severity": self.severity,
        }
        if self.fallback_applied is not None:
            out["fallback_applied"] = self.fallback_applied
        return out


# ---------------------------------------------------------------------------
# Generated output — what a backend produces
# ---------------------------------------------------------------------------

@dataclass
class GeneratedFile:
    """A single generated source file."""

    path: str  # Relative path within the output directory
    content: str  # File content
    language: str  # "tsx" | "css" | "html" | "vue" | "svelte" | "swift" | "dart" | ...
    node_ids: List[str] = field(default_factory=list)  # IR nodes this file covers

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "node_ids": list(self.node_ids),
            "size_bytes": len(self.content.encode("utf-8")),
        }


@dataclass
class GeneratedOutput:
    """Complete output of a backend's code generation."""

    files: List[GeneratedFile] = field(default_factory=list)
    fidelity_losses: List[FidelityLoss] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files": [f.to_dict() for f in self.files],
            "fidelity_losses": [l.to_dict() for l in self.fidelity_losses],
            "metadata": dict(self.metadata),
            "file_count": len(self.files),
            "loss_count": len(self.fidelity_losses),
        }

    @property
    def has_errors(self) -> bool:
        return any(l.severity == "error" for l in self.fidelity_losses)


# ---------------------------------------------------------------------------
# Backend capability declaration
# ---------------------------------------------------------------------------

@dataclass
class BackendCapabilities:
    """What a backend supports and what it cannot represent.

    Every backend MUST construct this honestly.  The pipeline uses it to
    pre-flight check whether a target is viable for a given design.
    """

    supported_features: frozenset = field(default_factory=frozenset)
    unsupported_features: frozenset = field(default_factory=frozenset)
    partial_features: frozenset = field(default_factory=frozenset)  # supported with caveats
    styling_system: str = ""  # "css" | "tailwind" | "swiftui_modifiers" | "flutter_widgets" | ...
    framework: str = ""  # "react" | "vue" | "svelte" | "html" | "swiftui" | "flutter" | ...
    renderer: str = ""  # "browser" | "xcode_preview" | "flutter_simulator" | ...
    file_extensions: Tuple[str, ...] = ()  # (".tsx", ".css") etc.

    def supports(self, feature: str) -> str:
        """Return 'supported', 'partial', or 'unsupported' for a feature."""
        if feature in self.supported_features:
            return "supported"
        if feature in self.partial_features:
            return "partial"
        return "unsupported"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "framework": self.framework,
            "styling_system": self.styling_system,
            "renderer": self.renderer,
            "supported_features": sorted(self.supported_features),
            "unsupported_features": sorted(self.unsupported_features),
            "partial_features": sorted(self.partial_features),
            "file_extensions": list(self.file_extensions),
        }


# ---------------------------------------------------------------------------
# Backend adapter — the protocol every backend implements
# ---------------------------------------------------------------------------

class BackendAdapter(ABC):
    """Abstract base class for code-generation backends.

    Each backend converts the framework-neutral IR + LayoutPlan into
    target-specific generated source code.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique backend identifier, e.g. 'react_css', 'vue', 'swiftui'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'React + CSS'."""

    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        """Declare what this backend supports and cannot represent."""

    @abstractmethod
    def generate(
        self,
        document: IRDocument,
        layout_plan: LayoutPlan,
        resolution: Optional[ResolutionReport] = None,
        viewport: float = 1440.0,
        options: Optional[Dict[str, Any]] = None,
    ) -> GeneratedOutput:
        """Generate target-specific code from the framework-neutral IR.

        Args:
            document: The normalized Design IR.
            layout_plan: The inferred layout plan.
            resolution: Optional component/token resolution report.
            viewport: Target viewport width.
            options: Backend-specific options (e.g. Tailwind prefix).

        Returns:
            GeneratedOutput with files and fidelity losses.
        """

    def preflight(
        self,
        document: IRDocument,
        layout_plan: LayoutPlan,
    ) -> List[FidelityLoss]:
        """Pre-flight check: what fidelity losses will this design incur?

        Default implementation walks the IR and checks each node's required
        features against capabilities.  Backends may override for more
        precise analysis.
        """
        losses: List[FidelityLoss] = []
        caps = self.capabilities

        for node in document.all_nodes():
            self._check_node_features(node, caps, losses)

        return losses

    def _check_node_features(
        self,
        node: Any,
        caps: BackendCapabilities,
        losses: List[FidelityLoss],
    ) -> None:
        """Check a single IR node's required features against capabilities."""
        # Layout features
        if node.layout:
            if node.layout.mode == "auto":
                if caps.supports(Feature.FLEX) == "unsupported":
                    losses.append(FidelityLoss(
                        feature=Feature.FLEX,
                        node_id=node.id,
                        message=f"Auto-layout not supported by {self.name}",
                    ))
            if node.layout.mode == "grid":
                if caps.supports(Feature.GRID) == "unsupported":
                    losses.append(FidelityLoss(
                        feature=Feature.GRID,
                        node_id=node.id,
                        message=f"Grid layout not supported by {self.name}",
                    ))

        # Position
        if node.position and node.position.mode == "absolute":
            if caps.supports(Feature.ABSOLUTE_POSITIONING) == "unsupported":
                losses.append(FidelityLoss(
                    feature=Feature.ABSOLUTE_POSITIONING,
                    node_id=node.id,
                    message=f"Absolute positioning not supported by {self.name}",
                ))
        if node.position and node.position.mode == "relative":
            if caps.supports(Feature.RELATIVE_POSITIONING) == "unsupported":
                losses.append(FidelityLoss(
                    feature=Feature.RELATIVE_POSITIONING,
                    node_id=node.id,
                    message=f"Relative positioning not supported by {self.name}",
                ))

        # Style checks
        if node.style:
            for fill in node.style.fills:
                if fill.kind == "gradient" and caps.supports(Feature.FILLS_GRADIENT) == "unsupported":
                    losses.append(FidelityLoss(
                        feature=Feature.FILLS_GRADIENT,
                        node_id=node.id,
                        message=f"Gradient fills not supported by {self.name}",
                    ))
                if fill.kind == "image" and caps.supports(Feature.FILLS_IMAGE) == "unsupported":
                    losses.append(FidelityLoss(
                        feature=Feature.FILLS_IMAGE,
                        node_id=node.id,
                        message=f"Image fills not supported by {self.name}",
                    ))

            if node.style.shadows and caps.supports(Feature.SHADOWS) == "unsupported":
                losses.append(FidelityLoss(
                    feature=Feature.SHADOWS,
                    node_id=node.id,
                    message=f"Shadows not supported by {self.name}",
                ))

            if node.style.blurs and caps.supports(Feature.BLUR) == "unsupported":
                losses.append(FidelityLoss(
                    feature=Feature.BLUR,
                    node_id=node.id,
                    message=f"Blur effects not supported by {self.name}",
                ))

            if node.style.corner_radii and len(node.style.corner_radii) == 4:
                if caps.supports(Feature.PER_CORNER_RADIUS) == "unsupported":
                    losses.append(FidelityLoss(
                        feature=Feature.PER_CORNER_RADIUS,
                        node_id=node.id,
                        message=f"Per-corner radius not supported by {self.name}",
                        fallback_applied="uniform radius approximation",
                    ))

        # Typography
        if node.typography:
            if node.typography.text_decoration and caps.supports(Feature.TEXT_DECORATION) == "unsupported":
                losses.append(FidelityLoss(
                    feature=Feature.TEXT_DECORATION,
                    node_id=node.id,
                    message=f"Text decoration not supported by {self.name}",
                ))

        # Components
        if node.instance and caps.supports(Feature.COMPONENT_INSTANCES) == "unsupported":
            losses.append(FidelityLoss(
                feature=Feature.COMPONENT_INSTANCES,
                node_id=node.id,
                message=f"Component instances not supported by {self.name}",
            ))

        # Responsive and interaction metadata must not disappear silently.
        if node.responsive is not None and caps.supports(Feature.RESPONSIVE_CONSTRAINTS) == "unsupported":
            losses.append(FidelityLoss(
                feature=Feature.RESPONSIVE_CONSTRAINTS,
                node_id=node.id,
                message=f"Responsive constraints not supported by {self.name}",
            ))
        if node.prototype is not None:
            if (node.prototype.links or node.prototype.url) and caps.supports(Feature.PROTOTYPE_LINKS) == "unsupported":
                losses.append(FidelityLoss(
                    feature=Feature.PROTOTYPE_LINKS,
                    node_id=node.id,
                    message=f"Prototype links not supported by {self.name}",
                ))
            if node.prototype.interactions and caps.supports(Feature.INTERACTIONS) == "unsupported":
                losses.append(FidelityLoss(
                    feature=Feature.INTERACTIONS,
                    node_id=node.id,
                    message=f"Interactions not supported by {self.name}",
                ))

        if node.style:
            if node.style.borders and caps.supports(Feature.BORDERS) == "unsupported":
                losses.append(FidelityLoss(
                    feature=Feature.BORDERS,
                    node_id=node.id,
                    message=f"Borders not supported by {self.name}",
                ))
            if node.style.opacity < 1.0 and caps.supports(Feature.OPACITY) == "unsupported":
                losses.append(FidelityLoss(
                    feature=Feature.OPACITY,
                    node_id=node.id,
                    message=f"Opacity not supported by {self.name}",
                ))


# ---------------------------------------------------------------------------
# Common feature sets for web backends
# ---------------------------------------------------------------------------

WEB_COMMON_FEATURES = frozenset({
    Feature.FLEX,
    Feature.ABSOLUTE_POSITIONING,
    Feature.RELATIVE_POSITIONING,
    Feature.AUTO_LAYOUT,
    Feature.FIXED_SIZE,
    Feature.FILL_SIZE,
    Feature.HUG_SIZE,
    Feature.PERCENT_SIZE,
    Feature.MIN_MAX_CONSTRAINTS,
    Feature.PADDING,
    Feature.GAP,
    Feature.JUSTIFY,
    Feature.ALIGN_ITEMS,
    Feature.FILLS_SOLID,
    Feature.BORDERS,
    Feature.CORNER_RADIUS,
    Feature.OPACITY,
    Feature.FONT_FAMILY,
    Feature.FONT_WEIGHT,
    Feature.FONT_SIZE,
    Feature.LINE_HEIGHT,
    Feature.TEXT_ALIGN,
    Feature.TEXT_WRAP,
    Feature.IMAGE_ASSETS,
    Feature.DESIGN_TOKENS,
    Feature.TOKEN_REFERENCES,
})
