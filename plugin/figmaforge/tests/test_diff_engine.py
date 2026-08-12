#!/usr/bin/env python3
"""
Tests for DiffEngine (Part 7).

Verify that DiffEngine correctly identifies geometry and style mismatches
between a predicted LayoutPlan and dummy render metadata.
"""
import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.diff_engine import DiffEngine, DiffReport


class _MockBox:
    def __init__(self, x, y, w, h):
        self.x = x
        self.y = y
        self.width = w
        self.height = h


class _MockText:
    def __init__(self, font_size=None):
        self.font_size = font_size


class _MockNode:
    def __init__(self, nid, x=0, y=0, w=100, h=50, font_size=None):
        self.node_id = nid
        self.box = _MockBox(x, y, w, h)
        self.text = _MockText(font_size) if font_size else None

    def nodes(self):
        return [self]


class _MockPlan:
    """A plan with multiple nodes for testing."""
    def __init__(self, nodes):
        self._nodes = nodes

    def nodes(self):
        return self._nodes


class TestDiffEngineGeometryMismatch(unittest.TestCase):
    def test_width_mismatch_detected(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 105, "height": 50}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertEqual(len(report.mismatches), 1)
        self.assertEqual(report.mismatches[0]["type"], "geometry_mismatch")

    def test_exact_match_no_mismatches(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertEqual(len(report.mismatches), 0)
        self.assertEqual(report.similarity_score, 1.0)

    def test_within_tolerance_no_mismatch(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0.5, "y": 0.5, "width": 100.5, "height": 50.5}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertEqual(len(report.mismatches), 0)

    def test_missing_in_render(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertEqual(report.mismatches[0]["type"], "missing_in_render")


class TestDiffEngineStyleMismatch(unittest.TestCase):
    def test_typography_mismatch_detected(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50, font_size=16)])
        render_meta = {"n1": {"styles": {"fontSize": 20}}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        style_mismatches = [m for m in report.mismatches if m["type"] == "typography_mismatch"]
        self.assertEqual(len(style_mismatches), 1)

    def test_typography_within_tolerance(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50, font_size=16)])
        render_meta = {"n1": {"styles": {"fontSize": 16.5}}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        style_mismatches = [m for m in report.mismatches if m["type"] == "typography_mismatch"]
        self.assertEqual(len(style_mismatches), 0)


class TestDiffEngineScoreClamping(unittest.TestCase):
    def test_score_clamped_to_zero(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 999, "y": 999, "width": 999, "height": 999, "styles": {"fontSize": 999}}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertGreaterEqual(report.similarity_score, 0.0)

    def test_categories_are_per_category(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 999, "y": 0, "width": 100, "height": 50}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertLess(report.categories["geometry"], 1.0)
        self.assertEqual(report.categories["style"], 1.0)

    def test_empty_plan_score_is_one(self):
        plan = _MockPlan([])
        render_meta = {}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertEqual(report.similarity_score, 1.0)


class TestDiffReportSerialization(unittest.TestCase):
    def test_to_dict(self):
        report = DiffReport(
            similarity_score=0.8,
            categories={"geometry": 0.9, "style": 0.7, "pixels": 1.0},
            mismatches=[{"node_id": "n1", "type": "geometry_mismatch"}],
        )
        d = report.to_dict()
        self.assertEqual(d["similarity_score"], 0.8)
        self.assertEqual(len(d["mismatches"]), 1)
        self.assertEqual(d["categories"]["geometry"], 0.9)


if __name__ == "__main__":
    unittest.main()
