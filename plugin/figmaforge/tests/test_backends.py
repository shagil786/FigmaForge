#!/usr/bin/env python3
"""
Tests for the backend adapter architecture (Part 10).

Verifies:
- BackendAdapter protocol (abstract methods, capability declaration)
- BackendRegistry (register, lookup, find, preflight)
- HtmlCssBackend (fully implemented, generates HTML + CSS)
- Stub backends (React+Tailwind, Vue, Svelte, SwiftUI, Flutter)
- FidelityLoss reporting (unsupported features are explicit)
- Feature vocabulary completeness
"""
import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from backends.protocol import (
    BackendAdapter,
    BackendCapabilities,
    Feature,
    FidelityLoss,
    GeneratedFile,
    GeneratedOutput,
    WEB_COMMON_FEATURES,
)
from backends.registry import BackendRegistry, reset_registry


# ---------------------------------------------------------------------------
# Protocol tests
# ---------------------------------------------------------------------------

class TestFeature(unittest.TestCase):
    """Feature vocabulary constants."""

    def test_core_features_defined(self):
        self.assertEqual(Feature.FLEX, "flex")
        self.assertEqual(Feature.GRID, "grid")
        self.assertEqual(Feature.ABSOLUTE_POSITIONING, "absolute_positioning")
        self.assertEqual(Feature.FILLS_SOLID, "fills_solid")
        self.assertEqual(Feature.FONT_SIZE, "font_size")

    def test_web_common_features_is_frozenset(self):
        self.assertIsInstance(WEB_COMMON_FEATURES, frozenset)
        self.assertIn(Feature.FLEX, WEB_COMMON_FEATURES)
        self.assertIn(Feature.PADDING, WEB_COMMON_FEATURES)
        self.assertIn(Feature.FONT_SIZE, WEB_COMMON_FEATURES)


class TestFidelityLoss(unittest.TestCase):
    """FidelityLoss records unsupported features explicitly."""

    def test_to_dict(self):
        loss = FidelityLoss(
            feature=Feature.GRID,
            node_id="1:2",
            message="Grid not supported",
            severity="warning",
            fallback_applied="flex approximation",
        )
        d = loss.to_dict()
        self.assertEqual(d["feature"], "grid")
        self.assertEqual(d["node_id"], "1:2")
        self.assertEqual(d["severity"], "warning")
        self.assertEqual(d["fallback_applied"], "flex approximation")

    def test_to_dict_without_fallback(self):
        loss = FidelityLoss(
            feature=Feature.BLUR,
            node_id="3:4",
            message="Blur not supported",
        )
        d = loss.to_dict()
        self.assertNotIn("fallback_applied", d)


class TestBackendCapabilities(unittest.TestCase):
    """BackendCapabilities declares support levels."""

    def test_supports_returns_correct_level(self):
        caps = BackendCapabilities(
            supported_features=frozenset({Feature.FLEX, Feature.GRID}),
            unsupported_features=frozenset({Feature.BLUR}),
            partial_features=frozenset({Feature.SHADOWS}),
            styling_system="css",
            framework="html",
            renderer="browser",
            file_extensions=(".html", ".css"),
        )
        self.assertEqual(caps.supports(Feature.FLEX), "supported")
        self.assertEqual(caps.supports(Feature.GRID), "supported")
        self.assertEqual(caps.supports(Feature.BLUR), "unsupported")
        self.assertEqual(caps.supports(Feature.SHADOWS), "partial")
        self.assertEqual(caps.supports(Feature.BORDERS), "unsupported")

    def test_to_dict(self):
        caps = BackendCapabilities(
            supported_features=frozenset({Feature.FLEX}),
            unsupported_features=frozenset({Feature.BLUR}),
            partial_features=frozenset(),
            styling_system="css",
            framework="html",
            renderer="browser",
            file_extensions=(".html",),
        )
        d = caps.to_dict()
        self.assertEqual(d["framework"], "html")
        self.assertEqual(d["styling_system"], "css")
        self.assertIn("flex", d["supported_features"])
        self.assertIn("blur", d["unsupported_features"])


