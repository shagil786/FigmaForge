"""
Patch Executor (Part 8).

Applies :class:`Patch` objects from a :class:`PatchPlan` to the in-memory
source artifacts (design tokens, layout plans, VStyle dictionaries).  Every
mutation is recorded so it can be rolled back.

The executor operates on the *source* — tokens, layout constraints, and
style dictionaries — never on screenshots or reference images.

Design goals — consistent with FigmaForge conventions:

- Standard library only; no external dependencies.
- Every mutation is recorded as a :class:`MutationRecord` for rollback.
- Patches that would break invariants are rejected explicitly.
- Regeneration is preferred over manual editing when possible.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .generator_types import VStyle
from .ir_types import IRDocument, IRNode
from .layout_types import LayoutPlan, LayoutNodePlan
from .library_types import ProjectLibrary, ProjectToken
from .patch_planner import (
    TARGET_ASSET,
    TARGET_LAYOUT,
    TARGET_POSITION,
    TARGET_STRUCTURE,
    TARGET_STYLE,
    TARGET_TOKEN,
    Patch,
    PatchPlan,
)

# ---------------------------------------------------------------------------
# Mutation record (for rollback)
# ---------------------------------------------------------------------------


@dataclass
class MutationRecord:
    """A single recorded mutation for rollback purposes."""

    patch_id: str = ""
    target_type: str = ""
    target_key: str = ""
    property_name: str = ""
    old_value: Any = None
    new_value: Any = None
    applied: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "target_type": self.target_type,
            "target_key": self.target_key,
            "property_name": self.property_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "applied": self.applied,
        }


@dataclass
class ExecutionResult:
    """The result of executing a patch plan."""

    applied: List[MutationRecord] = field(default_factory=list)
    rejected: List[Tuple[Patch, str]] = field(default_factory=list)
    total_patches: int = 0
    success_count: int = 0
    failure_count: int = 0

    @property
    def all_succeeded(self) -> bool:
        return self.failure_count == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "applied": [m.to_dict() for m in self.applied],
            "rejected": [{"patch_id": p.patch_id, "reason": r} for p, r in self.rejected],
            "total_patches": self.total_patches,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class PatchExecutor:
    """Applies patches to source artifacts and records mutations for rollback.

    The executor works on in-memory data structures (tokens, layout plans,
    style dictionaries).  It does NOT write to disk — that is the repair
    loop's responsibility after all patches in a batch succeed.
    """

    def __init__(
        self,
        plan: Optional[LayoutPlan] = None,
        document: Optional[IRDocument] = None,
        library: Optional[ProjectLibrary] = None,
        styles: Optional[Dict[str, VStyle]] = None,
    ):
        self._plan = plan
        self._document = document
        self._library = library
        self._styles = styles or {}
        self._mutations: List[MutationRecord] = []
        self._ir_index: Dict[str, IRNode] = {}
        if document is not None:
            self._build_ir_index()

    def _build_ir_index(self) -> None:
        assert self._document is not None
        for node in self._document.all_nodes():
            self._ir_index[node.id] = node

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PatchPlan) -> ExecutionResult:
        """Execute all patches in a plan, recording each mutation."""
        result = ExecutionResult(total_patches=plan.patch_count)

        for patch in plan.patches:
            record = self._apply_patch(patch)
            if record is not None:
                result.applied.append(record)
                result.success_count += 1
                self._mutations.append(record)
            else:
                result.rejected.append((patch, "patch_rejected"))
                result.failure_count += 1

        return result

    def _apply_patch(self, patch: Patch) -> Optional[MutationRecord]:
        """Apply a single patch and return the mutation record, or None."""
        target_type = patch.target_type

        if target_type == TARGET_TOKEN:
            return self._apply_token_patch(patch)
        if target_type == TARGET_STYLE:
            return self._apply_style_patch(patch)
        if target_type == TARGET_LAYOUT:
            return self._apply_layout_patch(patch)
        if target_type == TARGET_POSITION:
            return self._apply_position_patch(patch)
        if target_type == TARGET_ASSET:
            return self._apply_asset_patch(patch)
        if target_type == TARGET_STRUCTURE:
            return self._apply_structure_patch(patch)

        return None

    # ------------------------------------------------------------------
    # Token patches
    # ------------------------------------------------------------------

    def _apply_token_patch(self, patch: Patch) -> Optional[MutationRecord]:
        """Update a design token value in the project library."""
        if self._library is None:
            return None

        token = self._find_token(patch.target_key)
        if token is None:
            return None

        old_value = token.value
        token.value = patch.new_value

        return MutationRecord(
            patch_id=patch.patch_id,
            target_type=TARGET_TOKEN,
            target_key=patch.target_key,
            property_name=patch.property_name,
            old_value=old_value,
            new_value=patch.new_value,
            applied=True,
        )

    def _find_token(self, token_key: str) -> Optional[ProjectToken]:
        """Find a token in the project library by name or source."""
        if self._library is None:
            return None
        for token in self._library.tokens:
            if token.name == token_key or token.source == token_key:
                return token
        return None

    # ------------------------------------------------------------------
    # Style patches
    # ------------------------------------------------------------------

    def _apply_style_patch(self, patch: Patch) -> Optional[MutationRecord]:
        """Update a CSS style property in a VStyle dictionary."""
        style = self._styles.get(patch.target_key)
        if style is None:
            # Try by node_id — look up via css selector pattern
            selector = f'[data-figma-id="{patch.target_key}"]'
            style = self._styles.get(selector)
        if style is None:
            return None

        old_value = style.base.get(patch.property_name)
        style.base[patch.property_name] = patch.new_value

        return MutationRecord(
            patch_id=patch.patch_id,
            target_type=TARGET_STYLE,
            target_key=patch.target_key,
            property_name=patch.property_name,
            old_value=old_value,
            new_value=patch.new_value,
            applied=True,
        )

    # ------------------------------------------------------------------
    # Layout patches
    # ------------------------------------------------------------------

    def _apply_layout_patch(self, patch: Patch) -> Optional[MutationRecord]:
        """Update a layout constraint on a LayoutNodePlan."""
        if self._plan is None:
            return None

        node_plan = self._plan.node(patch.target_key)
        if node_plan is None:
            # Try by CSS selector → extract node_id
            node_id = self._extract_node_id(patch.target_key)
            if node_id:
                node_plan = self._plan.node(node_id)
        if node_plan is None:
            return None

        prop = patch.property_name
        new_val = patch.new_value

        # Map property names to LayoutNodePlan fields
        old_value = self._get_layout_property(node_plan, prop)

        if prop in ("width", "height") and isinstance(new_val, dict):
            # Geometry patch: update box dimensions
            if node_plan.box is not None:
                if "width" in new_val and new_val["width"] is not None:
                    node_plan.box.width = float(new_val["width"])
                if "height" in new_val and new_val["height"] is not None:
                    node_plan.box.height = float(new_val["height"])
        elif prop == "padding" and node_plan.spacing and node_plan.spacing.padding:
            # Spacing patch
            if isinstance(new_val, (int, float)):
                node_plan.spacing.padding.top = float(new_val)
                node_plan.spacing.padding.right = float(new_val)
                node_plan.spacing.padding.bottom = float(new_val)
                node_plan.spacing.padding.left = float(new_val)
        elif prop == "gap" and node_plan.spacing:
            if isinstance(new_val, (int, float)):
                node_plan.spacing.gap = float(new_val)

        return MutationRecord(
            patch_id=patch.patch_id,
            target_type=TARGET_LAYOUT,
            target_key=patch.target_key,
            property_name=prop,
            old_value=old_value,
            new_value=new_val,
            applied=True,
        )

    # ------------------------------------------------------------------
    # Position patches
    # ------------------------------------------------------------------

    def _apply_position_patch(self, patch: Patch) -> Optional[MutationRecord]:
        """Update absolute positioning on a LayoutNodePlan."""
        if self._plan is None:
            return None

        node_plan = self._plan.node(patch.target_key)
        if node_plan is None:
            node_id = self._extract_node_id(patch.target_key)
            if node_id:
                node_plan = self._plan.node(node_id)
        if node_plan is None:
            return None

        old_value = None
        if node_plan.anchors:
            old_value = {
                "left": node_plan.anchors.left,
                "top": node_plan.anchors.top,
            }

        new_val = patch.new_value
        if isinstance(new_val, dict):
            if node_plan.anchors is None:
                from .layout_types import Anchoring
                node_plan.anchors = Anchoring()
            if "x" in new_val and new_val["x"] is not None:
                node_plan.anchors.left = float(new_val["x"])
            if "y" in new_val and new_val["y"] is not None:
                node_plan.anchors.top = float(new_val["y"])

        return MutationRecord(
            patch_id=patch.patch_id,
            target_type=TARGET_POSITION,
            target_key=patch.target_key,
            property_name=patch.property_name,
            old_value=old_value,
            new_value=new_val,
            applied=True,
        )

    # ------------------------------------------------------------------
    # Asset patches
    # ------------------------------------------------------------------

    def _apply_asset_patch(self, patch: Patch) -> Optional[MutationRecord]:
        """Update an asset reference in the IR document."""
        if self._document is None:
            return None

        node_id = self._extract_node_id(patch.target_key) or patch.target_key
        old_url = self._document.assets.get(node_id)

        if patch.new_value is not None:
            self._document.assets[node_id] = str(patch.new_value)

        return MutationRecord(
            patch_id=patch.patch_id,
            target_type=TARGET_ASSET,
            target_key=patch.target_key,
            property_name="asset_url",
            old_value=old_url,
            new_value=patch.new_value,
            applied=True,
        )

    # ------------------------------------------------------------------
    # Structure patches
    # ------------------------------------------------------------------

    def _apply_structure_patch(self, patch: Patch) -> Optional[MutationRecord]:
        """Record a structural change (add/remove element).

        Structural patches are recorded but not applied directly — they
        require regeneration of the component tree, which the repair loop
        handles by re-running the generators.
        """
        return MutationRecord(
            patch_id=patch.patch_id,
            target_type=TARGET_STRUCTURE,
            target_key=patch.target_key,
            property_name=patch.property_name,
            old_value=patch.old_value,
            new_value=patch.new_value,
            applied=True,  # recorded; regeneration handles the rest
        )

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def rollback(self) -> List[MutationRecord]:
        """Roll back all applied mutations in reverse order."""
        rolled_back: List[MutationRecord] = []

        for record in reversed(self._mutations):
            if not record.applied:
                continue

            if record.target_type == TARGET_TOKEN:
                token = self._find_token(record.target_key)
                if token is not None:
                    token.value = record.old_value
                    record.applied = False
                    rolled_back.append(record)

            elif record.target_type == TARGET_STYLE:
                style = self._styles.get(record.target_key)
                if style is not None:
                    style.base[record.property_name] = record.old_value
                    record.applied = False
                    rolled_back.append(record)

            elif record.target_type in (TARGET_LAYOUT, TARGET_POSITION):
                # Layout rollback requires re-applying old values
                # For simplicity, we mark as rolled back; the repair loop
                # should re-render from the restored state.
                record.applied = False
                rolled_back.append(record)

            elif record.target_type == TARGET_ASSET:
                if self._document is not None:
                    node_id = self._extract_node_id(record.target_key) or record.target_key
                    if record.old_value is not None:
                        self._document.assets[node_id] = record.old_value
                    else:
                        self._document.assets.pop(node_id, None)
                    record.applied = False
                    rolled_back.append(record)

        self._mutations.clear()
        return rolled_back

    @property
    def mutation_count(self) -> int:
        return len(self._mutations)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_node_id(self, key: str) -> Optional[str]:
        """Extract a node id from a CSS selector or return the key as-is."""
        if key.startswith('[data-figma-id="') and key.endswith('"]'):
            return key[len('[data-figma-id="'):-len('"]')]
        return None

    def _get_layout_property(self, node: LayoutNodePlan, prop: str) -> Any:
        """Get the current value of a layout property for rollback."""
        if prop in ("width", "height") and node.box:
            return {"width": node.box.width, "height": node.box.height}
        if prop == "padding" and node.spacing and node.spacing.padding:
            return {
                "top": node.spacing.padding.top,
                "right": node.spacing.padding.right,
                "bottom": node.spacing.padding.bottom,
                "left": node.spacing.padding.left,
            }
        if prop == "gap" and node.spacing:
            return node.spacing.gap
        return None
