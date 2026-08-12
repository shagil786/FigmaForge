"""
React Code Generator (Part 6).

Transforms a fully-resolved LayoutPlan into a hierarchical VNode (Virtual DOM)
tree that represents the component structure of the design.
"""

from __future__ import annotations

from .generator_types import VNode
from .layout_types import DISPLAY_FLEX, DISPLAY_GRID, LayoutNodePlan

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

    def generate(self, plan: LayoutNodePlan) -> VNode:
        """Entry point: converts a plan into a root VNode."""
        return self._build_node(plan)

    def _build_node(self, plan: LayoutNodePlan) -> VNode:
        """Recursive node builder."""

        # 1. Determine tag/component name
        tag = self._get_tag_for(plan)
        is_component = self._is_component(plan)

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

    def _is_component(self, plan: LayoutNodePlan) -> bool:
        """True when this node maps to an existing library component.

        Integration with the Part 4 ``ResolutionReport`` is a future step;
        currently nothing is a resolved component.
        """
        return False
