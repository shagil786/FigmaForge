"""
Real-toolchain money test for the bundler harness (Part 21 Task 4).

The canonical honesty-audit fixture's output for all three web backends is
scaffolded through the REAL harness (``scaffold`` → ``build`` with real
npm → ``serve_built`` on an ephemeral port → per-component chromium
screenshot) and must render with zero console/page errors — the S2 fix is
what makes this possible.  Also proves the serve port is never fixed
(two sequential serves get different ports).

Skipped when chromium or npm are unavailable (render-test convention).
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
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
from bundler_harness import SPECS, build, scaffold, serve_built  # noqa: E402
from test_backend_honesty_audit import canonical_fixture  # noqa: E402


def _chromium_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


_HAVE_TOOLS = _chromium_available() and shutil.which("npm") is not None

_BACKENDS = [
    ("react", "react_tailwind", ReactTailwindBackend()),
    ("vue", "vue", VueBackend()),
    ("svelte", "svelte", SvelteBackend()),
]


def _render_pages(url: str, dist_dir: Path, out_dir: Path):
    """Visit every ``*.html`` page, collect errors, screenshot each.

    Returns ``(screens, console_errors, page_errors, png_sizes)``.
    """
    from playwright.sync_api import sync_playwright

    html_pages = sorted(p.name for p in dist_dir.glob("*.html"))
    screens: list[dict] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    png_sizes: dict[str, int] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("console", lambda m: console_errors.append(m.text)
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            for name in html_pages:
                page.goto(f"{url}{name}", timeout=15_000)
                page.wait_for_load_state("networkidle", timeout=15_000)
                page.evaluate("document.fonts.ready")
                png = out_dir / name.replace(".html", ".png")
                page.screenshot(path=str(png), full_page=True)
                png_sizes[name] = png.stat().st_size
                screens.append(name)
        finally:
            browser.close()
    return screens, console_errors, page_errors, png_sizes


@unittest.skipUnless(_HAVE_TOOLS, "chromium and/or npm unavailable")
class TestBundlerHarnessRealToolchain(unittest.TestCase):
    def test_canonical_backends_build_and_render_through_harness(self):
        doc, plan, resolution = canonical_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for label, backend_name, backend in _BACKENDS:
                with self.subTest(backend=label):
                    generated = tmp / f"{label}-generated"
                    generated.mkdir(parents=True, exist_ok=True)
                    out = backend.generate(doc, plan, resolution=resolution,
                                           viewport=1440.0)
                    for f in out.files:
                        p = generated / f.path
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_text(f.content, encoding="utf-8")

                    bundle_dir = tmp / f"{label}-bundle"
                    scaffold(backend_name, generated, bundle_dir)
                    build(bundle_dir)  # real npm install + vite build

                    dist = bundle_dir / "dist"
                    self.assertTrue((dist / "Root.html").is_file(),
                                    f"{label}: multi-page dist missing Root.html")
                    url, stop = serve_built(dist)
                    try:
                        screens, console, perr, sizes = _render_pages(
                            url, dist, tmp / f"{label}-shots",
                        )
                    finally:
                        stop()

                    self.assertEqual(screens, ["Root.html"], label)
                    self.assertEqual(perr, [], f"{label}: page errors: {perr}")
                    self.assertEqual(
                        console, [], f"{label}: console errors: {console}",
                    )
                    self.assertGreater(
                        sizes["Root.html"], 1024,
                        f"{label}: screenshot too small: {sizes}",
                    )

    def test_serve_port_never_fixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            # Two minimal dist dirs; ports must differ across serves.
            ports = []
            for i in range(2):
                dist = tmp / f"dist-{i}"
                dist.mkdir(parents=True, exist_ok=True)
                (dist / "index.html").write_text("<html></html>", encoding="utf-8")
                url, stop = serve_built(dist)
                try:
                    ports.append(int(url.split(":")[2].strip("/")))
                finally:
                    stop()
            self.assertEqual(len(set(ports)), 2,
                             f"expected different ports, got {ports}")


if __name__ == "__main__":
    unittest.main()
