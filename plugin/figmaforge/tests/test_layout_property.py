#!/usr/bin/env python3
"""
Property-based tests for the Part-5 layout engine.

No Hypothesis dependency (repo is stdlib-only): a deterministic generator
sweeps parameter combinations and viewport widths, asserting invariants that
must hold for every layout the engine emits:

- fixed designs solved at their native width reproduce Figma bounds,
- fill width == parent content width,
- percent (grow) width matches the reference share math,
- hug sizes respect min/max,
- contradictions and underdetermined bounds are always surfaced, never resolved,
- breakpoints are only emitted with evidence.

The reference layout math below is intentionally independent of the engine so a
regression in the solver shows up as a test failure.
"""

import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.ir_types import (
    IRDimensions, IRDocument, IRLayout, IRNode, IRPosition, IRSource,
    IRSpacing, IRTextContent, IRTypography, KIND_FRAME, KIND_PAGE, KIND_TEXT,
)
from core.layout_analyzer import LayoutAnalyzer
from core.library_types import LibraryLoader

VIEWPORTS = [320, 640, 768, 1024, 1440, 1920]


# ---------------------------------------------------------------------------
# IR construction helpers
# ---------------------------------------------------------------------------

def _src(node_id: str) -> IRSource:
    return IRSource(file_key="prop", node_id=node_id, node_type="FRAME")


def frame(
    node_id, name, width=None, height=None,
    sizing_h=None, sizing_v=None, min_w=None, max_w=None, min_h=None, max_h=None,
    direction=None, padding=None, gap=None, grow=None,
    x=0.0, y=0.0, mode="auto", children=None, pos_mode="auto",
):
    layout = None
    if direction is not None or gap is not None or padding is not None or grow is not None:
        layout = IRLayout(
            mode=mode, direction=direction, justify="MIN", align="MIN",
            padding=padding, gap=gap, grow=grow,
        )
    dims = IRDimensions(
        width=width, height=height, min_width=min_w, max_width=max_w,
        min_height=min_h, max_height=max_h,
        sizing_horizontal=sizing_h, sizing_vertical=sizing_v,
    )
    pos = IRPosition(mode=pos_mode, x=x, y=y, left=x, top=y)
    return IRNode(
        id=node_id, name=name, kind=KIND_FRAME, node_type="FRAME",
        source=_src(node_id), dimensions=dims, position=pos, layout=layout,
        children=children or [],
    )


def text(node_id, chars, size=16, line_height=20, x=0.0, y=0.0):
    dims = IRDimensions(sizing_horizontal="AUTO", sizing_vertical="AUTO")
    pos = IRPosition(mode="auto", x=x, y=y)
    return IRNode(
        id=node_id, name=node_id, kind=KIND_TEXT, node_type="TEXT",
        source=_src(node_id), dimensions=dims, position=pos,
        typography=IRTypography(font_family="Inter", font_size=size, line_height=line_height),
        text=IRTextContent(characters=chars),
    )


def page(node_id, children):
    return IRNode(
        id=node_id, name=node_id, kind=KIND_PAGE, node_type="CANVAS",
        source=_src(node_id), children=children,
    )


def document(children):
    return IRDocument(file_key="prop", name="prop", pages=[page("page", children)])


def analyze(doc, viewport=None):
    return LayoutAnalyzer().analyze(doc, library=LibraryLoader().load(), viewport=viewport)


# ---------------------------------------------------------------------------
# Reference layout math (independent of the engine)
# ---------------------------------------------------------------------------

def reference_column(root_w, padding, gap, children):
    """Author Figma-consistent bounds for a fixed-height column."""
    pos = []
    y = padding
    for w, h in children:
        pos.append((padding, y, w, h))
        y += h + gap
    return pos


def make_column(root_w, padding, gap, child_sizes):
    c_nodes = []
    for i, (w, h) in enumerate(child_sizes):
        c_nodes.append(frame(
            f"c{i}", f"c{i}", width=w, height=h, sizing_h="FIXED", sizing_v="FIXED",
            x=padding, y=0,  # y overwritten below by reference placement
        ))
    # overwrite with reference positions
    positions = reference_column(root_w, padding, gap, child_sizes)
    for cnode, (x, y, w, h) in zip(c_nodes, positions):
        cnode.position.x, cnode.position.y = x, y
    content_h = 2 * padding + sum(h for _, h in child_sizes) + gap * (len(child_sizes) - 1)
    root = frame(
        "root", "root", width=root_w, height=content_h,
        sizing_h="FIXED", sizing_v="FIXED",
        direction="column", padding=IRSpacing(top=padding, right=padding, bottom=padding, left=padding),
        gap=gap, children=c_nodes,
    )
    return document([root])


class TestFixedReproducesFigmaBounds(unittest.TestCase):
    def test_sweep_of_columns(self):
        for root_w in (320, 640, 1024, 1440):
            for padding in (0, 8, 16):
                for gap in (0, 4, 8):
                    doc = make_column(root_w, padding, gap, [(80, 24), (120, 32)])
                    plan = analyze(doc)
                    for n in plan.nodes():
                        if n.box is not None and n.figma_box is not None:
                            self.assertLessEqual(
                                n.bounds_delta, 1e-4,
                                f"root_w={root_w} pad={padding} gap={gap} "
                                f"{n.node_id} {n.box} vs {n.figma_box}",
                            )


