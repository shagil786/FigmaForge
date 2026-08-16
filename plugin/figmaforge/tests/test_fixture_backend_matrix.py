"""Run every implemented backend across the checked-in layout fixture matrix."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from backends.flutter import FlutterBackend
from backends.html_css import HtmlCssBackend
from backends.react_tailwind import ReactTailwindBackend
from backends.svelte import SvelteBackend
from backends.swiftui import SwiftUIBackend
from backends.vue import VueBackend
from core.figma_fixtures import FixtureLoader
from core.figma_types import FigmaFile
from core.ir_builder import IRBuilder
from core.layout_analyzer import LayoutAnalyzer
from core.library_types import LibraryLoader


FIXTURE_DIR = PLUGIN_ROOT / "fixtures" / "figma"
LAYOUT_FIXTURES = (
    "layout_desktop",
    "layout_tablet",
    "layout_mobile",
    "layout_nested",
    "layout_content_overflow",
)
BACKENDS = (
    HtmlCssBackend(), ReactTailwindBackend(), VueBackend(),
    SvelteBackend(), SwiftUIBackend(), FlutterBackend(),
)


class TestFixtureBackendMatrix(unittest.TestCase):
    def test_all_backends_generate_for_all_layout_fixtures(self):
        loader = FixtureLoader(FIXTURE_DIR)
        library = LibraryLoader().load()
        for fixture_name in LAYOUT_FIXTURES:
            with self.subTest(fixture=fixture_name):
                document = IRBuilder().build(
                    FigmaFile.from_dict(fixture_name, loader.load(fixture_name))
                )
                plan = LayoutAnalyzer().analyze(document, library=library)
                for backend in BACKENDS:
                    with self.subTest(backend=backend.name):
                        output = backend.generate(
                            document, plan, resolution=None,
                            viewport=float(plan.viewport or 1440.0),
                        )
                        self.assertTrue(output.files, f"{backend.name} emitted no files")
                        self.assertTrue(
                            all(file.content.strip() for file in output.files),
                            f"{backend.name} emitted an empty file",
                        )


if __name__ == "__main__":
    unittest.main()
