"""
Real-browser smoke test for the render harness (Part 11).

Skipped when headless chromium is unavailable so suites stay green on
machines without Playwright installed.

Run:  python3 -m unittest tests.test_render_harness_smoke -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.diff_engine import DiffEngine
from core.generator_types import VStyle
from core.ir_types import IRDocument, IRNode, IRSource, KIND_FRAME, KIND_PAGE
from core.layout_types import Box, DISPLAY_FLEX, LayoutNodePlan, LayoutPlan
from core.render_harness import RenderHarness
from core.render_html import generate_render_html


def _playwright_importable() -> bool:
    """Cheap import-only check; never launches a browser (import-time safe)."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


_CHROMIUM_AVAILABLE: bool | None = None


def _chromium_available() -> bool:
    """Lazy, memoized launch check — only runs inside tests, never at import."""
    global _CHROMIUM_AVAILABLE
    if _CHROMIUM_AVAILABLE is not None:
        return _CHROMIUM_AVAILABLE
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        _CHROMIUM_AVAILABLE = True
    except Exception:
        _CHROMIUM_AVAILABLE = False
    return _CHROMIUM_AVAILABLE


def _build_html() -> str:
    """One 200x100 red box ('node-a') as the page's only child."""
    frame = IRNode(
        id="node-a", name="Box", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="fk", node_id="node-a"),
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="fk", node_id="page-1"),
        children=[frame],
    )
    doc = IRDocument(file_key="fk", name="Smoke", pages=[page])
    styles = {
        "node-a": VStyle(base={
            "width": "200px",
            "height": "100px",
            "backgroundColor": "#ff0000",
        }),
    }
    return generate_render_html(doc, styles, {"w": 800, "h": 600})


@unittest.skipUnless(
    _playwright_importable(),
    "headless chromium not available — install with: "
    "pip install playwright && playwright install chromium",
)
class TestRenderHarnessSmoke(unittest.TestCase):
    def setUp(self):
        # Lazy in-test chromium check so importing this module never launches
        # a browser (review precedent from Task 3).
        if not _chromium_available():
            self.skipTest("headless chromium not launchable")
        self._tmp = tempfile.TemporaryDirectory(prefix="figmaforge_smoke_")
        self.addCleanup(self._tmp.cleanup)
        self.out_dir = Path(self._tmp.name)

    def test_smoke_render_screenshot_and_metadata(self):
        harness = RenderHarness(self.out_dir)
        result = harness.render(_build_html(), {"w": 800, "h": 600}, "smoke")

        self.assertTrue(result.screenshot_path.exists())
        self.assertGreater(result.screenshot_path.stat().st_size, 0)
        self.assertIn("node-a", result.layout_metadata)
        entry = result.layout_metadata["node-a"]
        self.assertEqual(entry["width"], 200)
        self.assertEqual(entry["height"], 100)
        self.assertEqual(entry["x"], 0)
        self.assertEqual(entry["y"], 0)
        self.assertIn("fontSize", entry["styles"])
        self.assertIn("backgroundColor", entry["styles"])
        self.assertEqual(entry["styles"]["backgroundColor"], "rgb(255, 0, 0)")

    def test_smoke_metadata_feeds_diff_engine(self):
        harness = RenderHarness(self.out_dir)
        result = harness.render(_build_html(), {"w": 800, "h": 600}, "smoke2")

        screen = LayoutNodePlan(
            node_id="node-a", name="Box", kind="frame",
            display=DISPLAY_FLEX, box=Box(x=0, y=0, width=200, height=100),
        )
        plan = LayoutPlan(file_key="fk", viewport=800.0, screens=[screen])

        report = DiffEngine().diff(plan, result.layout_metadata)
        types = [m["type"] for m in report.mismatches]
        self.assertNotIn("missing_in_render", types)
        self.assertNotIn("geometry_mismatch", types)


if __name__ == "__main__":
    unittest.main()
