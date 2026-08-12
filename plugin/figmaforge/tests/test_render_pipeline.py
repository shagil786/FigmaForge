#!/usr/bin/env python3
"""
Rendering Pipeline Tests (Part 7; browser render guarded in Part 11).
"""
import tempfile
import unittest
from pathlib import Path

from core.asset_manager import AssetManager
from core.render_harness import RenderHarness

META_HTML = (
    '<html><body><div data-node-id="n1" style="width:50px;height:50px"></div>'
    "<script>window.__figmaforge_meta={};document.querySelectorAll("
    '"[data-node-id]").forEach(el=>{const r=el.getBoundingClientRect();'
    "window.__figmaforge_meta[el.getAttribute(\"data-node-id\")]="
    '{"x":Math.round(r.x),"y":Math.round(r.y),"width":Math.round(r.width),'
    '"height":Math.round(r.height)}});</script></body></html>'
)


class TestRenderPipeline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.test_dir = Path(self._tmp.name)
        self.asset_dir = self.test_dir / "assets"
        self.render_dir = self.test_dir / "render"
        self.am = AssetManager(self.asset_dir)
        self.harness = RenderHarness(self.render_dir)

    def test_asset_hashing(self):
        data = b"image-content"
        h1 = self.am.ingest(data, "url1", "image", "png")
        h2 = self.am.ingest(data, "url2", "image", "png")
        self.assertEqual(h1, h2)
        self.assertIn(h1, self.am.manifest.assets)

    def test_svg_validation(self):
        unsafe = b"<svg><script>alert(1)</script></svg>"
        with self.assertRaises(ValueError):
            self.am.ingest(unsafe, "bad", "svg", "svg")

        safe = b"<svg><rect/></svg>"
        h = self.am.ingest(safe, "good", "svg", "svg")
        self.assertIn(h, self.am.manifest.assets)

    def test_harness_determinism(self):
        # Part 11: the harness now performs a real browser render.
        # Lazy availability check so importing this module never launches
        # a browser.
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("playwright not installed")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                browser.close()
        except Exception as exc:
            self.skipTest(f"headless chromium not available: {exc}")

        res = self.harness.render(META_HTML, {"w": 320, "h": 640}, "build1")
        self.assertTrue(res.screenshot_path.exists())
        self.assertGreater(res.screenshot_path.stat().st_size, 0)
        self.assertIsInstance(res.layout_metadata, dict)
        self.assertIn("n1", res.layout_metadata)
        self.assertEqual(res.layout_metadata["n1"]["width"], 50)


if __name__ == "__main__":
    unittest.main()
