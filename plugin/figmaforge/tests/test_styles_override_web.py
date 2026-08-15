"""
styles_override seam for the web backends (Part 22 Task 1).

The repair loop's repaired styles (``{node_id: {base, breakpoints}}`` — the
html_css Part-20 seam) must reach react/vue/svelte generated output so
``pipeline.py repair --backend <web>`` regenerates the right backend:

- vue / svelte: the override union applies in ``ScopedCssGenerator._node``
  after ``_extend`` and BEFORE the absolute-position pop (design review F6:
  ``generate_style`` writes ``position: absolute`` into base, so applying the
  union after the pop would re-attach absolute positioning).
- react: the override union goes into the computed style first, then the
  existing class loops emit it (design review F2). ``background`` →
  ``bg-[...]``, ``color`` → ``text-[...]``, ``fontSize`` → ``text-[...]px``
  are new mappings; an override ``background`` suppresses the IR fill classes
  INCLUDING image fills (F3 — override wins entirely, matching html_css's
  replacement semantics).

Absent/empty override → byte-identical output (the html_css contract).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))
tests_dir = Path(__file__).resolve().parent
if str(tests_dir) not in sys.path:
    sys.path.insert(0, str(tests_dir))

from backends.react_tailwind import ReactTailwindBackend  # noqa: E402
from backends.svelte import SvelteBackend  # noqa: E402
from backends.vue import VueBackend  # noqa: E402
from core.ir_types import (  # noqa: E402
    IRColor,
    IRDocument,
    IRFill,
    IRNode,
    IRSource,
    IRStyle,
    IRTextContent,
    IRTypography,
    KIND_FRAME,
    KIND_PAGE,
    KIND_TEXT,
)
from core.layout_types import (  # noqa: E402
    AxisSizing,
    Box,
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    DISPLAY_NONE,
    LayoutNodePlan,
    LayoutPlan,
    SizingSpec,
    SIZING_FIXED,
    TextModel,
)
from test_component_fallback import _plain_fixture  # noqa: E402


def _absolute_root_fixture():
    """A screen whose ROOT node is absolute-positioned (F6 ordering test)."""
    card = IRNode(
        id="abs:1", name="Card", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="absov", node_id="abs:1"),
        style=IRStyle(fills=[IRFill(kind="solid", color=IRColor(r=0.1, g=0.1, b=0.1, a=1.0))]),
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="absov", node_id="page-1"),
        children=[card],
    )
    doc = IRDocument(file_key="absov", name="Absolute", pages=[page])
    doc.root = card
    card_plan = LayoutNodePlan(
        node_id="abs:1", name="Root", kind="frame", display=DISPLAY_ABSOLUTE,
        box=Box(x=0, y=0, width=200, height=80),
        sizing=SizingSpec(
            horizontal=AxisSizing(mode=SIZING_FIXED),
            vertical=AxisSizing(mode=SIZING_FIXED),
        ),
    )
    plan = LayoutPlan(file_key="absov", viewport=1440.0, screens=[card_plan])
    return doc, plan


def _image_fill_fixture():
    """A frame with a resolved image fill (F3 suppression test)."""
    card = IRNode(
        id="img:1", name="Hero", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="imgf", node_id="img:1"),
        style=IRStyle(fills=[IRFill(kind="image", image_ref="img/hero")]),
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="imgf", node_id="page-1"),
        children=[card],
    )
    doc = IRDocument(file_key="imgf", name="Image", pages=[page])
    doc.root = card
    card_plan = LayoutNodePlan(
        node_id="img:1", name="Root", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=300, height=200),
        sizing=SizingSpec(
            horizontal=AxisSizing(mode=SIZING_FIXED),
            vertical=AxisSizing(mode=SIZING_FIXED),
        ),
    )
    plan = LayoutPlan(file_key="imgf", viewport=1440.0, screens=[card_plan])
    return doc, plan


class TestVueScopedOverride(unittest.TestCase):
    def setUp(self):
        self.backend = VueBackend()

    def _generate(self, doc, plan, options=None):
        out = self.backend.generate(doc, plan, viewport=1440.0, options=options)
        by_path = {f.path: f.content for f in out.files}
        self.assertIn("Root.vue", by_path)
        return by_path["Root.vue"]

    def test_override_background_in_scoped_css(self):
        doc, plan = _plain_fixture()
        vue = self._generate(doc, plan, options={
            "styles_override": {
                "card:1": {"base": {"background": "#00ff00"}, "breakpoints": {}},
            },
        })
        self.assertIn("background: #00ff00", vue)
        # The un-repaired child is untouched.
        self.assertIn(".n-t-1", vue)

    def test_absolute_override_does_not_reattach_position(self):
        doc, plan = _absolute_root_fixture()
        # The serialized layer carries position: absolute (F6) — the pop must
        # run AFTER the union so the override cannot re-attach it.
        vue = self._generate(doc, plan, options={
            "styles_override": {
                "abs:1": {
                    "base": {"background": "#00ff00", "position": "absolute"},
                    "breakpoints": {},
                },
            },
        })
        self.assertIn("background: #00ff00", vue)
        self.assertNotIn("position: absolute", vue)

    def test_empty_override_byte_identical(self):
        doc, plan = _plain_fixture()
        baseline = self._generate(doc, plan)
        self.assertEqual(self._generate(doc, plan, options={}), baseline)
        self.assertEqual(
            self._generate(doc, plan, options={"styles_override": {}}), baseline,
        )


class TestSvelteScopedOverride(unittest.TestCase):
    def setUp(self):
        self.backend = SvelteBackend()

    def _generate(self, doc, plan, options=None):
        out = self.backend.generate(doc, plan, viewport=1440.0, options=options)
        by_path = {f.path: f.content for f in out.files}
        self.assertIn("Root.svelte", by_path)
        return by_path["Root.svelte"]

    def test_override_background_in_scoped_css(self):
        doc, plan = _plain_fixture()
        svelte = self._generate(doc, plan, options={
            "styles_override": {
                "card:1": {"base": {"background": "#00ff00"}, "breakpoints": {}},
            },
        })
        self.assertIn("background: #00ff00", svelte)

    def test_empty_override_byte_identical(self):
        doc, plan = _plain_fixture()
        baseline = self._generate(doc, plan)
        self.assertEqual(self._generate(doc, plan, options={}), baseline)


class TestReactOverride(unittest.TestCase):
    def setUp(self):
        self.backend = ReactTailwindBackend()

    def _generate(self, doc, plan, options=None, assets=None):
        opts = dict(options or {})
        if assets is not None:
            opts = {**opts, "assets": assets}
        out = self.backend.generate(doc, plan, viewport=1440.0, options=opts)
        by_path = {f.path: f.content for f in out.files}
        self.assertIn("Root.tsx", by_path)
        return by_path["Root.tsx"]

    def test_override_background_wins_over_ir_fill(self):
        doc, plan = _plain_fixture()
        tsx = self._generate(doc, plan, options={
            "styles_override": {
                "card:1": {"base": {"background": "#00ff00"}, "breakpoints": {}},
            },
        })
        # Exactly one bg- class, carrying the override — the IR fill (#1a1a1a)
        # is suppressed (F3).
        self.assertEqual(tsx.count("bg-["), 1)
        self.assertIn("bg-[#00ff00]", tsx)
        self.assertNotIn("#1a1a1a", tsx)

    def test_override_background_suppresses_image_fill(self):
        doc, plan = _image_fill_fixture()
        assets = {"img:1": {"path": "/store/hero.png"}}
        # Control: the resolved image fill emits real url classes.
        control = self._generate(doc, plan, assets=assets)
        self.assertIn("bg-[url(/store/hero.png)]", control)
        self.assertIn("bg-cover", control)
        # With an override the image classes are replaced by the solid color.
        tsx = self._generate(doc, plan, options={
            "styles_override": {
                "img:1": {"base": {"background": "#00ff00"}, "breakpoints": {}},
            },
        })
        self.assertNotIn("bg-[url(", tsx)
        self.assertNotIn("bg-cover", tsx)
        self.assertIn("bg-[#00ff00]", tsx)

    def test_override_background_on_node_without_fill(self):
        doc, plan = _plain_fixture()
        # t:1 has no fills — the override adds the background.
        tsx = self._generate(doc, plan, options={
            "styles_override": {
                "t:1": {"base": {"background": "#00ff00"}, "breakpoints": {}},
            },
        })
        self.assertIn("bg-[#00ff00]", tsx)

    def test_override_layout_prop_maps(self):
        doc, plan = _plain_fixture()
        # Override values use the style layer's format ("24px", like
        # ``styles_to_dict`` serializes — not raw numbers).
        tsx = self._generate(doc, plan, options={
            "styles_override": {
                "card:1": {"base": {"paddingTop": "24px"}, "breakpoints": {}},
            },
        })
        self.assertIn("pt-[24px]", tsx)

    def test_override_font_size_maps_and_suppresses_ir(self):
        doc, plan = _plain_fixture()
        # The control emits the IR typography size (14px) for t:1.
        control = self._generate(doc, plan)
        self.assertIn("text-[14px]", control)
        # The style layer serializes sizes as "20px" strings (F5 format) —
        # no double unit.
        tsx = self._generate(doc, plan, options={
            "styles_override": {
                "t:1": {"base": {"fontSize": "20px"}, "breakpoints": {}},
            },
        })
        self.assertIn("text-[20px]", tsx)
        self.assertNotIn("text-[20pxpx]", tsx)
        self.assertNotIn("text-[14px]", tsx)

    def test_override_color_maps(self):
        doc, plan = _plain_fixture()
        tsx = self._generate(doc, plan, options={
            "styles_override": {
                "t:1": {"base": {"color": "#ff0000"}, "breakpoints": {}},
            },
        })
        self.assertIn("text-[#ff0000]", tsx)

    def test_override_breakpoint_variant(self):
        doc, plan = _plain_fixture()
        # Real serialized keys are "768px" strings (F5) — no double unit, and
        # the plan's own breakpoints are NOT double-emitted (the override
        # layer is authoritative).
        tsx = self._generate(doc, plan, options={
            "styles_override": {
                "card:1": {
                    "base": {},
                    "breakpoints": {"768px": {"width": "350px"}},
                },
            },
        })
        self.assertIn("max-[768px]:w-[350px]", tsx)
        self.assertNotIn("max-[768pxpx]", tsx)
        self.assertEqual(tsx.count("max-[768px]:"), 1)

    def test_empty_override_byte_identical(self):
        doc, plan = _plain_fixture()
        baseline = self._generate(doc, plan)
        self.assertEqual(self._generate(doc, plan, options={}), baseline)
        self.assertEqual(
            self._generate(doc, plan, options={"styles_override": {}}), baseline,
        )


if __name__ == "__main__":
    unittest.main()
