"""
Patch Planner (Part 8).

Converts classified :class:`RepairCandidate` objects into an ordered
:class:`PatchPlan`.  The planner enforces the repair-strategy priority:

1. Global environment mismatches first.
2. Missing / extra elements second.
3. Parent geometry before child geometry.
4. Shared tokens before local styles.
5. Layout constraints before absolute coordinates.
6. Typography before fine pixel offsets.
7. Assets before color tuning.

The planner prefers the *smallest possible source-level patch* and groups
candidates that can be fixed together in a single batch.

Design goals — consistent with FigmaForge conventions:

- Standard library only; no external dependencies.
- Deterministic ordering (stable sort keys, no randomness).
- Every candidate is either included in a patch or explicitly skipped with
  a reason — nothing is silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .ir_types import IRDocument, IRNode
from .layout_types import LayoutPlan, LayoutNodePlan
from .library_types import ProjectLibrary, ProjectToken
from .repair_classifier import (
    ALL_CATEGORIES,
    CATEGORY_ASSET,
    CATEGORY_COLOR,
    CATEGORY_EXTRA_ELEMENT,
    CATEGORY_GEOMETRY,
    CATEGORY_MISSING_ELEMENT,
    CATEGORY_RESPONSIVE,
    CATEGORY_SPACING,
    CATEGORY_TOKEN,
    CATEGORY_TYPOGRAPHY,
    ClassificationReport,
    RepairCandidate,
    SourceMapping,
)

# ---------------------------------------------------------------------------
# Patch data types
# ---------------------------------------------------------------------------

# Patch targets — what kind of source artifact the patch modifies
TARGET_TOKEN = "token"           # design token value
TARGET_STYLE = "style"           # local CSS/style property
TARGET_LAYOUT = "layout"         # layout constraint (display, flex, grid)
TARGET_POSITION = "position"     # absolute positioning
TARGET_ASSET = "asset"           # asset reference
TARGET_STRUCTURE = "structure"   # add/remove element


@dataclass
class Patch:
    """A single source-level patch operation."""

    patch_id: str = ""
    target_type: str = ""        # TARGET_* constant
    target_key: str = ""         # token key, CSS selector, or node id
    property_name: str = ""      # which property to change
    old_value: Any = None        # current value (for rollback)
    new_value: Any = None        # desired value
    candidate_ids: List[str] = field(default_factory=list)
    reason: str = ""
    is_shared: bool = False      # True when patch fixes multiple candidates

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "target_type": self.target_type,
            "target_key": self.target_key,
            "property_name": self.property_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "candidate_ids": list(self.candidate_ids),
            "reason": self.reason,
            "is_shared": self.is_shared,
        }


@dataclass
class SkippedCandidate:
    """A candidate that was not included in any patch, with reason."""

    candidate_id: str = ""
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"candidate_id": self.candidate_id, "reason": self.reason}


@dataclass
class PatchPlan:
    """An ordered plan of patches to apply in one repair batch."""

    iteration: int = 0
    patches: List[Patch] = field(default_factory=list)
    skipped: List[SkippedCandidate] = field(default_factory=list)
    total_candidates: int = 0
    strategy_order: List[str] = field(default_factory=list)

    @property
    def patch_count(self) -> int:
        return len(self.patches)

    @property
    def is_empty(self) -> bool:
        return len(self.patches) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration,
            "patches": [p.to_dict() for p in self.patches],
            "skipped": [s.to_dict() for s in self.skipped],
            "total_candidates": self.total_candidates,
            "strategy_order": list(self.strategy_order),
        }


# ---------------------------------------------------------------------------
# Strategy priority (lower index = applied first)
# ---------------------------------------------------------------------------

# Category → sort priority.  Categories not listed get the lowest priority.
_STRATEGY_PRIORITY: List[Tuple[str, int]] = [
    # 1. Missing/extra elements (structural, must fix before visual tweaks)
    (CATEGORY_MISSING_ELEMENT, 10),
    (CATEGORY_EXTRA_ELEMENT, 11),
    # 2. Geometry (parent before child — handled by depth sort later)
    (CATEGORY_GEOMETRY, 20),
    # 3. Spacing
    (CATEGORY_SPACING, 30),
    # 4. Shared tokens (before local styles)
    (CATEGORY_TOKEN, 40),
    # 5. Typography (before fine pixel offsets)
    (CATEGORY_TYPOGRAPHY, 50),
    # 6. Assets (before color tuning)
    (CATEGORY_ASSET, 60),
    # 7. Color
    (CATEGORY_COLOR, 70),
    # 8. Responsive (lowest — requires viewport re-render)
    (CATEGORY_RESPONSIVE, 80),
]

_CATEGORY_PRIORITY: Dict[str, int] = dict(_STRATEGY_PRIORITY)

# Patch target type → sort priority within the same category
_TARGET_PRIORITY: Dict[str, int] = {
    TARGET_STRUCTURE: 0,
    TARGET_TOKEN: 1,
    TARGET_LAYOUT: 2,
    TARGET_POSITION: 3,
    TARGET_STYLE: 4,
    TARGET_ASSET: 5,
}


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class PatchPlanner:
    """Converts a :class:`ClassificationReport` into an ordered :class:`PatchPlan`.

    The planner uses the layout plan and project library to determine the
    correct patch target type and to detect shared-token opportunities.
    """

    def __init__(
        self,
        plan: Optional[LayoutPlan] = None,
        document: Optional[IRDocument] = None,
        library: Optional[ProjectLibrary] = None,
    ):
        self._plan = plan
        self._document = document
        self._library = library
        self._ir_index: Dict[str, IRNode] = {}
        self._patch_counter: int = 0
        if document is not None:
            self._build_ir_index()

    def _build_ir_index(self) -> None:
        assert self._document is not None
        for node in self._document.all_nodes():
            self._ir_index[node.id] = node

    def plan(
        self,
        classification: ClassificationReport,
        iteration: int = 0,
    ) -> PatchPlan:
        """Create an ordered patch plan from classified candidates."""
        result = PatchPlan(iteration=iteration)
        result.total_candidates = len(classification.candidates)

        if not classification.candidates:
            return result

        # Step 1: Sort candidates by strategy priority
        sorted_candidates = self._sort_by_priority(classification.candidates)

        # Step 2: Group shared-token candidates
        token_groups = self._group_by_shared_token(sorted_candidates)

        # Step 3: Generate patches
        patched_candidate_ids: Set[str] = set()

        # 3a: Generate shared-token patches first (they fix multiple candidates)
        for token_key, candidates in token_groups.items():
            if len(candidates) > 1:
                patch = self._create_shared_token_patch(token_key, candidates)
                if patch is not None:
                    result.patches.append(patch)
                    for c in candidates:
                        patched_candidate_ids.add(c.candidate_id)

        # 3b: Generate individual patches for remaining candidates
        for candidate in sorted_candidates:
            if candidate.candidate_id in patched_candidate_ids:
                continue
            patch = self._create_patch(candidate)
            if patch is not None:
                result.patches.append(patch)
                patched_candidate_ids.add(candidate.candidate_id)
            else:
                result.skipped.append(SkippedCandidate(
                    candidate_id=candidate.candidate_id,
                    reason="no_safe_patch",
                ))

        # Step 4: Record the strategy order that was applied
        result.strategy_order = [
            cat for cat, _ in _STRATEGY_PRIORITY
            if any(c.category == cat for c in classification.candidates)
        ]

        return result

    # ------------------------------------------------------------------
    # Priority sorting
    # ------------------------------------------------------------------

    def _sort_by_priority(
        self, candidates: List[RepairCandidate],
    ) -> List[RepairCandidate]:
        """Sort candidates by strategy priority, then by tree depth."""
        def sort_key(c: RepairCandidate) -> Tuple[int, int, str]:
            cat_priority = _CATEGORY_PRIORITY.get(c.category, 90)
            depth = self._get_node_depth(c.node_id)
            return (cat_priority, depth, c.node_id)

        return sorted(candidates, key=sort_key)

    def _get_node_depth(self, node_id: str) -> int:
        """Get the depth of a node in the layout plan tree (0 = root)."""
        if self._plan is None:
            return 0
        # Walk the tree to find the node and its depth
        for screen in self._plan.screens:
            depth = self._find_depth(screen, node_id, 0)
            if depth >= 0:
                return depth
        return 0

    def _find_depth(
        self, node: LayoutNodePlan, target_id: str, depth: int,
    ) -> int:
        if node.node_id == target_id:
            return depth
        for child in node.children:
            result = self._find_depth(child, target_id, depth + 1)
            if result >= 0:
                return result
        return -1

    # ------------------------------------------------------------------
    # Shared-token grouping
    # ------------------------------------------------------------------

    def _group_by_shared_token(
        self, candidates: List[RepairCandidate],
    ) -> Dict[str, List[RepairCandidate]]:
        """Group candidates that share the same design token."""
        groups: Dict[str, List[RepairCandidate]] = {}
        for c in candidates:
            if c.shared_token and c.source_mapping.token_key:
                tk = c.source_mapping.token_key
                groups.setdefault(tk, []).append(c)
        return groups

    def _create_shared_token_patch(
        self, token_key: str, candidates: List[RepairCandidate],
    ) -> Optional[Patch]:
        """Create a single patch that fixes a shared token for multiple candidates."""
        self._patch_counter += 1

        # Determine the new value from the first candidate's expected value
        new_value = self._extract_token_value(token_key, candidates[0])
        old_value = self._get_current_token_value(token_key)

        if new_value is None:
            return None

        return Patch(
            patch_id=f"P-{self._patch_counter:04d}",
            target_type=TARGET_TOKEN,
            target_key=token_key,
            property_name=candidates[0].source_mapping.token_property,
            old_value=old_value,
            new_value=new_value,
            candidate_ids=[c.candidate_id for c in candidates],
            reason=f"Shared token fix: {token_key} resolves {len(candidates)} candidates",
            is_shared=True,
        )

    # ------------------------------------------------------------------
    # Individual patch creation
    # ------------------------------------------------------------------

    def _create_patch(
        self, candidate: RepairCandidate,
    ) -> Optional[Patch]:
        """Create a single patch for a candidate."""
        self._patch_counter += 1

        # Determine target type based on category and source mapping
        target_type = self._determine_target_type(candidate)
        target_key = self._determine_target_key(candidate)
        property_name = self._determine_property(candidate)
        new_value = self._determine_new_value(candidate)

        if new_value is None and target_type != TARGET_STRUCTURE:
            return None

        return Patch(
            patch_id=f"P-{self._patch_counter:04d}",
            target_type=target_type,
            target_key=target_key,
            property_name=property_name,
            old_value=self._get_current_value(candidate),
            new_value=new_value,
            candidate_ids=[candidate.candidate_id],
            reason=candidate.description,
            is_shared=False,
        )

    def _determine_target_type(self, candidate: RepairCandidate) -> str:
        """Determine what kind of source artifact to patch."""
        cat = candidate.category

        if cat in (CATEGORY_MISSING_ELEMENT, CATEGORY_EXTRA_ELEMENT):
            return TARGET_STRUCTURE
        if candidate.source_mapping.token_key and candidate.shared_token:
            return TARGET_TOKEN
        if cat == CATEGORY_GEOMETRY:
            # Check if this is an absolute-positioned node
            ir_node = self._ir_index.get(candidate.node_id)
            if ir_node and ir_node.position and ir_node.position.mode == "absolute":
                return TARGET_POSITION
            return TARGET_LAYOUT
        if cat == CATEGORY_SPACING:
            return TARGET_LAYOUT
        if cat == CATEGORY_TYPOGRAPHY:
            if candidate.source_mapping.token_key:
                return TARGET_TOKEN
            return TARGET_STYLE
        if cat == CATEGORY_ASSET:
            return TARGET_ASSET
        if cat == CATEGORY_COLOR:
            if candidate.source_mapping.token_key:
                return TARGET_TOKEN
            return TARGET_STYLE
        if cat == CATEGORY_TOKEN:
            return TARGET_TOKEN
        if cat == CATEGORY_RESPONSIVE:
            return TARGET_LAYOUT
        return TARGET_STYLE

    def _determine_target_key(self, candidate: RepairCandidate) -> str:
        """Determine the specific artifact to patch."""
        if candidate.source_mapping.token_key:
            return candidate.source_mapping.token_key
        if candidate.source_mapping.css_selector:
            return candidate.source_mapping.css_selector
        return candidate.node_id

    def _determine_property(self, candidate: RepairCandidate) -> str:
        """Determine which property to change."""
        if candidate.source_mapping.token_property:
            return candidate.source_mapping.token_property
        if candidate.affected_properties:
            return candidate.affected_properties[0]
        # Category-based fallback
        cat = candidate.category
        if cat == CATEGORY_GEOMETRY:
            return "width"
        if cat == CATEGORY_SPACING:
            return "padding"
        if cat == CATEGORY_TYPOGRAPHY:
            return "fontSize"
        if cat == CATEGORY_COLOR:
            return "color"
        return ""

    def _determine_new_value(self, candidate: RepairCandidate) -> Any:
        """Determine the desired new value from the expected data."""
        expected = candidate.expected
        if not expected:
            return None

        cat = candidate.category
        if cat == CATEGORY_GEOMETRY:
            return {
                "width": expected.get("w"),
                "height": expected.get("h"),
            }
        if cat == CATEGORY_TYPOGRAPHY:
            # Look up the IR node for the expected font size
            ir_node = self._ir_index.get(candidate.node_id)
            if ir_node and ir_node.typography and ir_node.typography.font_size:
                return ir_node.typography.font_size
            return None
        if cat in (CATEGORY_MISSING_ELEMENT, CATEGORY_EXTRA_ELEMENT):
            return None  # structural patches don't have simple values
        return expected

    def _get_current_value(self, candidate: RepairCandidate) -> Any:
        """Get the current value for rollback purposes."""
        return candidate.actual

    def _extract_token_value(
        self, token_key: str, candidate: RepairCandidate,
    ) -> Any:
        """Extract the expected value for a shared token."""
        # Look up the token in the project library
        if self._library is not None:
            for token in self._library.tokens:
                if token.name == token_key or token.source == token_key:
                    return token.value
        # Fall back to the candidate's expected value
        return candidate.expected

    def _get_current_token_value(self, token_key: str) -> Any:
        """Get the current value of a token for rollback."""
        if self._library is not None:
            for token in self._library.tokens:
                if token.name == token_key or token.source == token_key:
                    return token.value
        return None
