#!/usr/bin/env python3
"""
Rendering Pipeline Tests (Part 7; browser render guarded in Part 11).
"""
import shutil
import unittest
from pathlib import Path

from core.asset_manager import AssetManager
from core.render_harness import RenderHarness


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


CHROMIUM_AVAILABLE = _chromium_available()


class TestRenderPipeline(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("/tmp/figmaforge_test")
        shutil.rmtree(self.test_dir, ignore_errors=True)
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

    @unittest.skipUnless(
        CHROMIUM_AVAILABLE,
        "headless chromium not available — install with: "
        "pip install playwright && playwright install chromium",
    )
    def test_harness_determinism(self):
        # Part 11: the harness now performs a real browser render.
        res = self.harness.render(
            '<html><body><div data-node-id="n1"></div></body></html>',
            {"w": 320, "h": 640},
            "build1",
        )
        self.assertTrue(res.screenshot_path.exists())
        self.assertGreater(res.screenshot_path.stat().st_size, 0)
        self.assertIsInstance(res.layout_metadata, dict)


if __name__ == "__main__":
    unittest.main()
