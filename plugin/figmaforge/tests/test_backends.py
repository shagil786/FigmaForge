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
