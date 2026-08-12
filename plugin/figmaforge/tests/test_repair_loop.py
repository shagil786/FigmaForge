"""
Repair Loop Tests (Part 8).

Tests the full visual repair pipeline:
  DiffReport → RepairClassifier → PatchPlanner → PatchExecutor → RepairLoop

Uses intentional defects in fixture data to verify that the repair loop
detects, classifies, plans, executes, and iterates correctly.

Run:  python3 -m unittest tests.test_repair_loop -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

# Ensure the plugin root is on sys.path so ``from core.xxx import ...`` works.
plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.diff_engine import DiffEngine, DiffReport
from core.generator_types import GeneratorManifest, VStyle
from core.ir_types import (
    IRColor,
    IRDimensions,
    IRDocument,
    IRFill,
    IRLayout,
    IRNode,
    IRSource,
    IRStyle,
    IRTokens,
    IRTokenRef,
    IRTypography,
    KIND_FRAME,
    KIND_TEXT,
)
from core.layout_types import (
    Box,
    DISPLAY_FLEX,
    LayoutNodePlan,
    LayoutPlan,
    SizingSpec,
    AxisSizing,
    SIZING_FIXED,
    SpacingSpec,
    EdgeOffsets,
    TextModel,
)
from core.library_types import ProjectLibrary, ProjectToken, ProjectComponent
from core.patch_executor import PatchExecutor
from core.patch_planner import PatchPlanner, TARGET_TOKEN, TARGET_LAYOUT, TARGET_STYLE
from core.repair_classifier import (
    CATEGORY_COLOR,
    CATEGORY_GEOMETRY,
    CATEGORY_MISSING_ELEMENT,
    CATEGORY_SPACING,
    CATEGORY_TYPOGRAPHY,
    ClassificationReport,
    RepairCandidate,
    RepairClassifier,
)
from core.repair_history import IterationRecord, RepairHistory
from core.repair_loop import (
    RepairConfig,
    RepairLoop,
    RepairResult,
    STOP_MAX_ITERATIONS,
    STOP_NO_REPAIR,
    STOP_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_ir_node(
    node_id: str,
    name: str,
    kind: str = KIND_FRAME,
    width: float = 100,
    height: float = 50,
    font_size: float | None = None,
    token_key: str | None = None,
) -> IRNode:
    """Build a minimal IRNode for testing."""
    node = IRNode(
        id=node_id,
        name=name,
        kind=kind,
        node_type="FRAME" if kind == KIND_FRAME else "TEXT",
        source=IRSource(file_key="test-file", node_id=node_id),
    )
    node.dimensions = IRDimensions(width=width, height=height)
    if font_size is not None:
        node.typography = IRTypography(font_size=font_size)
    if token_key is not None:
        node.tokens = IRTokens(
            bound_variables={"fontSize": token_key},
            refs=[IRTokenRef(property_name="fontSize", token_key=token_key)],
        )
    return node


def _make_layout_plan(
    nodes: list[tuple[str, str, float, float, float, float]],
) -> LayoutPlan:
    """Build a LayoutPlan from a list of (node_id, name, x, y, w, h) tuples."""
    screen = LayoutNodePlan(node_id="screen", name="Screen", kind="frame")
    for node_id, name, x, y, w, h in nodes:
        child = LayoutNodePlan(
            node_id=node_id,
            name=name,
            kind="frame",
            display=DISPLAY_FLEX,
            box=Box(x=x, y=y, width=w, height=h),
        )
        screen.children.append(child)
    return LayoutPlan(
        file_key="test-file",
        viewport=1440.0,
        screens=[screen],
    )


def _make_render_meta_matching(plan: LayoutPlan) -> dict:
    """Create render_meta that perfectly matches the plan (no differences)."""
    meta = {}
    for node in plan.nodes():
        if node.box:
            meta[node.node_id] = {
                "x": node.box.x,
                "y": node.box.y,
                "width": node.box.width,
                "height": node.box.height,
            }
        else:
            # Nodes without a box (e.g. screen) still need a render entry
            # so the diff engine doesn't flag them as missing_in_render.
            meta[node.node_id] = {"x": 0, "y": 0, "width": 0, "height": 0}
    return meta


def _make_render_meta_with_defects(
    plan: LayoutPlan,
    defects: dict[str, dict],
) -> dict:
    """Create render_meta with intentional defects for specific nodes.

    defects: {node_id: {field: value}} — overrides the expected values.
    """
    meta = _make_render_meta_matching(plan)
    for node_id, overrides in defects.items():
        if node_id in meta:
            meta[node_id].update(overrides)
    return meta


# ---------------------------------------------------------------------------
# Test: RepairClassifier
# ---------------------------------------------------------------------------


class TestRepairClassifier(unittest.TestCase):
    """Tests for the repair candidate classifier."""

    def test_classify_geometry_mismatch(self):
        """Geometry mismatches are classified as geometry candidates."""
        plan = _make_layout_plan([
            ("n1", "Header", 0, 0, 200, 80),
        ])
        report = DiffReport(
            similarity_score=0.5,
            categories={"geometry": 0.5, "style": 1.0, "pixels": 1.0},
            mismatches=[{
                "node_id": "n1",
                "type": "geometry_mismatch",
                "expected": {"x": 0, "y": 0, "w": 200, "h": 80},
                "actual": {"x": 5, "y": 5, "width": 190, "height": 70},
            }],
        )
        classifier = RepairClassifier(plan=plan)
        result = classifier.classify(report)

        self.assertEqual(result.total_mismatches, 1)
        self.assertEqual(result.classified_count, 1)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].category, CATEGORY_GEOMETRY)
        self.assertEqual(result.candidates[0].node_id, "n1")

    def test_classify_missing_element(self):
        """Missing-in-render mismatches are classified as missing_element."""
        plan = _make_layout_plan([("n1", "Header", 0, 0, 200, 80)])
        report = DiffReport(
            similarity_score=0.5,
            categories={"geometry": 0.5, "style": 1.0, "pixels": 1.0},
            mismatches=[{"node_id": "n1", "type": "missing_in_render"}],
        )
        classifier = RepairClassifier(plan=plan)
        result = classifier.classify(report)

        self.assertEqual(result.candidates[0].category, CATEGORY_MISSING_ELEMENT)

    def test_classify_typography_mismatch(self):
        """Typography mismatches are classified correctly."""
        plan = _make_layout_plan([("n1", "Title", 0, 0, 200, 30)])
        report = DiffReport(
            similarity_score=0.8,
            categories={"geometry": 1.0, "style": 0.8, "pixels": 1.0},
            mismatches=[{"node_id": "n1", "type": "typography_mismatch"}],
        )
        classifier = RepairClassifier(plan=plan)
        result = classifier.classify(report)

        self.assertEqual(result.candidates[0].category, CATEGORY_TYPOGRAPHY)

    def test_small_geometry_delta_classified_as_spacing(self):
        """Small uniform geometry deltas are refined to spacing."""
        plan = _make_layout_plan([("n1", "Card", 0, 0, 200, 100)])
        report = DiffReport(
            similarity_score=0.7,
            categories={"geometry": 0.7, "style": 1.0, "pixels": 1.0},
            mismatches=[{
                "node_id": "n1",
                "type": "geometry_mismatch",
                "expected": {"x": 0, "y": 0, "w": 200, "h": 100},
                "actual": {"x": 0, "y": 0, "width": 197, "height": 97},
            }],
        )
        classifier = RepairClassifier(plan=plan)
        result = classifier.classify(report)

        # 3px delta is within the 4px threshold → spacing
        self.assertEqual(result.candidates[0].category, CATEGORY_SPACING)

    def test_unclassifiable_mismatch_tracked(self):
        """Unknown mismatch types go to unclassifiable, not candidates."""
        report = DiffReport(
            similarity_score=0.5,
            categories={"geometry": 0.5, "style": 1.0, "pixels": 1.0},
            mismatches=[{"node_id": "n1", "type": "unknown_type_xyz"}],
        )
        classifier = RepairClassifier()
        result = classifier.classify(report)

        self.assertEqual(len(result.candidates), 0)
        self.assertEqual(len(result.unclassifiable), 1)

    def test_shared_token_detection(self):
        """Candidates sharing a token are marked as shared_token."""
        doc = IRDocument(file_key="test")
        n1 = _make_ir_node("n1", "Title", font_size=16, token_key="font-body")
        n2 = _make_ir_node("n2", "Subtitle", font_size=14, token_key="font-body")
        doc.root = IRNode(
            id="root", name="Root", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="test", node_id="root"),
            children=[n1, n2],
        )

        report = DiffReport(
            similarity_score=0.5,
            categories={"geometry": 1.0, "style": 0.5, "pixels": 1.0},
            mismatches=[
                {"node_id": "n1", "type": "typography_mismatch"},
                {"node_id": "n2", "type": "typography_mismatch"},
            ],
        )
        classifier = RepairClassifier(plan=None, document=doc)
        result = classifier.classify(report)

        shared = [c for c in result.candidates if c.shared_token]
        self.assertEqual(len(shared), 2)

    def test_candidate_ids_are_sequential(self):
        """Candidate IDs are sequential: RC-0001, RC-0002, ..."""
        report = DiffReport(
            similarity_score=0.3,
            categories={"geometry": 0.3, "style": 0.3, "pixels": 1.0},
            mismatches=[
                {"node_id": "n1", "type": "geometry_mismatch",
                 "expected": {"w": 100, "h": 50}, "actual": {"width": 90, "height": 40}},
                {"node_id": "n2", "type": "missing_in_render"},
            ],
        )
        classifier = RepairClassifier()
        result = classifier.classify(report)

        self.assertEqual(result.candidates[0].candidate_id, "RC-0001")
        self.assertEqual(result.candidates[1].candidate_id, "RC-0002")

    def test_serialization_round_trip(self):
        """ClassificationReport serializes to dict without errors."""
        report = DiffReport(
            similarity_score=0.5,
            categories={"geometry": 0.5, "style": 1.0, "pixels": 1.0},
            mismatches=[{"node_id": "n1", "type": "geometry_mismatch",
                         "expected": {"w": 100}, "actual": {"width": 90}}],
        )
        classifier = RepairClassifier()
        result = classifier.classify(report)
        d = result.to_dict()
        self.assertIn("candidates", d)
        self.assertIn("categories", d)
        self.assertEqual(d["total_mismatches"], 1)


# ---------------------------------------------------------------------------
# Test: PatchPlanner
# ---------------------------------------------------------------------------


class TestPatchPlanner(unittest.TestCase):
    """Tests for the patch planner."""

    def _make_classification(self, candidates: list[RepairCandidate]) -> ClassificationReport:
        report = ClassificationReport(
            total_mismatches=len(candidates),
            classified_count=len(candidates),
        )
        report.candidates = candidates
        return report

    def test_empty_classification_produces_empty_plan(self):
        """No candidates → no patches."""
        classification = self._make_classification([])
        planner = PatchPlanner()
        plan = planner.plan(classification, iteration=0)

        self.assertTrue(plan.is_empty)
        self.assertEqual(plan.patch_count, 0)

    def test_missing_element_before_geometry(self):
        """Missing elements are planned before geometry fixes."""
        c1 = RepairCandidate(
            candidate_id="RC-0001", category=CATEGORY_GEOMETRY,
            node_id="n1", description="geo",
        )
        c2 = RepairCandidate(
            candidate_id="RC-0002", category=CATEGORY_MISSING_ELEMENT,
            node_id="n2", description="missing",
        )
        classification = self._make_classification([c1, c2])
        planner = PatchPlanner()
        plan = planner.plan(classification)

        # Missing element should come first
        self.assertGreater(plan.patch_count, 0)
        first_patch = plan.patches[0]
        self.assertIn("RC-0002", first_patch.candidate_ids)

    def test_shared_token_produces_single_patch(self):
        """Multiple candidates sharing a token produce one shared patch."""
        c1 = RepairCandidate(
            candidate_id="RC-0001", category=CATEGORY_TYPOGRAPHY,
            node_id="n1", shared_token=True,
            source_mapping=__import__("core.repair_classifier", fromlist=["SourceMapping"]).SourceMapping(
                token_key="font-body", token_property="fontSize",
            ),
            expected={"fontSize": 16},
        )
        c2 = RepairCandidate(
            candidate_id="RC-0002", category=CATEGORY_TYPOGRAPHY,
            node_id="n2", shared_token=True,
            source_mapping=__import__("core.repair_classifier", fromlist=["SourceMapping"]).SourceMapping(
                token_key="font-body", token_property="fontSize",
            ),
            expected={"fontSize": 16},
        )
        classification = self._make_classification([c1, c2])
        planner = PatchPlanner()
        plan = planner.plan(classification)

        shared_patches = [p for p in plan.patches if p.is_shared]
        self.assertEqual(len(shared_patches), 1)
        self.assertIn("RC-0001", shared_patches[0].candidate_ids)
        self.assertIn("RC-0002", shared_patches[0].candidate_ids)

    def test_parent_before_child(self):
        """Parent geometry is planned before child geometry."""
        # Build a tree where "parent" is an actual ancestor of "child"
        child_node = LayoutNodePlan(
            node_id="child", name="Inner", kind="frame",
            display=DISPLAY_FLEX,
            box=Box(x=10, y=10, width=200, height=100),
        )
        parent_node = LayoutNodePlan(
            node_id="parent", name="Container", kind="frame",
            display=DISPLAY_FLEX,
            box=Box(x=0, y=0, width=400, height=300),
            children=[child_node],
        )
        screen = LayoutNodePlan(
            node_id="screen", name="Screen", kind="frame",
            children=[parent_node],
        )
        plan = LayoutPlan(file_key="test", viewport=1440.0, screens=[screen])

        c1 = RepairCandidate(
            candidate_id="RC-0001", category=CATEGORY_GEOMETRY,
            node_id="child", description="child geo",
            expected={"w": 200, "h": 100},
        )
        c2 = RepairCandidate(
            candidate_id="RC-0002", category=CATEGORY_GEOMETRY,
            node_id="parent", description="parent geo",
            expected={"w": 400, "h": 300},
        )
        classification = self._make_classification([c1, c2])
        planner = PatchPlanner(plan=plan)
        result = planner.plan(classification)

        # Parent (depth 1) should come before child (depth 2)
        self.assertGreater(result.patch_count, 0)
        parent_idx = next(
            i for i, p in enumerate(result.patches) if "RC-0002" in p.candidate_ids
        )
        child_idx = next(
            i for i, p in enumerate(result.patches) if "RC-0001" in p.candidate_ids
        )
        self.assertLess(parent_idx, child_idx)

    def test_plan_serialization(self):
        """PatchPlan serializes to dict without errors."""
        c1 = RepairCandidate(
            candidate_id="RC-0001", category=CATEGORY_GEOMETRY,
            node_id="n1", expected={"w": 100, "h": 50},
        )
        classification = self._make_classification([c1])
        planner = PatchPlanner()
        plan = planner.plan(classification, iteration=3)
        d = plan.to_dict()
        self.assertEqual(d["iteration"], 3)
        self.assertIn("patches", d)


# ---------------------------------------------------------------------------
# Test: PatchExecutor
# ---------------------------------------------------------------------------


class TestPatchExecutor(unittest.TestCase):
    """Tests for the patch executor."""

    def test_token_patch_updates_library(self):
        """Token patches update the project library value."""
        library = ProjectLibrary(tokens=[
            ProjectToken(name="color-primary", type="color", value="#ff0000"),
        ])
        from core.patch_planner import Patch
        patch = Patch(
            patch_id="P-0001", target_type=TARGET_TOKEN,
            target_key="color-primary", property_name="value",
            old_value="#ff0000", new_value="#0000ff",
        )
        from core.patch_planner import PatchPlan
        plan = PatchPlan(patches=[patch])

        executor = PatchExecutor(library=library)
        result = executor.execute(plan)

        self.assertEqual(result.success_count, 1)
        self.assertEqual(library.tokens[0].value, "#0000ff")

    def test_rollback_restores_token(self):
        """Rollback restores the original token value."""
        library = ProjectLibrary(tokens=[
            ProjectToken(name="color-primary", type="color", value="#ff0000"),
        ])
        from core.patch_planner import Patch, PatchPlan
        patch = Patch(
            patch_id="P-0001", target_type=TARGET_TOKEN,
            target_key="color-primary", property_name="value",
            old_value="#ff0000", new_value="#0000ff",
        )
        plan = PatchPlan(patches=[patch])

        executor = PatchExecutor(library=library)
        executor.execute(plan)
        self.assertEqual(library.tokens[0].value, "#0000ff")

        executor.rollback()
        self.assertEqual(library.tokens[0].value, "#ff0000")

    def test_style_patch_updates_vstyle(self):
        """Style patches update the VStyle dictionary."""
        style = VStyle(base={"width": "100px"})
        styles = {"n1": style}

        from core.patch_planner import Patch, PatchPlan
        patch = Patch(
            patch_id="P-0001", target_type=TARGET_STYLE,
            target_key="n1", property_name="width",
            old_value="100px", new_value="200px",
        )
        plan = PatchPlan(patches=[patch])

        executor = PatchExecutor(styles=styles)
        result = executor.execute(plan)

        self.assertEqual(result.success_count, 1)
        self.assertEqual(style.base["width"], "200px")

    def test_missing_target_rejected(self):
        """Patches targeting non-existent artifacts are rejected."""
        from core.patch_planner import Patch, PatchPlan
        patch = Patch(
            patch_id="P-0001", target_type=TARGET_TOKEN,
            target_key="nonexistent-token", property_name="value",
            new_value="42",
        )
        plan = PatchPlan(patches=[patch])

        executor = PatchExecutor(library=ProjectLibrary())
        result = executor.execute(plan)

        self.assertEqual(result.success_count, 0)
        self.assertEqual(result.failure_count, 1)


# ---------------------------------------------------------------------------
# Test: RepairHistory
# ---------------------------------------------------------------------------


class TestRepairHistory(unittest.TestCase):
    """Tests for the repair history manifest."""

    def test_record_iteration(self):
        """Iterations are recorded in order."""
        history = RepairHistory(run_id="test-run")
        history.record_iteration(IterationRecord(
            iteration=0, similarity_before=0.0, similarity_after=0.5,
        ))
        history.record_iteration(IterationRecord(
            iteration=1, similarity_before=0.5, similarity_after=0.8,
        ))
        self.assertEqual(history.current_iteration, 2)

    def test_out_of_order_rejected(self):
        """Out-of-order iterations raise ValueError."""
        history = RepairHistory(run_id="test-run")
        history.record_iteration(IterationRecord(iteration=1))
        with self.assertRaises(ValueError):
            history.record_iteration(IterationRecord(iteration=0))

    def test_best_score(self):
        """best_score returns the highest similarity_after."""
        history = RepairHistory(run_id="test-run")
        history.record_iteration(IterationRecord(
            iteration=0, similarity_before=0.0, similarity_after=0.5,
        ))
        history.record_iteration(IterationRecord(
            iteration=1, similarity_before=0.5, similarity_after=0.9,
        ))
        history.record_iteration(IterationRecord(
            iteration=2, similarity_before=0.9, similarity_after=0.85,
        ))
        self.assertEqual(history.best_score, 0.9)

    def test_serialization_round_trip(self):
        """RepairHistory survives JSON serialization."""
        history = RepairHistory(run_id="test-run", status="completed")
        history.record_iteration(IterationRecord(
            iteration=0, similarity_before=0.0, similarity_after=0.7,
        ))
        d = history.to_dict()
        restored = RepairHistory.from_dict(d)
        self.assertEqual(restored.run_id, "test-run")
        self.assertEqual(restored.status, "completed")
        self.assertEqual(restored.current_iteration, 1)

    def test_save_and_load(self):
        """RepairHistory persists to and loads from disk."""
        import tempfile
        history = RepairHistory(run_id="persist-test")
        history.record_iteration(IterationRecord(
            iteration=0, similarity_before=0.0, similarity_after=0.6,
        ))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.json"
            history.save(path)
            loaded = RepairHistory.load(path)
            self.assertEqual(loaded.run_id, "persist-test")
            self.assertEqual(loaded.current_iteration, 1)


# ---------------------------------------------------------------------------
# Test: RepairLoop (integration)
# ---------------------------------------------------------------------------


class TestRepairLoop(unittest.TestCase):
    """Integration tests for the full repair loop."""

    def test_perfect_render_stops_immediately(self):
        """When render matches plan perfectly, loop stops at threshold."""
        plan = _make_layout_plan([
            ("n1", "Header", 0, 0, 200, 80),
            ("n2", "Content", 0, 80, 200, 400),
        ])
        doc = IRDocument(file_key="test")
        doc.root = IRNode(
            id="root", name="Root", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="test", node_id="root"),
        )

        config = RepairConfig(similarity_threshold=0.95, max_iterations=5)

        def perfect_render(p, s, d, i):
            return _make_render_meta_matching(p), ""

        loop = RepairLoop(config=config, render_fn=perfect_render)
        result = loop.run(plan, doc, run_id="perfect")

        self.assertTrue(result.success)
        self.assertEqual(result.stop_reason, STOP_THRESHOLD)
        self.assertEqual(result.iterations_run, 0)

    def test_max_iterations_stops_loop(self):
        """Loop stops after max_iterations when no progress is made."""
        plan = _make_layout_plan([
            ("n1", "Header", 0, 0, 200, 80),
        ])
        doc = IRDocument(file_key="test")
        doc.root = IRNode(
            id="root", name="Root", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="test", node_id="root"),
        )

        config = RepairConfig(
            max_iterations=3,
            min_progress=0.0,  # disable progress check — only max_iterations stops
            similarity_threshold=1.0,  # unreachable — ensures we don't stop early
        )

        # Render always returns a defect — no progress possible
        def bad_render(p, s, d, i):
            return {"n1": {"x": 0, "y": 0, "width": 150, "height": 60}}, ""

        loop = RepairLoop(config=config, render_fn=bad_render)
        result = loop.run(plan, doc, run_id="max-iter")

        self.assertFalse(result.success)
        self.assertEqual(result.stop_reason, STOP_MAX_ITERATIONS)
        self.assertEqual(result.iterations_run, 3)

    def test_history_records_all_iterations(self):
        """Every iteration is recorded in the history."""
        plan = _make_layout_plan([("n1", "Header", 0, 0, 200, 80)])
        doc = IRDocument(file_key="test")
        doc.root = IRNode(
            id="root", name="Root", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="test", node_id="root"),
        )

        config = RepairConfig(max_iterations=3)
        call_count = [0]

        def counting_render(p, s, d, i):
            call_count[0] += 1
            return {"n1": {"x": 0, "y": 0, "width": 150, "height": 60}}, ""

        loop = RepairLoop(config=config, render_fn=counting_render)
        result = loop.run(plan, doc, run_id="history-test")

        self.assertIsNotNone(result.history)
        self.assertGreater(result.history.current_iteration, 0)

    def test_approval_denied_stops_loop(self):
        """When approval is denied, the loop stops."""
        plan = _make_layout_plan([("n1", "Header", 0, 0, 200, 80)])
        doc = IRDocument(file_key="test")
        doc.root = IRNode(
            id="root", name="Root", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="test", node_id="root"),
        )

        config = RepairConfig(
            max_iterations=5,
            require_approval=True,
            similarity_threshold=0.5,
        )

        def bad_render(p, s, d, i):
            return {"n1": {"x": 0, "y": 0, "width": 150, "height": 60}}, ""

        def deny_approval(patch_plan, iteration):
            return False  # always deny

        loop = RepairLoop(
            config=config, render_fn=bad_render, approval_fn=deny_approval,
        )
        result = loop.run(plan, doc, run_id="approval-test")

        self.assertFalse(result.success)
        self.assertEqual(result.stop_reason, "approval_denied")

    def test_result_serialization(self):
        """RepairResult serializes to dict without errors."""
        plan = _make_layout_plan([("n1", "Header", 0, 0, 200, 80)])
        doc = IRDocument(file_key="test")
        doc.root = IRNode(
            id="root", name="Root", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="test", node_id="root"),
        )

        config = RepairConfig(similarity_threshold=0.99)
        loop = RepairLoop(config=config, render_fn=_default_render)
        result = loop.run(plan, doc, run_id="serialize-test")

        d = result.to_dict()
        self.assertIn("success", d)
        self.assertIn("final_score", d)
        self.assertIn("stop_reason", d)
        self.assertIn("history", d)


def _default_render(p, s, d, i):
    return _make_render_meta_matching(p), ""


# ---------------------------------------------------------------------------
# Test: Fixture-based end-to-end repair
# ---------------------------------------------------------------------------


class TestFixtureRepairLoop(unittest.TestCase):
    """End-to-end test with intentional defects in fixture data."""

    def test_geometry_defect_detected_and_classified(self):
        """An intentional geometry defect is detected and classified."""
        plan = _make_layout_plan([
            ("header", "Header", 0, 0, 1440, 80),
            ("hero", "Hero", 0, 80, 1440, 400),
        ])
        doc = IRDocument(file_key="test")
        doc.root = IRNode(
            id="root", name="Root", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="test", node_id="root"),
        )

        # Intentional defect: hero is 380px tall instead of 400px
        defects = {"hero": {"height": 380}}
        render_meta = _make_render_meta_with_defects(plan, defects)

        diff_engine = DiffEngine()
        diff_report = diff_engine.diff(plan, render_meta)

        classifier = RepairClassifier(plan=plan, document=doc)
        classification = classifier.classify(diff_report)

        # Should detect the hero geometry mismatch
        self.assertGreater(classification.classified_count, 0)
        hero_candidates = [
            c for c in classification.candidates if c.node_id == "hero"
        ]
        self.assertGreater(len(hero_candidates), 0)

    def test_full_pipeline_detect_plan_execute(self):
        """Full pipeline: detect → classify → plan → execute."""
        plan = _make_layout_plan([
            ("header", "Header", 0, 0, 1440, 80),
        ])
        doc = IRDocument(file_key="test")
        doc.root = IRNode(
            id="root", name="Root", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="test", node_id="root"),
        )
        library = ProjectLibrary(tokens=[
            ProjectToken(name="spacing-lg", type="spacing", value=24),
        ])

        # Intentional defect
        defects = {"header": {"width": 1400, "height": 70}}
        render_meta = _make_render_meta_with_defects(plan, defects)

        # Diff
        diff_engine = DiffEngine()
        diff_report = diff_engine.diff(plan, render_meta)
        self.assertGreater(len(diff_report.mismatches), 0)

        # Classify
        classifier = RepairClassifier(plan=plan, document=doc)
        classification = classifier.classify(diff_report)
        self.assertGreater(classification.classified_count, 0)

        # Plan
        planner = PatchPlanner(plan=plan, document=doc, library=library)
        patch_plan = planner.plan(classification, iteration=0)
        self.assertGreater(patch_plan.patch_count, 0)

        # Execute
        executor = PatchExecutor(plan=plan, document=doc, library=library)
        exec_result = executor.execute(patch_plan)
        self.assertGreater(exec_result.success_count, 0)

    def test_unresolved_differences_reported(self):
        """When repair can't fix everything, unresolved differences are reported."""
        plan = _make_layout_plan([
            ("n1", "Header", 0, 0, 200, 80),
        ])
        doc = IRDocument(file_key="test")
        doc.root = IRNode(
            id="root", name="Root", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="test", node_id="root"),
        )

        config = RepairConfig(max_iterations=1, similarity_threshold=0.99)

        # Render always returns wrong values — repair can't converge
        def bad_render(p, s, d, i):
            return {"n1": {"x": 0, "y": 0, "width": 150, "height": 60}}, ""

        loop = RepairLoop(config=config, render_fn=bad_render)
        result = loop.run(plan, doc, run_id="unresolved")

        # Result should indicate unresolved differences
        self.assertFalse(result.success)
        # The final iteration's diff report should have mismatches
        self.assertIsNotNone(result.history)


if __name__ == "__main__":
    unittest.main()
