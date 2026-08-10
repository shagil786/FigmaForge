#!/usr/bin/env python3
"""
Snapshot test for the Part-4 resolution report.

The ``variants.json`` fixture is resolved against the project library and the
resulting report is compared byte-for-byte against a checked-in snapshot under
``tests/snapshots/``.

To regenerate (only when the change is intended):

    REWRITE_SNAPSHOTS=1 python3 tests/test_resolution_snapshot.py
"""

import json
import os
import sys
import unittest
from pathlib import Path

# Add plugin root to path so `core.*` packages resolve
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.figma_fixtures import FixtureLoader
from core.figma_types import FigmaFile
from core.ir_builder import IRBuilder
from core.library_types import LibraryLoader
from core.resolver import Resolver, report_to_json

SNAPSHOT_DIR = plugin_root / "tests" / "snapshots"
SNAPSHOT_NAME = "resolution-report.json"
REWRITE_ENV = "REWRITE_SNAPSHOTS"


def build_snapshot_payload() -> str:
    loader = FixtureLoader(plugin_root / "fixtures" / "figma")
    doc = IRBuilder().build(FigmaFile.from_dict("vars123", loader.load("variants")))
    report = Resolver(doc, LibraryLoader().load()).resolve()
    return report_to_json(report) + "\n"


class TestResolutionSnapshot(unittest.TestCase):
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
            "Resolution report no longer matches the snapshot. If the change is "
            f"intended, regenerate with {REWRITE_ENV}=1 and review the diff.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