class TestFillWidth(unittest.TestCase):
    def test_fill_matches_content_width(self):
        doc = make_column(400, 16, 8, [(80, 24), (120, 32)])
        root = doc.pages[0].children[0]
        filler = frame(
            "fill", "fill", height=24, sizing_h="FILL", sizing_v="FIXED",
        )
        root.children.insert(0, filler)
        plan = analyze(doc)
        self.assertAlmostEqual(plan.node("fill").box.width, 400 - 32, places=4)


class TestPercentReference(unittest.TestCase):
    def test_grow_shares_match_reference(self):
        W, padding, gap = 500, 10, 20
        content = W - 2 * padding
        share = (content - gap) / 2.0  # two equal-grow children
        a = frame("a", "a", height=40, grow=1, sizing_h="FILL", x=padding, y=padding)
        b = frame("b", "b", height=40, grow=1, sizing_h="FILL", x=padding + share + gap, y=padding)
        root = frame(
            "root", "root", width=W, height=60, sizing_h="FIXED", sizing_v="FIXED",
            direction="row", padding=IRSpacing(top=padding, right=padding, bottom=padding, left=padding),
            gap=gap, children=[a, b],
        )
        plan = analyze(document([root]))
        self.assertAlmostEqual(plan.node("a").box.width, share, places=4)
        self.assertAlmostEqual(plan.node("b").box.width, share, places=4)
        self.assertEqual(plan.node("a").sizing.horizontal.mode, "percent")

    def test_percent_scales_with_viewport(self):
        W, padding, gap = 500, 10, 20
        # Root is FILL so its width (and thus child percents) scale with viewport.
        root = frame(
            "root", "root", width=W, height=60, sizing_h="FILL", sizing_v="FIXED",
            direction="row", padding=IRSpacing(top=padding, right=padding, bottom=padding, left=padding),
            gap=gap,
            children=[
                frame("a", "a", height=40, grow=1, sizing_h="FILL"),
                frame("b", "b", height=40, grow=1, sizing_h="FILL"),
            ],
        )
        for viewport in VIEWPORTS:
            plan = analyze(document([root]), viewport=viewport)
            content = viewport - 2 * padding
            expected = (content - gap) / 2.0
            self.assertAlmostEqual(
                plan.node("a").box.width, expected, places=2,
                msg=f"viewport={viewport}",
            )


class TestHugMinMax(unittest.TestCase):
    def test_hug_text_clamped_to_max(self):
        # "A very long sentence indeed" at 16px -> ~ 26 chars * 8.8 = 228.8
        # natural width; a max of 200 must clamp it.
        t = text("t", "A very long sentence indeed", size=16, line_height=20)
        t.dimensions.max_width = 200
        root = frame(
            "root", "root", width=300, height=40, sizing_h="FIXED", sizing_v="FIXED",
            direction="row", padding=IRSpacing(top=8, right=8, bottom=8, left=8),
            gap=0, children=[t],
        )
        plan = analyze(document([root]))
        self.assertAlmostEqual(plan.node("t").box.width, 200.0, places=3)
        self.assertEqual(plan.node("t").sizing.horizontal.mode, "hug")


class TestContradictionsAlwaysSurfaced(unittest.TestCase):
    def test_min_max_contradiction(self):
        bad = frame(
            "bad", "bad", width=100, height=20,
            min_w=120, max_w=80, sizing_h="FIXED", sizing_v="FIXED",
        )
        root = frame(
            "root", "root", width=300, height=60, sizing_h="FIXED", sizing_v="FIXED",
            direction="column", children=[bad],
        )
        plan = analyze(document([root]))
        self.assertTrue(
            any(d.code == "contradiction" for d in plan.node("bad").diagnostics),
        )
        self.assertEqual(plan.node("bad").confidence, 0.0)


class TestUnderdeterminedNeverResolved(unittest.TestCase):
    def test_empty_hug_frame_is_underdetermined(self):
        empty = frame(
            "empty", "empty", sizing_h="AUTO", sizing_v="AUTO",
        )
        root = frame(
            "root", "root", width=300, height=60, sizing_h="FIXED", sizing_v="FIXED",
            direction="column", children=[empty],
        )
        plan = analyze(document([root]))
        self.assertIsNone(plan.node("empty").box)
        self.assertTrue(
            any(d.code == "underdetermined" for d in plan.node("empty").diagnostics),
        )


class TestBreakpointEvidenceOnly(unittest.TestCase):
    def test_fixed_design_has_no_breakpoint_changes(self):
        doc = make_column(1440, 16, 8, [(80, 24), (120, 32)])
        plan = analyze(doc)
        self.assertEqual(plan.breakpoints.changes, [])
        self.assertEqual(
            set(plan.breakpoints.no_change),
            {"root", "page", "c0", "c1"},
        )

    def test_percent_design_emits_evidence_backed_changes(self):
        root = frame(
            "root", "root", width=1440, height=60, sizing_h="FILL", sizing_v="FIXED",
            direction="row", padding=IRSpacing(top=10, right=10, bottom=10, left=10),
            gap=20,
            children=[frame("a", "a", height=40, grow=1, sizing_h="FILL")],
        )
        plan = analyze(document([root]))
        self.assertGreater(len(plan.breakpoints.changes), 0)
        for change in plan.breakpoints.changes:
            self.assertTrue(change.evidence)
            self.assertTrue(change.node_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