class TestGeneratedOutput(unittest.TestCase):
    """GeneratedOutput tracks files and fidelity losses."""

    def test_has_errors(self):
        output = GeneratedOutput()
        self.assertFalse(output.has_errors)

        output.fidelity_losses.append(FidelityLoss(
            feature=Feature.GRID,
            node_id="1:1",
            message="Grid not supported",
            severity="warning",
        ))
        self.assertFalse(output.has_errors)

        output.fidelity_losses.append(FidelityLoss(
            feature=Feature.BLUR,
            node_id="1:2",
            message="Blur not supported",
            severity="error",
        ))
        self.assertTrue(output.has_errors)

    def test_to_dict(self):
        output = GeneratedOutput()
        output.files.append(GeneratedFile(
            path="index.html",
            content="<html></html>",
            language="html",
            node_ids=["1:1"],
        ))
        d = output.to_dict()
        self.assertEqual(d["file_count"], 1)
        self.assertEqual(d["loss_count"], 0)
        self.assertEqual(len(d["files"]), 1)


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestBackendRegistry(unittest.TestCase):
    """BackendRegistry manages backend adapters."""

    def setUp(self):
        self.registry = BackendRegistry()

    def test_register_and_get(self):
        from backends.html_css import HtmlCssBackend
        backend = HtmlCssBackend()
        self.registry.register(backend)
        self.assertIs(self.registry.get("html_css"), backend)

    def test_register_duplicate_raises(self):
        from backends.html_css import HtmlCssBackend
        self.registry.register(HtmlCssBackend())
        with self.assertRaises(ValueError):
            self.registry.register(HtmlCssBackend())

    def test_require_missing_raises(self):
        with self.assertRaises(KeyError):
            self.registry.require("nonexistent")

    def test_unregister(self):
        from backends.html_css import HtmlCssBackend
        self.registry.register(HtmlCssBackend())
        self.registry.unregister("html_css")
        self.assertIsNone(self.registry.get("html_css"))

    def test_find_by_framework(self):
        from backends.html_css import HtmlCssBackend
        from backends.vue import VueBackend
        self.registry.register(HtmlCssBackend())
        self.registry.register(VueBackend())

        html_backends = self.registry.find(framework="html")
        self.assertEqual(len(html_backends), 1)
        self.assertEqual(html_backends[0].name, "html_css")

        vue_backends = self.registry.find(framework="vue")
        self.assertEqual(len(vue_backends), 1)

    def test_find_by_renderer(self):
        from backends.html_css import HtmlCssBackend
        from backends.swiftui import SwiftUIBackend
        self.registry.register(HtmlCssBackend())
        self.registry.register(SwiftUIBackend())

        browser = self.registry.find(renderer="browser")
        self.assertEqual(len(browser), 1)
        self.assertEqual(browser[0].name, "html_css")

        xcode = self.registry.find(renderer="xcode_preview")
        self.assertEqual(len(xcode), 1)
        self.assertEqual(xcode[0].name, "swiftui")

    def test_list_sorted(self):
        from backends.html_css import HtmlCssBackend
        from backends.vue import VueBackend
        self.registry.register(VueBackend())
        self.registry.register(HtmlCssBackend())
        names = [b.name for b in self.registry.list()]
        self.assertEqual(names, ["html_css", "vue"])

    def test_names_sorted(self):
        from backends.flutter import FlutterBackend
        from backends.html_css import HtmlCssBackend
        self.registry.register(FlutterBackend())
        self.registry.register(HtmlCssBackend())
        self.assertEqual(self.registry.names(), ["flutter", "html_css"])

    def test_capabilities_report(self):
        from backends.html_css import HtmlCssBackend
        self.registry.register(HtmlCssBackend())
        report = self.registry.capabilities_report()
        self.assertIn("html_css", report)
        self.assertEqual(report["html_css"]["framework"], "html")


# ---------------------------------------------------------------------------
# HtmlCssBackend tests
# ---------------------------------------------------------------------------

