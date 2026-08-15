"""
Self-contained component references + quoted tailwind token keys (Part 21 Task 1).

S2: a screen whose resolution report resolves component/instance nodes to
names must emit self-contained output — every referenced component name has a
local definition (react function / vue render-function fallback / svelte
snippet) rendering that node's own subtree, so the file compiles AND renders
(no more ``ReferenceError: ButtonCard is not defined`` blank page).

S3: ``_generate_tailwind_config`` must quote keys so hyphenated design-token
names (``brand-blue``) produce valid JavaScript.
"""

from __future__ import annotations

import shutil
import subprocess
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
    AlignmentSpec,
    AxisSizing,
    Box,
    DISPLAY_FLEX,
    DISPLAY_NONE,
    LayoutNodePlan,
    LayoutPlan,
    SizingSpec,
    SIZING_FIXED,
    TextModel,
)
from core.resolver import MatchResult, ResolutionReport  # noqa: E402
from test_backend_honesty_audit import canonical_fixture  # noqa: E402


def _plain_fixture():
    """A screen with NO component/instance resolution (control fixture)."""
    title = IRNode(
        id="t:1", name="Title", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="ctrl", node_id="t:1"),
        typography=IRTypography(font_size=14.0, font_weight=500.0),
        text=IRTextContent(characters="Hello"),
    )
    card = IRNode(
        id="card:1", name="Card", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="ctrl", node_id="card:1"),
        style=IRStyle(fills=[IRFill(kind="solid", color=IRColor(r=0.1, g=0.1, b=0.1, a=1.0))]),
        children=[title],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="ctrl", node_id="page-1"),
        children=[card],
    )
    doc = IRDocument(file_key="ctrl", name="Plain", pages=[page])
    doc.root = card

    card_plan = LayoutNodePlan(
        node_id="card:1", name="Root", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=80),
        sizing=SizingSpec(
            horizontal=AxisSizing(mode=SIZING_FIXED),
            vertical=AxisSizing(mode=SIZING_FIXED),
        ),
    )
    title_plan = LayoutNodePlan(
        node_id="t:1", name="Title", kind="text", display=DISPLAY_NONE,
        text=TextModel(characters="Hello"),
    )
    card_plan.children.append(title_plan)
    plan = LayoutPlan(file_key="ctrl", viewport=1440.0, screens=[card_plan])
    return doc, plan


def _collision_fixture():
    """Two component nodes resolving to the SAME name (one fallback)."""
    comp_a = IRNode(
        id="comp:1", name="BadgeA", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="collide", node_id="comp:1"),
    )
    comp_b = IRNode(
        id="comp:2", name="BadgeB", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="collide", node_id="comp:2"),
    )
    root = IRNode(
        id="0:1", name="Root", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="collide", node_id="0:1"),
        children=[comp_a, comp_b],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="collide", node_id="page-1"),
        children=[root],
    )
    doc = IRDocument(file_key="collide", name="Collide", pages=[page])
    doc.root = root

    plan_a = LayoutNodePlan(
        node_id="comp:1", name="BadgeA", kind="frame", display=DISPLAY_NONE,
        box=Box(x=0, y=0, width=100, height=30),
    )
    plan_b = LayoutNodePlan(
        node_id="comp:2", name="BadgeB", kind="frame", display=DISPLAY_NONE,
        box=Box(x=0, y=0, width=100, height=30),
    )
    root_plan = LayoutNodePlan(
        node_id="0:1", name="Root", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=100),
    )
    root_plan.children = [plan_a, plan_b]
    plan = LayoutPlan(file_key="collide", viewport=1440.0, screens=[root_plan])

    resolution = ResolutionReport(
        file_key="collide",
        resolved=[
            MatchResult(status="resolved", figma_component="comp:1",
                        figma_name="Badge", matches=["Badge"], reason="fixture"),
            MatchResult(status="resolved", figma_component="comp:2",
                        figma_name="Badge", matches=["Badge"], reason="fixture"),
        ],
        instances=[],
    )
    return doc, plan, resolution


