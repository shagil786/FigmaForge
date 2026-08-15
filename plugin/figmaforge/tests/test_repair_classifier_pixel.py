"""
Classifier registration test for pixel_mismatch (Part 12).

A pixel_mismatch must classify — never land in unclassifiable (repair-inert)
and never be silently dropped.

Run:  python3 -m unittest tests.test_repair_classifier_pixel -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.diff_engine import DiffReport
from core.repair_classifier import CATEGORY_COLOR, RepairClassifier


def _pixel_mismatch_report():
    return DiffReport(
        similarity_score=0.95,
        categories={"geometry": 1.0, "style": 1.0, "pixels": 0.95},
        mismatches=[{
            "node_id": "n1",
            "type": "pixel_mismatch",
            "expected": {
                "region": {"x": 0, "y": 0, "width": 20, "height": 10, "area": 200},
                "baseline_mae": {"r": 12.5, "g": 3.0, "b": 2.0},
            },
            "actual": {"diff_percentage": 0.041},
        }],
    )


class TestPixelMismatchClassification(unittest.TestCase):
    def test_pixel_mismatch_is_classified(self):
        result = RepairClassifier().classify(_pixel_mismatch_report())
        self.assertEqual(result.classified_count, 1)
        self.assertEqual(result.unclassifiable, [])
        candidate = result.candidates[0]
        self.assertEqual(candidate.category, CATEGORY_COLOR)
        self.assertEqual(candidate.node_id, "n1")
        self.assertTrue(candidate.description)  # non-empty description
        self.assertEqual(candidate.expected["region"]["area"], 200)
        self.assertGreater(candidate.confidence, 0.0)

    def test_pixel_mismatch_counted_in_categories(self):
        result = RepairClassifier().classify(_pixel_mismatch_report())
        self.assertEqual(result.categories.get(CATEGORY_COLOR), 1)


if __name__ == "__main__":
    unittest.main()