class TestHtmlCssBackend(unittest.TestCase):
    """HTML + CSS backend — the fully implemented reference backend."""

    def setUp(self):
        from backends.html_css import HtmlCssBackend
        self.backend = HtmlCssBackend()

    def test_name(self):
        self.assertEqual(self.backend.name, "html_css")
        self.assertEqual(self.backend.display_name, "HTML + CSS")

    def test_capabilities(self):
        caps = self.backend.capabilities
        self.assertEqual(caps.framework, "html")
        self.assertEqual(caps.styling_system, "css")
        self.assertEqual(caps.renderer, "browser")
        self.assertIn(Feature.FLEX, caps.supported_features)
        self.assertIn(Feature.GRID, caps.supported_features)
        self.assertIn(Feature.SHADOWS, caps.supported_features)
        # HTML/CSS supports everything — no unsupported features
        self.assertEqual(len(caps.unsupported_features), 0)

    def test_generate_empty_layout(self):
        from core.layout_types import LayoutPlan
        from core.ir_types import IRDocument
        output = self.backend.generate(
            document=IRDocument(),
            layout_plan=LayoutPlan(),
        )
        self.assertIsInstance(output, GeneratedOutput)
        self.assertEqual(output.metadata["backend"], "html_css")
        self.assertEqual(output.metadata["screen_count"], 0)

    def test_web_common_shared_machinery(self):
        """The shared web machinery lives in web_common with public names."""
        from backends import web_common
        from core.layout_types import DISPLAY_FLEX, LayoutNodePlan

        for name in (
            "VNode",
            "VStyle",
            "CssStyleGenerator",
            "VNodeBuilder",
            "semantic_tag",
            "camel_to_kebab",
            "escape_html",
            "escape_attr",
        ):
            self.assertTrue(
                hasattr(web_common, name), f"web_common is missing {name}"
            )

        # A trivial one-node plan lowers to the expected flex base style,
        # guarding the shared module in isolation.
        plan = LayoutNodePlan(
            node_id="1:1",
            name="Row",
            display=DISPLAY_FLEX,
            direction="row",
        )
        style = web_common.CssStyleGenerator().generate_style(plan)
        self.assertEqual(style.base.get("display"), "flex")
        self.assertEqual(style.base.get("flexDirection"), "row")
        self.assertEqual(web_common.semantic_tag("Header"), "header")
        self.assertEqual(web_common.camel_to_kebab("paddingTop"), "padding-top")
        self.assertEqual(web_common.escape_html("<b>&"), "&lt;b&gt;&amp;")

    def test_html_css_emit_smoke(self):
        """Refactored html_css still generates the full file set end-to-end."""
        from core.figma_fixtures import FixtureLoader
        from core.figma_types import FigmaFile
        from core.ir_builder import IRBuilder
        from core.layout_analyzer import LayoutAnalyzer
        from core.library_types import LibraryLoader

        plugin_root = Path(__file__).parent.parent
        loader = FixtureLoader(plugin_root / "fixtures" / "figma")
        doc = IRBuilder().build(
            FigmaFile.from_dict("lay1440", loader.load("layout_desktop"))
        )
        plan = LayoutAnalyzer().analyze(doc, library=LibraryLoader().load())
        output = self.backend.generate(document=doc, layout_plan=plan)

        # One HTML file per screen plus a combined stylesheet.
        html_files = [f for f in output.files if f.path.endswith(".html")]
        self.assertEqual(len(html_files), len(plan.screens))
        self.assertTrue(any(f.path == "styles.css" for f in output.files))
        self.assertEqual(output.metadata["screen_count"], len(plan.screens))

        # The first screen's HTML file covers all of its nodes.
        screen_ids = {
            n.node_id for n in plan.screens[0].walk() if n.node_id
        }
        self.assertTrue(screen_ids)
        self.assertTrue(set(html_files[0].node_ids) >= screen_ids)
        self.assertIn("<!DOCTYPE html>", html_files[0].content)

    # -- declared-supported features must be emitted, never silently dropped --
    @staticmethod
    def _rich_fixture():
        """A screen exercising the full IR style surface the audit found
        declared-supported but silently dropped by the reference backend."""
        from core.ir_types import (
            IRBlur,
            IRColor,
            IRDocument,
            IRFill,
            IRNode,
            IRShadow,
            IRSource,
            IRStyle,
            IRTextContent,
            IRTypography,
            KIND_FRAME,
            KIND_PAGE,
            KIND_TEXT,
        )
        from core.layout_types import (
            AlignmentSpec,
            Box,
            DISPLAY_FLEX,
            DISPLAY_NONE,
            LayoutNodePlan,
            LayoutPlan,
            OVERFLOW_CLIP,
            OverflowSpec,
            TextModel,
        )

        label = IRNode(
            id="t:3", name="Label", kind=KIND_TEXT, node_type="TEXT",
            source=IRSource(file_key="html", node_id="t:3"),
            typography=IRTypography(
                font_family="Inter", font_size=14.0, font_weight=600.0,
                letter_spacing=0.5, text_decoration="UNDERLINE",
                text_case="UPPER",
            ),
            text=IRTextContent(characters="Save changes"),
        )
        card = IRNode(
            id="card:1", name="Card", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="html", node_id="card:1"),
            style=IRStyle(
                fills=[IRFill(
                    kind="solid", color=IRColor(r=0.1, g=0.1, b=0.1, a=1.0),
                )],
                shadows=[IRShadow(
                    color=IRColor(r=0.0, g=0.0, b=0.0, a=0.25),
                    x=0.0, y=4.0, blur=8.0,
                )],
                blurs=[IRBlur(kind="layer", radius=4.0)],
            ),
            children=[label],
        )
        page = IRNode(
            id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
            source=IRSource(file_key="html", node_id="page-1"),
            children=[card],
        )
        doc = IRDocument(file_key="html", name="Rich", pages=[page])
        doc.root = card

        card_plan = LayoutNodePlan(
            node_id="card:1", name="Card", kind="frame", display=DISPLAY_FLEX,
            box=Box(x=0, y=0, width=200, height=80),
            alignment=AlignmentSpec(align_self="MAX"),
            overflow=OverflowSpec(x=OVERFLOW_CLIP, y=OVERFLOW_CLIP),
        )
        label_plan = LayoutNodePlan(
            node_id="t:3", name="Label", kind="text", display=DISPLAY_NONE,
            text=TextModel(characters="Save changes"),
        )
        card_plan.children.append(label_plan)
        plan = LayoutPlan(file_key="html", viewport=1440.0, screens=[card_plan])
        return doc, plan

    @staticmethod
    def _fidelity_fixture():
        """Gradient + image fills and an absolute node — all representable in
        HTML/CSS, so they must lower to real CSS (plus a marker for images)."""
        from core.ir_types import (
            IRColor,
            IRDocument,
            IRFill,
            IRGradientStop,
            IRNode,
            IRPosition,
            IRSource,
            IRStyle,
            KIND_FRAME,
            KIND_PAGE,
        )
        from core.layout_types import (
            Box,
            DISPLAY_ABSOLUTE,
            DISPLAY_FLEX,
            DISPLAY_NONE,
            LayoutNodePlan,
            LayoutPlan,
        )

        grad = IRNode(
            id="grad:1", name="Gradient", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="html", node_id="grad:1"),
            style=IRStyle(fills=[IRFill(
                kind="gradient",
                gradient_stops=[
                    IRGradientStop(
                        position=0.0, color=IRColor(r=1.0, g=0.0, b=0.0, a=1.0),
                    ),
                    IRGradientStop(
                        position=1.0, color=IRColor(r=0.0, g=0.0, b=1.0, a=1.0),
                    ),
                ],
            )]),
        )
        img = IRNode(
            id="img:1", name="Photo", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="html", node_id="img:1"),
            style=IRStyle(fills=[IRFill(kind="image", image_ref="asset://photo")]),
        )
        badge = IRNode(
            id="abs:1", name="Badge", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="html", node_id="abs:1"),
            position=IRPosition(mode="absolute", left=8.0, top=8.0),
        )
        root = IRNode(
            id="0:9", name="Overlay", kind=KIND_FRAME, node_type="FRAME",
            source=IRSource(file_key="html", node_id="0:9"),
            children=[grad, img, badge],
        )
        page = IRNode(
            id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
            source=IRSource(file_key="html", node_id="page-1"),
            children=[root],
        )
        doc = IRDocument(file_key="html", name="Overlay", pages=[page])
        doc.root = root

        screen = LayoutNodePlan(
            node_id="0:9", name="Overlay", kind="frame",
            display=DISPLAY_FLEX, direction="column",
            box=Box(x=0, y=0, width=400, height=300),
        )
        screen.children.append(LayoutNodePlan(
            node_id="grad:1", name="Gradient", kind="frame",
            display=DISPLAY_NONE, box=Box(x=0, y=0, width=200, height=100),
        ))
        screen.children.append(LayoutNodePlan(
            node_id="img:1", name="Photo", kind="frame",
            display=DISPLAY_NONE, box=Box(x=0, y=0, width=100, height=50),
        ))
        screen.children.append(LayoutNodePlan(
            node_id="abs:1", name="Badge", kind="frame",
            display=DISPLAY_ABSOLUTE, box=Box(x=8, y=8, width=64, height=24),
        ))
        plan = LayoutPlan(file_key="html", viewport=1440.0, screens=[screen])
        return doc, plan

    def test_ir_style_emitted(self):
        """The reference backend lowers the IR style surface, not just layout."""
        doc, plan = self._rich_fixture()
        content = "\n".join(
            f.content for f in self.backend.generate(
                document=doc, layout_plan=plan,
            ).files
        )
        # Shadows -> box-shadow; blur -> filter.
        self.assertIn("box-shadow: 0px 4px 8px rgba(0, 0, 0, 0.25)", content)
        self.assertIn("filter: blur(4px)", content)
        # Text decoration / case / letter spacing / typography.
        self.assertIn("text-decoration: underline", content)
        self.assertIn("text-transform: uppercase", content)
        self.assertIn("letter-spacing: 0.5px", content)
        self.assertIn("font-size: 14px", content)
        self.assertIn("font-family: Inter", content)
        # Overflow clip + explicit align-self.
        self.assertIn("overflow: hidden", content)
        self.assertIn("align-self: flex-end", content)

    def test_gradient_and_image_fills_lowered(self):
        """Gradients are real CSS; image fills degrade with a marker."""
        doc, plan = self._fidelity_fixture()
        output = self.backend.generate(document=doc, layout_plan=plan)
        content = "\n".join(f.content for f in output.files)
        # Gradient -> real linear-gradient.
        self.assertIn(
            "background: linear-gradient(to bottom, #ff0000 0%, #0000ff 100%)",
            content,
        )
        # Image fill -> named fallback + inline marker, never silent.
        self.assertIn("background: #f0f0f0", content)
        self.assertIn(
            "<!-- fidelity: fills_image approximated (solid fallback) -->",
            content,
        )

    def test_image_fill_resolved_emits_real_url(self):
        """A resolved image-fill asset lowers to a real CSS background, no marker."""
        doc, plan = self._fidelity_fixture()
        output = self.backend.generate(
            document=doc, layout_plan=plan,
            options={"assets": {
                "img:1": {"path": "assets/photo.png", "kind": "image"},
            }},
        )
        content = "\n".join(f.content for f in output.files)
        self.assertIn("background-image: url(assets/photo.png)", content)
        self.assertIn("background-size: cover", content)
        self.assertIn("background-position: center", content)
        self.assertNotIn("fills_image approximated", content)

    def test_image_fill_unresolved_keeps_fallback_and_marker(self):
        """Without a resolved asset the honest fallback + marker stays."""
        doc, plan = self._fidelity_fixture()
        output = self.backend.generate(
            document=doc, layout_plan=plan,
            options={"assets": {"other:1": {"path": "x.png", "kind": "image"}}},
        )
        content = "\n".join(f.content for f in output.files)
        self.assertIn("background: #f0f0f0", content)
        self.assertIn(
            "<!-- fidelity: fills_image approximated (solid fallback) -->",
            content,
        )

    def test_absolute_positioning_is_valid_css(self):
        """Positioning lowers to position + anchors, never ``display: absolute``."""
        doc, plan = self._fidelity_fixture()
        content = "\n".join(
            f.content for f in self.backend.generate(
                document=doc, layout_plan=plan,
            ).files
        )
        self.assertIn("position: absolute", content)
        self.assertNotIn("display: absolute", content)

    def test_breakpoints_lower_to_media_rules(self):
        """Breakpoint changes fold into @media rules in the emitted CSS."""
        from core.layout_types import BreakpointChange
        doc, plan = self._rich_fixture()
        plan.screens[0].breakpoints.append(BreakpointChange(
            breakpoint="md", width=768.0, node_id="card:1",
            property="direction", before="column", after="row",
            evidence="measured",
        ))
        content = "\n".join(
            f.content for f in self.backend.generate(
                document=doc, layout_plan=plan,
            ).files
        )
        self.assertIn("@media (max-width: 768px)", content)
        self.assertIn("flex-direction: row", content)

    def test_capability_declaration_honesty(self):
        """Partial features must not be reported supported; emitted ones must."""
        caps = self.backend.capabilities
        supported = caps.supported_features
        for partial in (
            Feature.MARGIN,
            Feature.RELATIVE_POSITIONING,
            Feature.IMAGE_ASSETS,
            Feature.SVG_ASSETS,
            Feature.DESIGN_TOKENS,
            Feature.TOKEN_REFERENCES,
            Feature.PROTOTYPE_LINKS,
            Feature.INTERACTIONS,
        ):
            self.assertIn(partial, caps.partial_features)
            self.assertNotIn(partial, supported)
        # FILLS_IMAGE lifted to supported in Part 18 (resolved-asset path).
        self.assertIn(Feature.FILLS_IMAGE, supported)
        # Features the shared machinery genuinely emits.
        for emitted in (
            Feature.FILLS_SOLID,
            Feature.FILLS_GRADIENT,
            Feature.SHADOWS,
            Feature.BLUR,
            Feature.BORDERS,
            Feature.CORNER_RADIUS,
            Feature.PER_CORNER_RADIUS,
            Feature.OPACITY,
            Feature.TEXT_DECORATION,
            Feature.TEXT_CASE,
            Feature.LETTER_SPACING,
            Feature.OVERFLOW_CLIP,
            Feature.OVERFLOW_SCROLL,
            Feature.BREAKPOINTS,
            Feature.MEDIA_QUERIES,
            Feature.ALIGN_SELF,
        ):
            self.assertIn(emitted, supported)
        # HTML/CSS genuinely supports absolute positioning (position + anchors).
        self.assertIn(Feature.ABSOLUTE_POSITIONING, supported)
        # No common-IR feature is unrepresentable in HTML/CSS.
        self.assertEqual(len(caps.unsupported_features), 0)


