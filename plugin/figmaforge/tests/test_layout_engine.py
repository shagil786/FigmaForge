#!/usr/bin/env python3
"""
End-to-end tests for the Part-5 layout engine (requirements 1-12).

Each test group maps to one of the twelve requirements. Fixtures are raw Figma
responses under ``fixtures/figma/layout_*.json`` that flow through
FigmaFile -> IRBuilder -> LayoutAnalyzer, mirroring the Part-4 test pattern.
"""

import json
import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.figma_fixtures import FixtureLoader
from core.figma_types import FigmaFile
from core.ir_builder import IRBuilder
from core.layout_analyzer import LayoutAnalyzer
from core.layout_types import plan_to_json
from core.library_types import LibraryLoader
from core.ir_validator import validate_ir, load_schema

LAYOUT_SCHEMA = plugin_root / "schemas" / "layout-plan.schema.json"
FIXTURE_DIR = plugin_root / "fixtures" / "figma"


def analyze(fixture: str, file_key: str, viewport=None):
    loader = FixtureLoader(FIXTURE_DIR)
    doc = IRBuilder().build(FigmaFile.from_dict(file_key, loader.load(fixture)))
    return LayoutAnalyzer().analyze(doc, library=LibraryLoader().load(), viewport=viewport)


def node(plan, node_id):
    return plan.node(node_id)


class TestAutoLayoutToFlex(unittest.TestCase):
    def test_auto_layout_infers_flex(self):
        plan = analyze("layout_desktop", "lay1440")
        self.assertEqual(node(plan, "d:1").display, "flex")
        self.assertEqual(node(plan, "d:1").direction, "column")
        self.assertEqual(node(plan, "d:2").display, "flex")
        self.assertEqual(node(plan, "d:2").direction, "row")

    def test_flow_children_prefer_semantic_layout(self):
        plan = analyze("layout_desktop", "lay1440")
        # Header/brand/docs are flow children, not absolutely positioned.
        self.assertEqual(node(plan, "d:3").display, "none")  # leaf, laid out by parent
        self.assertNotEqual(node(plan, "d:3").anchors, None, "flow child has no anchors")


class TestSizingModes(unittest.TestCase):
    def test_fixed(self):
        plan = analyze("layout_desktop", "lay1440")
        self.assertEqual(node(plan, "d:1").sizing.horizontal.mode, "fixed")
        self.assertEqual(node(plan, "d:1").box.width, 1440)

    def test_fill(self):
        plan = analyze("layout_desktop", "lay1440")
        header = node(plan, "d:2")
        self.assertEqual(header.sizing.horizontal.mode, "fill")
        self.assertEqual(header.box.width, 1392)

    def test_hug(self):
        plan = analyze("layout_desktop", "lay1440")
        brand = node(plan, "d:3")
        self.assertEqual(brand.sizing.horizontal.mode, "hug")
        self.assertAlmostEqual(brand.box.width, 44.0, places=3)

    def test_percent(self):
        plan = analyze("layout_tablet", "lay1024")
        card = node(plan, "t:6")
        self.assertEqual(card.sizing.horizontal.mode, "percent")
        self.assertAlmostEqual(card.box.width, 480.0, places=3)


class TestMinMax(unittest.TestCase):
    def test_contradiction_detected(self):
        plan = analyze("layout_content_overflow", "lay200")
        bad = node(plan, "o:4")
        self.assertTrue(
            any(d.code == "contradiction" for d in bad.diagnostics),
            "min>max must be reported, never resolved",
        )
        self.assertEqual(bad.confidence, 0.0)

    def test_plan_flags_contradictions_in_counts(self):
        plan = analyze("layout_content_overflow", "lay200")
        self.assertGreaterEqual(plan.counts["contradictions"], 1)


class TestSpacing(unittest.TestCase):
    def test_padding_and_gap(self):
        plan = analyze("layout_desktop", "lay1440")
        spacing = node(plan, "d:1").spacing
        self.assertEqual(spacing.padding.left, 24)
        self.assertEqual(spacing.gap, 16)

    def test_margin_only_from_evidence(self):
        plan = analyze("layout_desktop", "lay1440")
        # Flow children have no margin (Figma has no native margin).
        self.assertIsNone(node(plan, "d:3").spacing.margin)
        # Absolutely-positioned node infers a margin from its offsets.
        nested = analyze("layout_nested", "lay300")
        badge = node(nested, "n:5")
        self.assertEqual(badge.spacing.margin_source, "absolute_offset")


class TestAlignment(unittest.TestCase):
    def test_justify_align_inferred(self):
        plan = analyze("layout_desktop", "lay1440")
        self.assertEqual(node(plan, "d:1").alignment.align, "MIN")
        footer = node(plan, "d:5")
        self.assertEqual(footer.alignment.align, "CENTER")
        # CENTER cross axis centers the child within the footer's content box.
        self.assertAlmostEqual(node(plan, "d:6").box.y, 88.0, places=3)


class TestAbsolutePositioning(unittest.TestCase):
    def test_absolute_only_where_required(self):
        plan = analyze("layout_nested", "lay300")
        self.assertEqual(node(plan, "n:5").display, "absolute")
        # Normal children of the flex frame are NOT absolute.
        self.assertNotEqual(node(plan, "n:2").display, "absolute")


class TestAnchoring(unittest.TestCase):
    def test_anchor_inferred_from_constraints(self):
        plan = analyze("layout_nested", "lay300")
        badge = node(plan, "n:5")
        self.assertEqual(badge.anchors.horizontal, "max")
        self.assertEqual(badge.anchors.vertical, "min")


