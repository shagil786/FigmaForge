"""
CSS Style Generator (Part 6).

Transforms a LayoutPlan's constraints, sizing, spacing, and alignment into
abstract CSS-ready style dictionaries (VStyle).
"""

from __future__ import annotations

from .generator_types import VStyle
from .layout_types import (
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    DISPLAY_GRID,
    LayoutNodePlan,
    SIZING_FIXED,
)


class CSSGenerator:
    """Orchestrates the conversion of a LayoutPlan into a VStyle dictionary."""

    def generate_style(self, plan: LayoutNodePlan) -> VStyle:
        """Entry point: converts a plan into a VStyle object."""
        style = VStyle()

        # 1. Base Size & Display
        display = self._map_display(plan)
        style.base["display"] = display

        if plan.box:
            if plan.sizing and plan.sizing.horizontal and plan.sizing.horizontal.mode == SIZING_FIXED:
                style.base["width"] = f"{plan.box.width}px"
            if plan.sizing and plan.sizing.vertical and plan.sizing.vertical.mode == SIZING_FIXED:
                style.base["height"] = f"{plan.box.height}px"

        # 2. Spacing (Padding/Gap)
        if plan.spacing:
            if plan.spacing.padding:
                p = plan.spacing.padding
                if p.top is not None: style.base["paddingTop"] = f"{p.top}px"
                if p.right is not None: style.base["paddingRight"] = f"{p.right}px"
                if p.bottom is not None: style.base["paddingBottom"] = f"{p.bottom}px"
                if p.left is not None: style.base["paddingLeft"] = f"{p.left}px"
            if plan.spacing.gap:
                style.base["gap"] = f"{plan.spacing.gap}px"

        # 3. Flex/Grid Layout
        if display == "flex":
            style.base["flexDirection"] = plan.direction or "row"
            if plan.alignment:
                if plan.alignment.justify:
                     style.base["justifyContent"] = self._map_justify(plan.alignment.justify)
                if plan.alignment.align:
                     style.base["alignItems"] = self._map_align(plan.alignment.align)

        # 4. Absolute Positioning (only where the solver requires it).
        if display == "absolute" and plan.box:
            style.base["position"] = "absolute"
            if plan.anchors:
                a = plan.anchors
                if a.left is not None:
                    style.base["left"] = f"{a.left}px"
                elif a.right is not None:
                    style.base["right"] = f"{a.right}px"
                if a.top is not None:
                    style.base["top"] = f"{a.top}px"
                elif a.bottom is not None:
                    style.base["bottom"] = f"{a.bottom}px"

        return style

    def _map_display(self, plan: LayoutNodePlan) -> str:
        if plan.display == DISPLAY_FLEX: return "flex"
        if plan.display == DISPLAY_GRID: return "grid"
        if plan.display == DISPLAY_ABSOLUTE: return "absolute"
        return "block"

    def _map_justify(self, justify: str) -> str:
        mapping = {"MIN": "flex-start", "MAX": "flex-end", "CENTER": "center", "SPACE_BETWEEN": "space-between"}
        return mapping.get(justify, "flex-start")

    def _map_align(self, align: str) -> str:
        mapping = {"MIN": "flex-start", "MAX": "flex-end", "CENTER": "center", "STRETCH": "stretch"}
        return mapping.get(align, "flex-start")