class TestHtmlCssStylesOverride(unittest.TestCase):
    """Part 20: the styles_override seam lets repaired styles reach the
    generated html_css output.  Overrides apply per-node AFTER the computed
    style (union on top); absent/empty overrides are byte-identical."""

    def _generate(self, options=None):
        from backends.html_css import HtmlCssBackend
        doc, plan = TestHtmlCssBackend._rich_fixture()
        backend = HtmlCssBackend()
        return backend.generate(document=doc, layout_plan=plan, options=options)

    def test_override_applies_background_to_named_node(self):
        output = self._generate(options={
            "styles_override": {
                "card:1": {"base": {"background": "#ff0000"}, "breakpoints": {}},
            },
        })
        css = next(f.content for f in output.files if f.path == "styles.css")
        # The repaired color replaces the computed fill color on the node.
        self.assertIn("background: #ff0000", css)
        self.assertNotIn("background: #1a1a1a", css)  # computed fill replaced
        # The override is scoped to the node's own selector.
        self.assertIn(".n-card-1", css)

    def test_override_only_touches_listed_node(self):
        output = self._generate(options={
            "styles_override": {
                "card:1": {"base": {"background": "#ff0000"}, "breakpoints": {}},
            },
        })
        css = next(f.content for f in output.files if f.path == "styles.css")
        # The sibling text node keeps its computed styles (its own selector
        # is emitted, and the override value appears exactly once).
        self.assertIn(".n-t-3", css)
        self.assertEqual(css.count("background: #ff0000"), 1)

    def test_absent_or_empty_override_is_byte_identical(self):
        baseline = self._generate(options=None)
        empty = self._generate(options={"styles_override": {}})
        self.assertEqual(
            [(f.path, f.content) for f in baseline.files],
            [(f.path, f.content) for f in empty.files],
        )
        # And the computed background is still present without an override.
        css = next(f.content for f in baseline.files if f.path == "styles.css")
        self.assertIn("background: #1a1a1a", css)

    def test_breakpoints_merged_from_override(self):
        output = self._generate(options={
            "styles_override": {
                "card:1": {
                    "base": {},
                    "breakpoints": {"md": {"flexDirection": "column"}},
                },
            },
        })
        css = next(f.content for f in output.files if f.path == "styles.css")
        self.assertIn("@media (max-width: md)", css)
        self.assertIn("flex-direction: column", css)


