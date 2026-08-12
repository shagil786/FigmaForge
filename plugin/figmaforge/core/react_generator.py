"""
React Code Generator (Part 6).

Transforms a fully-resolved LayoutPlan into a hierarchical VNode (Virtual DOM)
tree that represents the component structure of the design.
"""

from __future__ import annotations

from typing import Dict, Optional

from .generator_types import VNode
from .layout_types import DISPLAY_FLEX, DISPLAY_GRID, LayoutNodePlan
from .resolver import ResolutionReport

# Name tokens -> semantic HTML tag (requirement: semantic, not
# screenshot-specific markup). Names are matched case-insensitively on the
# figma node name; unknown containers fall back to ``div``.
_SEMANTIC_TAG_BY_NAME = {
    "header": "header",
    "nav": "nav",
    "hero": "section",
    "main": "main",
    "content": "main",
    "section": "section",
    "card": "section",
    "aside": "aside",
    "footer": "footer",
}


class ReactGenerator:
    """Orchestrates the conversion of a LayoutPlan into a VNode tree."""

    def __init__(self, resolution: Optional[ResolutionReport] = None):
        """Initialize with an optional Part-4 resolution report.

        When a report is provided, nodes that resolved to project components
        are emitted with ``is_component=True`` and the component name as the
        tag, wiring the resolution pipeline into code generation.
        """
        self._component_names: Dict[str, str] = {}
        if resolution is not None:
            self._index_resolution(resolution)

    def _index_resolution(self, report: ResolutionReport) -> None:
        """Build a lookup from Figma component/instance ids to component names."""
        # Resolved component definitions: figma_component_id → library name
        for match in report.resolved:
            if match.matches:
                self._component_names[match.figma_component] = match.matches[0]

        # Resolved instances: instance node_id → resolved component name
        for inst in report.instances:
            if inst.get("status") == "resolved" and inst.get("resolved_name"):
                self._component_names[inst["node_id"]] = inst["resolved_name"]

    def generate(self, plan: LayoutNodePlan) -> VNode:
        """Entry point: converts a plan into a root VNode."""
        return self._build_node(plan)

    def _build_node(self, plan: LayoutNodePlan) -> VNode:
        """Recursive node builder."""

        # 1. Determine tag/component name
        is_component, tag = self._resolve_tag(plan)

        # 2. Build props
        props = {}
        if plan.node_id:
            props["data-figma-id"] = plan.node_id
        if plan.name:
            props["name"] = plan.name

        # 3. Build VNode
        node = VNode(
            node_id=plan.node_id,
            tag=tag,
            is_component=is_component,
            props=props,
        )

        # 4. Add children
        for child in plan.children:
            node.children.append(self._build_node(child))

        # 5. Add text content for text nodes
        if plan.text and plan.text.characters:
            node.text_content = plan.text.characters

        return node

    def _resolve_tag(self, plan: LayoutNodePlan) -> tuple:
        """Return (is_component, tag) for this plan node.

        If a resolution report was provided at init and this node maps to a
        resolved project component, returns ``(True, component_name)``.
        Otherwise falls back to semantic HTML tag mapping.
        """
        # Check resolution report first
        if plan.node_id and plan.node_id in self._component_names:
            return True, self._component_names[plan.node_id]

        # Fall back to semantic HTML tag mapping
        return False, self._get_tag_for(plan)

    def _get_tag_for(self, plan: LayoutNodePlan) -> str:
        """Map layout/IR kind to a semantic tag.

        Text renders as ``span``; containers match their name against the
        semantic tag table (``Header`` -> ``<header>``, ``Footer`` -> ``<footer>``)
        before falling back to ``div``.
        """
        if plan.kind == "text":
            return "span"
        if plan.display in (DISPLAY_FLEX, DISPLAY_GRID) and plan.name:
            tag = _SEMANTIC_TAG_BY_NAME.get(plan.name.lower())
            if tag is not None:
                return tag
        return "div"
