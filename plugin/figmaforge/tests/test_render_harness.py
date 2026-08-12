"""
Render Harness contract tests (Part 11).

Mocked-Playwright tests: no browser is launched. A fake ``playwright``
package is injected into ``sys.modules`` so the harness's sync-API contract
is verified deterministically.

Run:  python3 -m unittest tests.test_render_harness -v
"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.render_harness import (
    PLAYWRIGHT_INSTALL_HINT,
    RenderHarness,
    RenderHarnessError,
    normalize_viewport,
)


class _FakePlaywright:
    """Installs a fake ``playwright.sync_api`` into ``sys.modules``."""

    def __init__(self, meta_payload):
        self.meta_payload = meta_payload
        self.page = mock.MagicMock(name="page")
        self.page.evaluate.return_value = meta_payload
        self.browser = mock.MagicMock(name="browser")
        self.browser.new_page.return_value = self.page
        self.p = mock.MagicMock(name="playwright_instance")
        self.p.chromium.launch.return_value = self.browser
        self.context = mock.MagicMock(name="sync_playwright_context")
        self.context.__enter__.return_value = self.p
        self.context.__exit__.return_value = False
        self.sync_playwright = mock.MagicMock(
            name="sync_playwright", return_value=self.context
        )

    def install(self, testcase: unittest.TestCase) -> None:
        module = types.ModuleType("playwright")
        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.sync_playwright = self.sync_playwright
        module.sync_api = sync_api
        patcher = mock.patch.dict(
            sys.modules, {"playwright": module, "playwright.sync_api": sync_api}
        )
        patcher.start()
        testcase.addCleanup(patcher.stop)


META_FIXTURE = {
    "node-a": {
        "x": 0,
        "y": 0,
        "width": 200,
        "height": 100,
        "styles": {
            "fontSize": 16,
            "color": "rgb(0, 0, 0)",
            "backgroundColor": "rgba(0, 0, 0, 0)",
            "padding": "0px",
            "margin": "0px",
        },
    }
}


class TestNormalizeViewport(unittest.TestCase):
    def test_wh_keys(self):
        self.assertEqual(normalize_viewport({"w": 320, "h": 640}),
                         {"width": 320, "height": 640})

    def test_width_height_keys(self):
        self.assertEqual(normalize_viewport({"width": 1440, "height": 900}),
                         {"width": 1440, "height": 900})

    def test_missing_keys_raises(self):
        with self.assertRaises(ValueError):
            normalize_viewport({"w": 320})

    def test_non_numeric_raises(self):
        with self.assertRaises(ValueError):
            normalize_viewport({"w": "wide", "h": 640})

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            normalize_viewport("320x640")

    def test_non_positive_dimension_raises(self):
        with self.assertRaises(ValueError):
            normalize_viewport({"w": 0, "h": 640})
        with self.assertRaises(ValueError):
            normalize_viewport({"w": 320, "h": -640})


class TestRenderHarnessContract(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out_dir = Path(self._tmp.name)
        self.harness = RenderHarness(self.out_dir)

    def test_output_dir_created(self):
        self.assertTrue(self.out_dir.is_dir())

    def test_render_contract(self):
        fake = _FakePlaywright(META_FIXTURE)
        fake.install(self)

        result = self.harness.render("<html></html>", {"w": 1440, "h": 900}, "build1")

        fake.p.chromium.launch.assert_called_once_with()
        fake.browser.new_page.assert_called_once_with(
            viewport={"width": 1440, "height": 900}
        )
        fake.page.goto.assert_called_once_with(
            (self.out_dir / "build1.html").as_uri(), timeout=15_000
        )
        fake.page.wait_for_load_state.assert_called_once_with(
            "networkidle", timeout=15_000
        )
        fake.page.screenshot.assert_called_once_with(
            path=str(self.out_dir / "build1.png"), full_page=True
        )
        fake.page.evaluate.assert_called_once_with("window.__figmaforge_meta || {}")
        fake.browser.close.assert_called_once_with()

        self.assertEqual(result.screenshot_path, self.out_dir / "build1.png")
        self.assertEqual(result.layout_metadata, META_FIXTURE)

    def test_render_writes_html_file(self):
        fake = _FakePlaywright({})
        fake.install(self)
        self.harness.render("<html><body>x</body></html>", {"w": 800, "h": 600}, "b2")
        self.assertEqual(
            (self.out_dir / "b2.html").read_text(encoding="utf-8"),
            "<html><body>x</body></html>",
        )

    def test_render_normalizes_width_height_keys(self):
        fake = _FakePlaywright({})
        fake.install(self)
        self.harness.render("<html></html>", {"width": 390, "height": 844}, "mobile")
        fake.browser.new_page.assert_called_once_with(
            viewport={"width": 390, "height": 844}
        )

    def test_render_non_dict_meta_coerced_to_empty(self):
        fake = _FakePlaywright(["not", "a", "dict"])
        fake.install(self)
        result = self.harness.render("<html></html>", {"w": 800, "h": 600}, "b3")
        self.assertEqual(result.layout_metadata, {})

    def test_missing_playwright_raises_clear_error(self):
        # A sys.modules entry set to None makes the import raise ImportError.
        with mock.patch.dict(
            sys.modules, {"playwright": None, "playwright.sync_api": None}
        ):
            with self.assertRaises(RenderHarnessError) as ctx:
                self.harness.render("<html></html>", {"w": 800, "h": 600}, "b4")
        self.assertEqual(str(ctx.exception), PLAYWRIGHT_INSTALL_HINT)
        self.assertIn("pip install playwright", str(ctx.exception))

    def test_browser_failure_raises_render_harness_error(self):
        fake = _FakePlaywright({})
        fake.install(self)
        fake.p.chromium.launch.side_effect = RuntimeError("Executable doesn't exist")
        with self.assertRaises(RenderHarnessError) as ctx:
            self.harness.render("<html></html>", {"w": 800, "h": 600}, "b5")
        self.assertIn("browser rendering failed", str(ctx.exception))
        self.assertIn("playwright install chromium", str(ctx.exception))

    def test_goto_timeout_raises_render_harness_error(self):
        fake = _FakePlaywright({})
        fake.install(self)
        fake.page.goto.side_effect = Exception("Timeout 15000ms exceeded")
        with self.assertRaises(RenderHarnessError) as ctx:
            self.harness.render("<html></html>", {"w": 800, "h": 600}, "b6")
        self.assertIn("browser rendering failed", str(ctx.exception))

    def test_build_id_path_traversal_rejected(self):
        fake = _FakePlaywright({})
        fake.install(self)
        with self.assertRaises(ValueError):
            self.harness.render("<html></html>", {"w": 800, "h": 600}, "../evil")


if __name__ == "__main__":
    unittest.main()