def _children_fixture():
    """A component reference WITH children (fallback must render them)."""
    child = IRNode(
        id="c:1", name="BadgeText", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="kids", node_id="c:1"),
        text=IRTextContent(characters="Badge"),
    )
    comp = IRNode(
        id="comp:1", name="Badge", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="kids", node_id="comp:1"),
        children=[child],
    )
    root = IRNode(
        id="0:1", name="Root", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="kids", node_id="0:1"),
        children=[comp],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="kids", node_id="page-1"),
        children=[root],
    )
    doc = IRDocument(file_key="kids", name="Kids", pages=[page])
    doc.root = root

    comp_plan = LayoutNodePlan(
        node_id="comp:1", name="Badge", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=100, height=30),
    )
    child_plan = LayoutNodePlan(
        node_id="c:1", name="BadgeText", kind="text", display=DISPLAY_NONE,
        text=TextModel(characters="Badge"),
    )
    comp_plan.children.append(child_plan)
    root_plan = LayoutNodePlan(
        node_id="0:1", name="Root", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=100),
    )
    root_plan.children.append(comp_plan)
    plan = LayoutPlan(file_key="kids", viewport=1440.0, screens=[root_plan])

    resolution = ResolutionReport(
        file_key="kids",
        resolved=[MatchResult(
            status="resolved", figma_component="comp:1", figma_name="Badge",
            matches=["Badge"], reason="fixture",
        )],
        instances=[],
    )
    return doc, plan, resolution


class TestReactComponentFallback(unittest.TestCase):
    def setUp(self):
        self.backend = ReactTailwindBackend()

    def _generate(self, doc, plan, resolution=None):
        out = self.backend.generate(doc, plan, resolution=resolution, viewport=1440.0)
        by_path = {f.path: f.content for f in out.files}
        self.assertIn("Root.tsx", by_path)
        return by_path["Root.tsx"]

    def test_canonical_component_refs_have_local_definitions(self):
        doc, plan, resolution = canonical_fixture()
        tsx = self._generate(doc, plan, resolution)

        # Call sites keep the component tags (audit signal preserved) and are
        # self-closing with NO className (the fallback owns the styling).
        self.assertIn(
            '<ButtonCard data-figma-id="comp:1" name="ButtonCard"></ButtonCard>',
            tsx,
        )
        self.assertIn(
            '<PrimaryButton data-figma-id="inst:1" name="Inst"></PrimaryButton>',
            tsx,
        )
        # Local fallback definitions exist for BOTH previously-undefined names.
        self.assertIn("function ButtonCard({ className = '' }: { className?: string }) {", tsx)
        self.assertIn("function PrimaryButton({ className = '' }: { className?: string }) {", tsx)
        # The fallback renders the node's own subtree as a plain div (classes + id).
        self.assertIn(
            '<div data-figma-id="comp:1" name="ButtonCard" className="block w-[120px] h-[36px]">',
            tsx,
        )
        # Instance fallbacks carry the honesty marker.
        self.assertIn("fidelity: component_instance approximated", tsx)

    def test_every_uppercase_tag_is_defined(self):
        """Self-containment lock: no JSX tag is referenced without a definition."""
        import re
        doc, plan, resolution = canonical_fixture()
        tsx = self._generate(doc, plan, resolution)
        defined = set(re.findall(r"function ([A-Za-z_$][A-Za-z0-9_$]*)\(", tsx))
        html_tags = {
            "div", "span", "p", "a", "button", "section", "header", "footer",
            "main", "nav", "article", "aside", "h1", "h2", "h3", "h4", "h5",
            "h6", "ul", "ol", "li", "img", "figure", "figcaption", "form",
            "input", "label", "select", "option", "textarea", "table", "thead",
            "tbody", "tr", "th", "td", "blockquote", "code", "pre", "em",
            "strong", "small", "hr", "br", "svg", "path", "circle", "rect",
        }
        for tag in re.findall(r"<([A-Z][A-Za-z0-9_$]*)\b", tsx):
            self.assertTrue(
                tag in defined or tag.lower() in html_tags,
                f"referenced component {tag!r} has no definition",
            )

    def test_plain_fixture_unchanged(self):
        doc, plan = _plain_fixture()
        tsx = self._generate(doc, plan, resolution=None)
        self.assertNotIn("function ButtonCard", tsx)
        self.assertNotIn("component_instance", tsx)
        self.assertNotIn(">ButtonCard<", tsx)

    def test_deterministic(self):
        doc, plan, resolution = canonical_fixture()
        a = self._generate(doc, plan, resolution)
        b = self._generate(doc, plan, resolution)
        self.assertEqual(a, b)

    def test_name_collision_deduped(self):
        doc, plan, resolution = _collision_fixture()
        tsx = self._generate(doc, plan, resolution)
        # Two nodes resolving to the same name → exactly one fallback.
        self.assertEqual(tsx.count("function Badge("), 1)

    def test_component_children_rendered_in_fallback(self):
        doc, plan, resolution = _children_fixture()
        tsx = self._generate(doc, plan, resolution)
        # Call site self-closes; the child text lives inside the fallback div.
        self.assertIn('<Badge data-figma-id="comp:1" name="Badge"></Badge>', tsx)
        self.assertIn("function Badge({ className = '' }: { className?: string }) {", tsx)
        fallback_start = tsx.index("function Badge(")
        self.assertIn('<span data-figma-id="c:1"', tsx[fallback_start:])


