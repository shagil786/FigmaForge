#!/usr/bin/env python3
"""
Snapshot tests for the React and CSS generators (Part 6).

The desktop fixture is analyzed and the generated ``VNode`` tree plus ``VStyle``
maps are compared byte-for-byte against checked-in golden files under
``tests/snapshots/generator/``.

To regenerate (only when the change is intended):

    REWRITE_SNAPSHOTS=1 python3 tests/test_generator_snapshot.py
"""

import json
import os
import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.css_generator import CSSGenerator
from core.figma_fixtures import FixtureLoader
from core.figma_types import FigmaFile
from core.ir_builder import IRBuilder
from core.layout_analyzer import LayoutAnalyzer
from core.library_types import LibraryLoader
from core.react_generator import ReactGenerator

SNAPSHOT_DIR = plugin_root / "tests" / "snapshots" / "generator"
SNAPSHOT_NAME = "desktop-gen.json"
REWRITE_ENV = "REWRITE_SNAPSHOTS"


def build_snapshot_payload() -> str:
    """Deterministic JSON spec: per-screen VNode tree + base style map."""
    loader = FixtureLoader(plugin_root / "fixtures" / "figma")
    doc = IRBuilder().build(FigmaFile.from_dict("lay1440", loader.load("layout_desktop")))
    plan = LayoutAnalyzer().analyze(doc, library=LibraryLoader().load())

    react_gen = ReactGenerator()
    css_gen = CSSGenerator()

    spec = {}
    for screen in plan.screens:
        spec[screen.node_id] = {
            "vnode": react_gen.generate(screen).to_dict(),
            "style": css_gen.generate_style(screen).base,
        }
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


class TestGeneratorSnapshot(unittest.TestCase):
    def test_snapshot_is_valid_json(self):
        json.loads(build_snapshot_payload())

    def test_snapshot_matches_checked_in(self):
        snapshot_path = SNAPSHOT_DIR / SNAPSHOT_NAME
        current = build_snapshot_payload()

        if os.environ.get(REWRITE_ENV) == "1":
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(current, encoding="utf-8")
            self.skipTest(f"Snapshot regenerated at {snapshot_path}")

        if not snapshot_path.exists():
            self.fail(
                f"Snapshot {snapshot_path} is missing. "
                f"Run with {REWRITE_ENV}=1 to generate it."
            )

        self.assertEqual(
            current,
            snapshot_path.read_text(encoding="utf-8"),
            "Generated output no longer matches the snapshot. If the change is "
            f"intended, regenerate with {REWRITE_ENV}=1 and review the diff.",
        )

    def test_deterministic_output(self):
        self.assertEqual(build_snapshot_payload(), build_snapshot_payload())

    def test_semantic_tags_and_figma_ids(self):
        spec = json.loads(build_snapshot_payload())
        page = spec.get("0:1", {})
        vnode = page.get("vnode", {})
        self.assertEqual(vnode.get("node_id"), "0:1")
        self.assertIn("data-figma-id", vnode.get("props", {}))
        # Header/footer name mapping should produce semantic tags somewhere.
        header = self._find_by_name(vnode, "Header")
        footer = self._find_by_name(vnode, "Footer")
        if header:
            self.assertEqual(header["tag"], "header")
        if footer:
            self.assertEqual(footer["tag"], "footer")

    @staticmethod
    def _find_by_name(vnode, name):
        if vnode.get("props", {}).get("name") == name:
            return vnode
        for child in vnode.get("children", []):
            hit = TestGeneratorSnapshot._find_by_name(child, name)
            if hit is not None:
                return hit
        return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
