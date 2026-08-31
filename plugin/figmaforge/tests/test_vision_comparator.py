#!/usr/bin/env python3
"""Tests for core.vision_comparator."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vision_comparator import (
    VisionScore, VisionComparator, generate_agent_prompt,
    parse_agent_score, vision_compare, _parse_response,
)


class TestVisionScore(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        s = VisionScore(layout=0.85, overall=0.84, summary="test", issues=["a"])
        d = s.to_dict()
        s2 = VisionScore.from_dict(d)
        self.assertAlmostEqual(s2.overall, 0.84)

    def test_passes(self):
        self.assertTrue(VisionScore(overall=0.90).passes(0.85))
        self.assertFalse(VisionScore(overall=0.80).passes(0.85))

    def test_str(self):
        self.assertIn("85%", str(VisionScore(overall=0.85)))


class TestParseResponse(unittest.TestCase):
    def test_valid_json(self):
        raw = json.dumps({"layout": 0.9, "color": 0.8, "typography": 0.85,
                          "spacing": 0.82, "hierarchy": 0.88, "composition": 0.86,
                          "overall": 0.87, "summary": "Good", "issues": [], "suggestions": []})
        s = _parse_response(raw)
        self.assertAlmostEqual(s.overall, 0.87)

    def test_markdown_fences(self):
        raw = '```json\n{"layout":0.8,"color":0.7,"typography":0.82,"spacing":0.78,"hierarchy":0.85,"composition":0.8,"overall":0.8,"summary":"x","issues":[],"suggestions":[]}\n```'
        s = _parse_response(raw)
        self.assertAlmostEqual(s.overall, 0.80)

    def test_invalid(self):
        s = _parse_response("not json at all")
        self.assertEqual(s.overall, 0.0)


class TestAgentPrompt(unittest.TestCase):
    def test_contains_paths(self):
        p = generate_agent_prompt("/a.png", "/b.png")
        self.assertIn("/a.png", p)
        self.assertIn("/b.png", p)

    def test_contains_rubric(self):
        p = generate_agent_prompt("/a.png", "/b.png")
        for dim in ["layout", "color", "typography", "spacing", "hierarchy", "composition"]:
            self.assertIn(dim, p)


class TestParseAgentScore(unittest.TestCase):
    def test_valid(self):
        r = json.dumps({"layout": 0.9, "color": 0.85, "typography": 0.88,
                        "spacing": 0.82, "hierarchy": 0.91, "composition": 0.87,
                        "overall": 0.88, "summary": "Good", "issues": [], "suggestions": []})
        s = parse_agent_score(r)
        self.assertAlmostEqual(s.overall, 0.88)
        self.assertEqual(s.provider, "agent")


class TestMockProvider(unittest.TestCase):
    def test_mock_auto_detect(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            comp = VisionComparator(provider="auto")
            self.assertEqual(comp.provider_name, "mock")

    def test_mock_compare(self):
        # Create minimal PNGs
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as a:
            a.write(png_bytes); a_path = a.name
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as b:
            b.write(png_bytes); b_path = b.name
        try:
            comp = VisionComparator(provider="mock")
            score = comp.compare(a_path, b_path)
            self.assertGreater(score.overall, 0.0)
            self.assertEqual(score.provider, "mock")
        finally:
            os.unlink(a_path); os.unlink(b_path)


if __name__ == "__main__":
    unittest.main()