# ---------------------------------------------------------------------------
# Stub backend tests
# ---------------------------------------------------------------------------

class TestStubBackends(unittest.TestCase):
    """Verify stub backends implement the protocol correctly."""

    def test_react_tailwind(self):
        from backends.react_tailwind import ReactTailwindBackend
        b = ReactTailwindBackend()
        self.assertEqual(b.name, "react_tailwind")
        self.assertEqual(b.capabilities.framework, "react")
        self.assertEqual(b.capabilities.styling_system, "tailwind")
        self.assertIn(".tsx", b.capabilities.file_extensions)

    def test_vue(self):
        from backends.vue import VueBackend
        b = VueBackend()
        self.assertEqual(b.name, "vue")
        self.assertEqual(b.capabilities.framework, "vue")
        self.assertIn(".vue", b.capabilities.file_extensions)

    def test_svelte(self):
        from backends.svelte import SvelteBackend
        b = SvelteBackend()
        self.assertEqual(b.name, "svelte")
        self.assertEqual(b.capabilities.framework, "svelte")
        self.assertIn(".svelte", b.capabilities.file_extensions)

    def test_swiftui(self):
        from backends.swiftui import SwiftUIBackend
        b = SwiftUIBackend()
        self.assertEqual(b.name, "swiftui")
        self.assertEqual(b.capabilities.framework, "swiftui")
        self.assertEqual(b.capabilities.renderer, "xcode_preview")
        self.assertIn(".swift", b.capabilities.file_extensions)
        # SwiftUI has unsupported features
        self.assertGreater(len(b.capabilities.unsupported_features), 0)

    def test_flutter(self):
        from backends.flutter import FlutterBackend
        b = FlutterBackend()
        self.assertEqual(b.name, "flutter")
        self.assertEqual(b.capabilities.framework, "flutter")
        self.assertEqual(b.capabilities.renderer, "flutter_simulator")
        self.assertIn(".dart", b.capabilities.file_extensions)
        # Flutter has unsupported features
        self.assertGreater(len(b.capabilities.unsupported_features), 0)


