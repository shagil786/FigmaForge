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
    SIZING_FILL,
    SIZING_HUG,
    SIZING_PERCENT,
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
            self._apply_sizing(style, plan, display)

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

        # 3. Flex Layout
        if display == "flex":
            style.base["flexDirection"] = plan.direction or "row"
            if plan.alignment:
                if plan.alignment.justify:
                     style.base["justifyContent"] = self._map_justify(plan.alignment.justify)
                if plan.alignment.align:
                     style.base["alignItems"] = self._map_align(plan.alignment.align)

        # 4. Grid Layout
        if display == "grid":
            if plan.direction:
                style.base["gridAutoFlow"] = (
                    "column" if plan.direction == "column" else "row"
                )
            if plan.spacing and plan.spacing.gap:
                # gap already set above; add explicit column/row gap aliases
                style.base["columnGap"] = f"{plan.spacing.gap}px"
                style.base["rowGap"] = f"{plan.spacing.gap}px"
            if plan.alignment:
                if plan.alignment.justify:
                    style.base["justifyItems"] = self._map_justify(plan.alignment.justify)
                if plan.alignment.align:
                    style.base["alignItems"] = self._map_align(plan.alignment.align)

        # 5. Absolute Positioning (only where the solver requires it).
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

    # --------------------------------------------------------- sizing helpers

    def _apply_sizing(
        self, style: VStyle, plan: LayoutNodePlan, display: str,
    ) -> None:
        """Emit width/height CSS for all sizing modes."""
        if not plan.sizing:
            # Fallback to fixed box dimensions when no sizing model exists.
            if plan.box:
                style.base["width"] = f"{plan.box.width}px"
                style.base["height"] = f"{plan.box.height}px"
            return

        h = plan.sizing.horizontal
        v = plan.sizing.vertical

        # --- Horizontal (width) ---
        if h:
            if h.mode == SIZING_FIXED:
                style.base["width"] = f"{plan.box.width}px"
            elif h.mode == SIZING_FILL:
                if display == "flex":
                    style.base["flex"] = "1 1 0%"
                else:
                    style.base["width"] = "100%"
            elif h.mode == SIZING_HUG:
                style.base["width"] = "fit-content"
            elif h.mode == SIZING_PERCENT and h.value is not None:
                style.base["width"] = f"{h.value * 100:.2f}%"
            # Apply min/max clamps
            if h.min is not None:
                style.base["minWidth"] = f"{h.min}px"
            if h.max is not None:
                style.base["maxWidth"] = f"{h.max}px"

        # --- Vertical (height) ---
        if v:
            if v.mode == SIZING_FIXED:
                style.base["height"] = f"{plan.box.height}px"
            elif v.mode == SIZING_FILL:
                if display == "flex":
                    style.base["flex"] = style.base.get("flex", "1 1 0%")
                    style.base["alignSelf"] = "stretch"
                else:
                    style.base["height"] = "100%"
            elif v.mode == SIZING_HUG:
                style.base["height"] = "fit-content"
            elif v.mode == SIZING_PERCENT and v.value is not None:
                style.base["height"] = f"{v.value * 100:.2f}%"
            # Apply min/max clamps
            if v.min is not None:
                style.base["minHeight"] = f"{v.min}px"
            if v.max is not None:
                style.base["maxHeight"] = f"{v.max}px"

    # --------------------------------------------------------- display mapping

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