class TestVueComponentFallback(unittest.TestCase):
    def setUp(self):
        self.backend = VueBackend()

    def _generate(self, doc, plan, resolution=None):
        out = self.backend.generate(doc, plan, resolution=resolution, viewport=1440.0)
        by_path = {f.path: f.content for f in out.files}
        self.assertIn("Root.vue", by_path)
        return by_path["Root.vue"]

    def test_canonical_component_refs_have_local_definitions(self):
        doc, plan, resolution = canonical_fixture()
        vue = self._generate(doc, plan, resolution)

        self.assertIn("import { h } from 'vue';", vue)
        # Fallback components registered in script setup, rendering the node
        # as a plain div carrying its scoped class.
        self.assertIn(
            "const ButtonCard = { setup: (_, { slots }) => () => h('div', { "
            "'data-figma-id': 'comp:1', 'class': 'n-comp-1', 'name': 'ButtonCard' }, []) };",
            vue,
        )
        self.assertIn("const PrimaryButton", vue)
        self.assertIn("// fidelity: component_instance approximated", vue)
        # Call site keeps the tag (audit signal) without the scoped class.
        self.assertIn(
            '<ButtonCard data-figma-id="comp:1" name="ButtonCard"></ButtonCard>',
            vue,
        )
        self.assertNotIn('<ButtonCard data-figma-id="comp:1" class="n-', vue)

    def test_plain_fixture_unchanged(self):
        doc, plan = _plain_fixture()
        vue = self._generate(doc, plan, resolution=None)
        self.assertNotIn("h('div'", vue)
        self.assertNotIn("component_instance", vue)


class TestSvelteComponentFallback(unittest.TestCase):
    def setUp(self):
        self.backend = SvelteBackend()

    def _generate(self, doc, plan, resolution=None):
        out = self.backend.generate(doc, plan, resolution=resolution, viewport=1440.0)
        by_path = {f.path: f.content for f in out.files}
        self.assertIn("Root.svelte", by_path)
        return by_path["Root.svelte"]

    def test_canonical_component_refs_have_local_definitions(self):
        doc, plan, resolution = canonical_fixture()
        svelte = self._generate(doc, plan, resolution)

        self.assertIn("{#snippet ButtonCard()}", svelte)
        self.assertIn("{#snippet PrimaryButton()}", svelte)
        # Snippet body renders the node's own subtree with its scoped class.
        self.assertIn(
            '<div data-figma-id="comp:1" class="n-comp-1" name="ButtonCard"></div>',
            svelte,
        )
        self.assertIn("<!-- fidelity: component_instance approximated", svelte)
        # Call site keeps the tag (audit signal) without the scoped class.
        self.assertIn(
            '<ButtonCard data-figma-id="comp:1" name="ButtonCard"></ButtonCard>',
            svelte,
        )
        self.assertNotIn('<ButtonCard data-figma-id="comp:1" class="n-', svelte)

    def test_plain_fixture_unchanged(self):
        doc, plan = _plain_fixture()
        svelte = self._generate(doc, plan, resolution=None)
        self.assertNotIn("{#snippet", svelte)
        self.assertNotIn("component_instance", svelte)


class TestTailwindConfigQuoting(unittest.TestCase):
    """S3: hyphenated token keys must be quoted so the config is valid JS."""

    def test_canonical_config_quotes_hyphenated_keys(self):
        doc, plan, resolution = canonical_fixture()
        out = ReactTailwindBackend().generate(
            doc, plan, resolution=resolution, viewport=1440.0,
        )
        config = next(f.content for f in out.files
                      if f.path == "tailwind.config.figmaforge.js")
        self.assertIn('"brand-blue": "#3366cc"', config)
        self.assertIn('"space-4": "16px"', config)
        # No unquoted hyphenated keys remain.
        for line in config.splitlines():
            stripped = line.strip()
            if stripped.startswith("brand-blue") or stripped.startswith("space-4"):
                self.fail(f"unquoted token key: {line!r}")

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_config_loads_in_node(self):
        doc, plan, resolution = canonical_fixture()
        out = ReactTailwindBackend().generate(
            doc, plan, resolution=resolution, viewport=1440.0,
        )
        config = next(f.content for f in out.files
                      if f.path == "tailwind.config.figmaforge.js")
        with __import__("tempfile").TemporaryDirectory() as tmp:
            path = Path(tmp) / "tailwind.config.figmaforge.js"
            path.write_text(config, encoding="utf-8")
            result = subprocess.run(
                ["node", "-e", f"require({str(path)!r}); console.log('OK')"],
                capture_output=True, text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                f"config is invalid JS:\n{result.stderr}",
            )
            self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