class TestResponsiveBreakpoints(unittest.TestCase):
    def test_ladder_from_library(self):
        plan = analyze("layout_tablet", "lay1024")
        sizes = {bp["breakpoint"]: bp["width"] for bp in plan.breakpoints.breakpoints}
        self.assertEqual(sizes.get("lg"), 1440)
        self.assertEqual(sizes.get("sm"), 640)

    def test_changes_only_with_evidence(self):
        plan = analyze("layout_tablet", "lay1024")
        # A growing card changes width across viewports.
        self.assertTrue(
            any(c.node_id == "t:6" and c.property == "width" for c in plan.breakpoints.changes),
            "percent-sized card must show a width change",
        )
        # A fixed-size brand has no change and is listed explicitly.
        self.assertIn("t:3", plan.breakpoints.no_change)
        for change in plan.breakpoints.changes:
            self.assertTrue(change.evidence, "every breakpoint change needs evidence")

    def test_never_invents_breakpoints(self):
        plan = analyze("layout_desktop", "lay1440")
        # Desktop has no responsive sizing; no changes are invented.
        for change in plan.breakpoints.changes:
            self.assertNotEqual(change.node_id, "d:3")


class TestTextWrappingAndContentSizing(unittest.TestCase):
    def test_hug_text_measured(self):
        plan = analyze("layout_mobile", "lay375")
        title = node(plan, "m:6")
        self.assertEqual(title.sizing.horizontal.mode, "hug")
        self.assertAlmostEqual(title.box.width, 84.7, places=3)
        self.assertTrue(title.text.approximate)

    def test_wrapping_and_height(self):
        plan = analyze("layout_mobile", "lay375")
        body = node(plan, "m:7")
        self.assertTrue(body.text.wrapped)
        self.assertGreaterEqual(len(body.text.lines), 2)
        self.assertAlmostEqual(body.box.height, 42.0, places=3)


class TestOverflowClipScroll(unittest.TestCase):
    def test_clip_detected(self):
        plan = analyze("layout_content_overflow", "lay200")
        frame = node(plan, "o:1")
        self.assertTrue(frame.overflow.clipped_content)
        self.assertEqual(frame.overflow.x, "clip")

    def test_scroll_never_inferred(self):
        plan = analyze("layout_content_overflow", "lay200")
        for n in plan.nodes():
            self.assertNotEqual(n.overflow.x, "scroll")
            self.assertNotEqual(n.overflow.y, "scroll")


class TestNestedPropagation(unittest.TestCase):
    def test_nested_layout_propagates(self):
        plan = analyze("layout_nested", "lay300")
        group = node(plan, "n:2")
        self.assertEqual(group.sizing.vertical.mode, "hug")
        self.assertAlmostEqual(group.box.height, 64.0, places=3)
        self.assertAlmostEqual(node(plan, "n:4").box.x, 24.0, places=3)
        self.assertAlmostEqual(node(plan, "n:4").box.y, 48.0, places=3)


class TestConfidence(unittest.TestCase):
    def test_evidence_based_scores(self):
        plan = analyze("layout_content_overflow", "lay200")
        # Contradictory node scores 0.
        self.assertEqual(node(plan, "o:4").confidence, 0.0)
        # Fixed, well-constrained nodes score high.
        desktop = analyze("layout_desktop", "lay1440")
        self.assertGreater(node(desktop, "d:1").confidence, 0.75)
        # Heuristic text is flagged with reduced confidence.
        self.assertLess(node(desktop, "d:3").confidence, 1.0)
        self.assertIn("text_width_heuristic", node(desktop, "d:3").assumptions)

    def test_aggregate_confidence(self):
        plan = analyze("layout_desktop", "lay1440")
        self.assertGreaterEqual(plan.confidence["high"], 1)
        self.assertGreaterEqual(plan.confidence["low"], 0)


class TestPredictedVsFigmaBounds(unittest.TestCase):
    def test_bounds_reproduced_at_native_width(self):
        for fixture, file_key in (
            ("layout_desktop", "lay1440"),
            ("layout_tablet", "lay1024"),
            ("layout_mobile", "lay375"),
            ("layout_nested", "lay300"),
        ):
            plan = analyze(fixture, file_key)
            for n in plan.nodes():
                if n.box is not None and n.figma_box is not None:
                    self.assertLessEqual(
                        n.bounds_delta, 1e-3,
                        f"{fixture}:{n.node_id} predicted {n.box} vs Figma {n.figma_box}",
                    )


class TestSchemaAndSerialization(unittest.TestCase):
    def test_schema_validation_passes(self):
        plan = analyze("layout_desktop", "lay1440")
        schema = load_schema(LAYOUT_SCHEMA)
        self.assertEqual(validate_ir(plan.to_dict(), schema), [])

    def test_deterministic_serialization(self):
        a = plan_to_json(analyze("layout_desktop", "lay1440"))
        b = plan_to_json(analyze("layout_desktop", "lay1440"))
        self.assertEqual(a, b)
        json.loads(a)

    def test_all_fixtures_analyze_without_error(self):
        for fixture in ("layout_desktop", "layout_tablet", "layout_mobile",
                        "layout_nested", "layout_content_overflow"):
            plan = analyze(fixture, "key" + fixture[-2:])
            self.assertGreaterEqual(plan.counts["nodes"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
