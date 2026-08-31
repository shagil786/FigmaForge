#!/usr/bin/env python3
"""Tests for core.vision_iterator."""

import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vision_iterator import (
    VisionIterationPlan, VisionIterationResult, VisionIterationFinal,
    apply_template_fixes, _extract_html_from_llm_response,
)


class TestTemplateFixes(unittest.TestCase):
    def test_gradient_alpha(self):
        html = '<div style="background:linear-gradient(180deg,rgba(11,29,38,0.0) 0%,rgba(11,29,38,1.0) 100%)"></div>'
        feedback = {"issues": ["Gradient is darker"], "suggestions": []}
        fixed, fixes = apply_template_fixes(html, feedback)
        self.assertTrue(any("gradient" in f.lower() for f in fixes))
        self.assertNotIn("rgba(11,29,38,1.0)", fixed)

    def test_object_fit(self):
        html = '<img style="object-fit:fill">'
        feedback = {"issues": ["Image stretched"], "suggestions": ["Use cover"]}
        fixed, fixes = apply_template_fixes(html, feedback)
        self.assertIn("object-fit:cover", fixed)

    def test_no_fixes(self):
        html = '<div>hello</div>'
        feedback = {"issues": ["Text is fine"], "suggestions": []}
        fixed, fixes = apply_template_fixes(html, feedback)
        self.assertEqual(fixed, html)


class TestExtractHtml(unittest.TestCase):
    def test_raw_html(self):
        resp = "<!DOCTYPE html><html><body>test</body></html>"
        self.assertIn("<!DOCTYPE", _extract_html_from_llm_response(resp))

    def test_markdown_fenced(self):
        resp = "Here is the fix:\n```html\n<!DOCTYPE html><html><body>fixed</body></html>\n```\nDone."
        self.assertIn("<!DOCTYPE", _extract_html_from_llm_response(resp))


class TestVisionIterationPlan(unittest.TestCase):
    def test_defaults(self):
        p = VisionIterationPlan(file_path="t.json", baseline_path="b.png")
        self.assertEqual(p.target_score, 0.90)
        self.assertEqual(p.max_iterations, 10)


class TestVisionIterationFinal(unittest.TestCase):
    def test_creation(self):
        f = VisionIterationFinal([], 0.88, 3, True, False, "", "")
        self.assertTrue(f.target_reached)
        self.assertAlmostEqual(f.best_score, 0.88)


if __name__ == "__main__":
    unittest.main()
