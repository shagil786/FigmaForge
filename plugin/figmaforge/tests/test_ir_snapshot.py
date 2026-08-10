#!/usr/bin/env python3
"""
Snapshot test for the normalized Design IR.

The full ``file.json`` fixture (plus ``images.json`` asset refs) is normalized
and serialized, then compared byte-for-byte against a checked-in snapshot under
``tests/snapshots/``. A mismatch means the IR shape or the fixture changed.

To regenerate the snapshot (only when the change is intended):

    REWRITE_SNAPSHOTS=1 python3 tests/test_ir_snapshot.py
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
from core.ir_types import ir_to_json

SNAPSHOT_DIR = plugin_root / "tests" / "snapshots"
SNAPSHOT_NAME = "file.json"
REWRITE_ENV = "REWRITE_SNAPSHOTS"


def build_snapshot_payload() -> str:
    loader = FixtureLoader(plugin_root / "fixtures" / "figma")
    raw = loader.load_file("file")
    figma_file = FigmaFile.from_dict("abc123", raw)
    images = loader.load("images")["images"]
    document = IRBuilder(images=images).build(figma_file)
    return ir_to_json(document) + "\n"


class TestIRSnapshot(unittest.TestCase):
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
            "Normalized IR no longer matches the snapshot. If the change is "
            f"intended, regenerate with {REWRITE_ENV}=1 and review the diff.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