# ---------------------------------------------------------------------------
# Cross-backend capability comparison
# ---------------------------------------------------------------------------

class TestCapabilityComparison(unittest.TestCase):
    """Verify different backends declare different capabilities."""

    def test_web_vs_native_feature_gap(self):
        from backends.html_css import HtmlCssBackend
        from backends.swiftui import SwiftUIBackend

        html_caps = HtmlCssBackend().capabilities
        swift_caps = SwiftUIBackend().capabilities

        # HTML/CSS supports more features than SwiftUI
        html_count = len(html_caps.supported_features)
        swift_count = len(swift_caps.supported_features)
        self.assertGreater(html_count, swift_count)

    def test_each_backend_unique_name(self):
        from backends.html_css import HtmlCssBackend
        from backends.react_tailwind import ReactTailwindBackend
        from backends.vue import VueBackend
        from backends.svelte import SvelteBackend
        from backends.swiftui import SwiftUIBackend
        from backends.flutter import FlutterBackend

        backends = [
            HtmlCssBackend(),
            ReactTailwindBackend(),
            VueBackend(),
            SvelteBackend(),
            SwiftUIBackend(),
            FlutterBackend(),
        ]
        names = [b.name for b in backends]
        self.assertEqual(len(names), len(set(names)), "Backend names must be unique")


if __name__ == "__main__":
    unittest.main()
