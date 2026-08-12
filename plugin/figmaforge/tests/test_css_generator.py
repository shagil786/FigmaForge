#!/usr/bin/env python3
"""
Tests for CSSGenerator (Part 6).

Verify that all sizing modes (fixed, fill, hug, percent) and display types
(flex, grid, absolute) emit correct CSS properties.
"""
import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.css_generator import CSSGenerator
from core.layout_types import (
    AlignmentSpec,
    Anchoring,
    AxisSizing,
    Box,
    LayoutNodePlan,
    OverflowSpec,
    SizingSpec,
    SpacingSpec,
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    DISPLAY_GRID,
    DISPLAY_NONE,
    SIZING_FIXED,
    SIZING_FILL,
    SIZING_HUG,
    SIZING_PERCENT,
)


def _plan(**overrides) -> LayoutNodePlan:
    defaults = dict(
        node_id="n1",
        name="container",
        kind="frame",
        display=DISPLAY_FLEX,
        direction="row",
        box=Box(x=0, y=0, width=200, height=100),
        sizing=SizingSpec(
            horizontal=AxisSizing(mode=SIZING_FIXED),
            vertical=AxisSizing(mode=SIZING_FIXED),
        ),
    )
    defaults.update(overrides)
    return LayoutNodePlan(**defaults)


class TestCSSFixedSizing(unittest.TestCase):
    def test_fixed_emits_px_dimensions(self):
        gen = CSSGenerator()
        style = gen.generate_style(_plan())
        self.assertEqual(style.base["width"], "200px")
        self.assertEqual(style.base["height"], "100px")

    def test_fixed_display_flex(self):
        gen = CSSGenerator()
        style = gen.generate_style(_plan(display=DISPLAY_FLEX))
        self.assertEqual(style.base["display"], "flex")


class TestCSSFillSizing(unittest.TestCase):
    def test_fill_in_flex_emits_flex_property(self):
        gen = CSSGenerator()
        plan = _plan(
            sizing=SizingSpec(
                horizontal=AxisSizing(mode=SIZING_FILL),
                vertical=AxisSizing(mode=SIZING_FILL),
            ),
        )
        style = gen.generate_style(plan)
        self.assertIn("flex", style.base)
        self.assertEqual(style.base["flex"], "1 1 0%")

    def test_fill_outside_flex_emits_100_percent(self):
        gen = CSSGenerator()
        plan = _plan(
            display=DISPLAY_NONE,
            sizing=SizingSpec(
                horizontal=AxisSizing(mode=SIZING_FILL),
                vertical=AxisSizing(mode=SIZING_FILL),
            ),
        )
        style = gen.generate_style(plan)
        self.assertEqual(style.base["width"], "100%")
        self.assertEqual(style.base["height"], "100%")


class TestCSSHugSizing(unittest.TestCase):
    def test_hug_emits_fit_content(self):
        gen = CSSGenerator()
        plan = _plan(
            sizing=SizingSpec(
                horizontal=AxisSizing(mode=SIZING_HUG),
                vertical=AxisSizing(mode=SIZING_HUG),
            ),
        )
        style = gen.generate_style(plan)
        self.assertEqual(style.base["width"], "fit-content")
        self.assertEqual(style.base["height"], "fit-content")


class TestCSSPercentSizing(unittest.TestCase):
    def test_percent_emits_percentage(self):
        gen = CSSGenerator()
        plan = _plan(
            sizing=SizingSpec(
                horizontal=AxisSizing(mode=SIZING_PERCENT, value=0.5),
                vertical=AxisSizing(mode=SIZING_PERCENT, value=0.75),
            ),
        )
        style = gen.generate_style(plan)
        self.assertEqual(style.base["width"], "50.00%")
        self.assertEqual(style.base["height"], "75.00%")


class TestCSSMinMaxClamps(unittest.TestCase):
    def test_min_max_emit_css_properties(self):
        gen = CSSGenerator()
        plan = _plan(
            sizing=SizingSpec(
                horizontal=AxisSizing(mode=SIZING_FIXED, min=50.0, max=300.0),
                vertical=AxisSizing(mode=SIZING_FIXED, min=20.0, max=500.0),
            ),
        )
        style = gen.generate_style(plan)
        self.assertEqual(style.base["minWidth"], "50.0px")
        self.assertEqual(style.base["maxWidth"], "300.0px")
        self.assertEqual(style.base["minHeight"], "20.0px")
        self.assertEqual(style.base["maxHeight"], "500.0px")


class TestCSSGridLayout(unittest.TestCase):
    def test_grid_emits_display_and_auto_flow(self):
        gen = CSSGenerator()
        plan = _plan(display=DISPLAY_GRID, direction="row")
        style = gen.generate_style(plan)
        self.assertEqual(style.base["display"], "grid")
        self.assertEqual(style.base["gridAutoFlow"], "row")

    def test_grid_column_direction(self):
        gen = CSSGenerator()
        plan = _plan(display=DISPLAY_GRID, direction="column")
        style = gen.generate_style(plan)
        self.assertEqual(style.base["gridAutoFlow"], "column")

    def test_grid_alignment(self):
        gen = CSSGenerator()
        plan = _plan(
            display=DISPLAY_GRID,
            alignment=AlignmentSpec(justify="CENTER", align="STRETCH"),
        )
        style = gen.generate_style(plan)
        self.assertEqual(style.base["justifyItems"], "center")
        self.assertEqual(style.base["alignItems"], "stretch")


class TestCSSAbsolutePositioning(unittest.TestCase):
    def test_absolute_emits_position_and_anchors(self):
        gen = CSSGenerator()
        plan = _plan(
            display=DISPLAY_ABSOLUTE,
            anchors=Anchoring(left=10.0, top=20.0),
        )
        style = gen.generate_style(plan)
        self.assertEqual(style.base["position"], "absolute")
        self.assertEqual(style.base["left"], "10.0px")
        self.assertEqual(style.base["top"], "20.0px")


class TestCSSSpacing(unittest.TestCase):
    def test_padding_and_gap(self):
        gen = CSSGenerator()
        from layout_types import EdgeOffsets
        plan = _plan(
            spacing=SpacingSpec(
                padding=EdgeOffsets(top=8.0, right=16.0, bottom=8.0, left=16.0),
                gap=12.0,
            ),
        )
        style = gen.generate_style(plan)
        self.assertEqual(style.base["paddingTop"], "8.0px")
        self.assertEqual(style.base["paddingRight"], "16.0px")
        self.assertEqual(style.base["gap"], "12.0px")


class TestCSSNoSizing(unittest.TestCase):
    def test_no_sizing_falls_back_to_box(self):
        gen = CSSGenerator()
        plan = _plan(sizing=None)
        style = gen.generate_style(plan)
        self.assertEqual(style.base["width"], "200px")
        self.assertEqual(style.base["height"], "100px")


if __name__ == "__main__":
    unittest.main()
