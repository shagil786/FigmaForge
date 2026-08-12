"""
Repair Candidate Classifier (Part 8).

Converts :class:`DiffReport` mismatches from the diff engine into structured
:class:`RepairCandidate` objects.  Each candidate is classified into one of
nine repair categories and mapped back to the originating Figma node, the
generated component, source file, CSS selector, and design token.

Design goals — consistent with FigmaForge conventions:

- Standard library only; no external dependencies.
- Never hide differences, blur screenshots, or alter reference images.
- Every mismatch produces an *explicit* candidate or is reported as
  unclassifiable — nothing is silently dropped.
- Confidence scores are deterministic and derived from the mismatch data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .diff_engine import DiffReport
from .generator_types import GeneratorManifest
from .ir_types import IRDocument, IRNode
from .layout_types import LayoutPlan, LayoutNodePlan

# ---------------------------------------------------------------------------
# Repair categories (exactly 9, per specification)
# ---------------------------------------------------------------------------

CATEGORY_GEOMETRY = "geometry"
CATEGORY_SPACING = "spacing"
CATEGORY_TYPOGRAPHY = "typography"
CATEGORY_COLOR = "color"
CATEGORY_TOKEN = "token"
CATEGORY_ASSET = "asset"
CATEGORY_RESPONSIVE = "responsive"
CATEGORY_MISSING_ELEMENT = "missing_element"
CATEGORY_EXTRA_ELEMENT = "extra_element"

ALL_CATEGORIES = frozenset({
    CATEGORY_GEOMETRY,
    CATEGORY_SPACING,
    CATEGORY_TYPOGRAPHY,
    CATEGORY_COLOR,
    CATEGORY_TOKEN,
    CATEGORY_ASSET,
    CATEGORY_RESPONSIVE,
    CATEGORY_MISSING_ELEMENT,
    CATEGORY_EXTRA_ELEMENT,
})

# Mismatch type → repair category mapping
_MISMATCH_TYPE_TO_CATEGORY: Dict[str, str] = {
    "geometry_mismatch": CATEGORY_GEOMETRY,
    "typography_mismatch": CATEGORY_TYPOGRAPHY,
    "missing_in_render": CATEGORY_MISSING_ELEMENT,
    "extra_in_render": CATEGORY_EXTRA_ELEMENT,
    "spacing_mismatch": CATEGORY_SPACING,
    "color_mismatch": CATEGORY_COLOR,
    "token_mismatch": CATEGORY_TOKEN,
    "asset_mismatch": CATEGORY_ASSET,
    "responsive_mismatch": CATEGORY_RESPONSIVE,
}

# Geometry sub-classification: when a geometry mismatch involves only small
# deltas it is more likely a spacing/padding issue than a structural one.
_GEOMETRY_DELTA_THRESHOLD = 4.0


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class SourceMapping:
    """Maps a repair candidate back to the source artifacts that produced it."""

    figma_node_id: str = ""
    component_name: str = ""
    source_file: str = ""
    css_selector: str = ""
    token_key: str = ""
    token_property: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "figma_node_id": self.figma_node_id,
            "component_name": self.component_name,
            "source_file": self.source_file,
            "css_selector": self.css_selector,
            "token_key": self.token_key,
            "token_property": self.token_property,
        }


@dataclass
class RepairCandidate:
    """A single classified difference with full source attribution."""

    candidate_id: str = ""
    category: str = ""
    node_id: str = ""
    description: str = ""
    source_mapping: SourceMapping = field(default_factory=SourceMapping)
    confidence: float = 1.0
    expected: Dict[str, Any] = field(default_factory=dict)
    actual: Dict[str, Any] = field(default_factory=dict)
    affected_properties: List[str] = field(default_factory=list)
    shared_token: bool = False  # True when multiple candidates share one token

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "category": self.category,
            "node_id": self.node_id,
            "description": self.description,
            "source_mapping": self.source_mapping.to_dict(),
            "confidence": self.confidence,
            "expected": dict(self.expected),
            "actual": dict(self.actual),
            "affected_properties": list(self.affected_properties),
            "shared_token": self.shared_token,
        }


@dataclass
class ClassificationReport:
    """The full output of classifying a DiffReport."""

    candidates: List[RepairCandidate] = field(default_factory=list)
    unclassifiable: List[Dict[str, Any]] = field(default_factory=list)
    total_mismatches: int = 0
    classified_count: int = 0

    @property
    def categories(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for c in self.candidates:
            counts[c.category] = counts.get(c.category, 0) + 1
        return counts

    def by_category(self, category: str) -> List[RepairCandidate]:
        return [c for c in self.candidates if c.category == category]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "unclassifiable": list(self.unclassifiable),
            "total_mismatches": self.total_mismatches,
            "classified_count": self.classified_count,
            "categories": self.categories,
        }


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class RepairClassifier:
    """Converts a :class:`DiffReport` into a :class:`ClassificationReport`.

    The classifier uses the layout plan, the IR document, and the generator
    manifest to attribute each mismatch back to its source artifacts.
    """

    def __init__(
        self,
        plan: Optional[LayoutPlan] = None,
        document: Optional[IRDocument] = None,
        manifest: Optional[GeneratorManifest] = None,
    ):
        self._plan = plan
        self._document = document
        self._manifest = manifest
        self._ir_index: Dict[str, IRNode] = {}
        self._candidate_counter: int = 0
        if document is not None:
            self._build_ir_index()

    def _build_ir_index(self) -> None:
        """Index IR nodes by id for fast lookup during attribution."""
        assert self._document is not None
        for node in self._document.all_nodes():
            self._ir_index[node.id] = node

    def classify(self, report: DiffReport) -> ClassificationReport:
        """Classify all mismatches in a diff report."""
        result = ClassificationReport(total_mismatches=len(report.mismatches))

        # Track token usage to detect shared tokens
        token_usage: Dict[str, List[str]] = {}  # token_key → [node_ids]
        raw_candidates: List[RepairCandidate] = []

        for mismatch in report.mismatches:
            candidate = self._classify_mismatch(mismatch)
            if candidate is None:
                result.unclassifiable.append(mismatch)
            else:
                raw_candidates.append(candidate)
                # Track token references for shared-token detection
                if candidate.source_mapping.token_key:
                    tk = candidate.source_mapping.token_key
                    token_usage.setdefault(tk, []).append(candidate.node_id)

        # Mark candidates that share a token with other candidates
        shared_tokens = {tk for tk, ids in token_usage.items() if len(ids) > 1}
        for candidate in raw_candidates:
            tk = candidate.source_mapping.token_key
            if tk in shared_tokens:
                candidate.shared_token = True
            self._candidate_counter += 1
            candidate.candidate_id = f"RC-{self._candidate_counter:04d}"
            result.candidates.append(candidate)

        result.classified_count = len(result.candidates)
        return result

    # ------------------------------------------------------------------
    # Per-mismatch classification
    # ------------------------------------------------------------------

    def _classify_mismatch(
        self, mismatch: Dict[str, Any],
    ) -> Optional[RepairCandidate]:
        """Classify a single mismatch dict into a RepairCandidate."""
        mismatch_type = mismatch.get("type", "")
        node_id = mismatch.get("node_id", "")

        # Determine category
        category = _MISMATCH_TYPE_TO_CATEGORY.get(mismatch_type)
        if category is None:
            return None

        # Build source mapping
        source = self._build_source_mapping(node_id, mismatch)

        # Refine category for geometry mismatches that look like spacing
        if category == CATEGORY_GEOMETRY:
            category = self._refine_geometry(mismatch, category)

        # Compute confidence from the mismatch data
        confidence = self._compute_confidence(mismatch, category)

        # Build affected properties list
        affected = self._extract_affected_properties(mismatch)

        # Build description
        description = self._build_description(category, mismatch)

        return RepairCandidate(
            category=category,
            node_id=node_id,
            description=description,
            source_mapping=source,
            confidence=confidence,
            expected=mismatch.get("expected", {}),
            actual=mismatch.get("actual", {}),
            affected_properties=affected,
        )

    # ------------------------------------------------------------------
    # Source attribution
    # ------------------------------------------------------------------

    def _build_source_mapping(
        self, node_id: str, mismatch: Dict[str, Any],
    ) -> SourceMapping:
        """Map a mismatch back to its source artifacts."""
        source = SourceMapping(figma_node_id=node_id)

        # Look up the IR node for richer metadata
        ir_node = self._ir_index.get(node_id)
        if ir_node is not None:
            # Check for bound tokens
            if ir_node.tokens and ir_node.tokens.bound_variables:
                first_prop = next(iter(ir_node.tokens.bound_variables))
                source.token_key = ir_node.tokens.bound_variables[first_prop]
                source.token_property = first_prop

        # Look up the generator manifest for source file
        if self._manifest is not None:
            filepath = self._manifest.components.get(node_id)
            if filepath:
                source.source_file = filepath
                source.css_selector = f'[data-figma-id="{node_id}"]'

        # Look up the layout plan for component name
        if self._plan is not None:
            node_plan = self._plan.node(node_id)
            if node_plan is not None and node_plan.name:
                source.component_name = node_plan.name

        return source

    # ------------------------------------------------------------------
    # Geometry refinement
    # ------------------------------------------------------------------

    def _refine_geometry(
        self, mismatch: Dict[str, Any], default: str,
    ) -> str:
        """Refine geometry mismatches into spacing when the delta is small.

        Small uniform deltas across siblings suggest a padding/gap issue
        rather than a structural layout problem.
        """
        expected = mismatch.get("expected", {})
        actual = mismatch.get("actual", {})
        if not expected or not actual:
            return default

        dw = abs(expected.get("w", 0) - actual.get("width", 0))
        dh = abs(expected.get("h", 0) - actual.get("height", 0))

        # Small uniform size delta → likely a spacing/padding issue
        if 0 < dw <= _GEOMETRY_DELTA_THRESHOLD and 0 < dh <= _GEOMETRY_DELTA_THRESHOLD:
            return CATEGORY_SPACING

        return default

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    def _compute_confidence(
        self, mismatch: Dict[str, Any], category: str,
    ) -> float:
        """Deterministic confidence score from mismatch data.

        Higher confidence when:
        - The mismatch type is well-defined (geometry, typography).
        - Expected and actual values are both present.
        - The node has a bound token (clear fix path).
        """
        confidence = 0.5  # base

        # Both expected and actual present → higher confidence
        if mismatch.get("expected") and mismatch.get("actual"):
            confidence += 0.2

        # Well-defined mismatch types
        if category in (CATEGORY_GEOMETRY, CATEGORY_TYPOGRAPHY, CATEGORY_MISSING_ELEMENT):
            confidence += 0.2
        elif category in (CATEGORY_SPACING, CATEGORY_COLOR):
            confidence += 0.1

        # Node has a bound token → clear fix path
        node_id = mismatch.get("node_id", "")
        ir_node = self._ir_index.get(node_id)
        if ir_node and ir_node.tokens and ir_node.tokens.bound_variables:
            confidence += 0.1

        return min(1.0, max(0.0, confidence))

    # ------------------------------------------------------------------
    # Affected properties
    # ------------------------------------------------------------------

    def _extract_affected_properties(
        self, mismatch: Dict[str, Any],
    ) -> List[str]:
        """Extract the list of CSS/style properties affected by this mismatch."""
        mismatch_type = mismatch.get("type", "")
        expected = mismatch.get("expected", {})

        if mismatch_type == "geometry_mismatch":
            props = []
            if "w" in expected or "width" in expected:
                props.append("width")
            if "h" in expected or "height" in expected:
                props.append("height")
            if "x" in expected:
                props.append("x")
            if "y" in expected:
                props.append("y")
            return props

        if mismatch_type == "typography_mismatch":
            return ["fontSize"]

        if mismatch_type == "missing_in_render":
            return ["display"]

        return []

    # ------------------------------------------------------------------
    # Description builder
    # ------------------------------------------------------------------

    def _build_description(
        self, category: str, mismatch: Dict[str, Any],
    ) -> str:
        """Build a human-readable description of the mismatch."""
        node_id = mismatch.get("node_id", "?")
        mismatch_type = mismatch.get("type", "unknown")

        if category == CATEGORY_GEOMETRY:
            expected = mismatch.get("expected", {})
            actual = mismatch.get("actual", {})
            return (
                f"Node {node_id}: geometry mismatch — "
                f"expected ({expected.get('w', '?')}x{expected.get('h', '?')}), "
                f"actual ({actual.get('width', '?')}x{actual.get('height', '?')})"
            )
        if category == CATEGORY_SPACING:
            return f"Node {node_id}: spacing/padding mismatch"
        if category == CATEGORY_TYPOGRAPHY:
            return f"Node {node_id}: typography mismatch"
        if category == CATEGORY_MISSING_ELEMENT:
            return f"Node {node_id}: present in design but missing in render"
        if category == CATEGORY_EXTRA_ELEMENT:
            return f"Node {node_id}: present in render but not in design"
        if category == CATEGORY_COLOR:
            return f"Node {node_id}: color mismatch"
        if category == CATEGORY_TOKEN:
            return f"Node {node_id}: design token mismatch"
        if category == CATEGORY_ASSET:
            return f"Node {node_id}: asset mismatch"
        if category == CATEGORY_RESPONSIVE:
            return f"Node {node_id}: responsive behavior mismatch"
        return f"Node {node_id}: {mismatch_type}"
