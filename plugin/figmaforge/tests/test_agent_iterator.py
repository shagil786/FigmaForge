#!/usr/bin/env python3
"""Tests for the agent iteration engine."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.agent_iterator import (
    AgentIterator,
    IterationPlan,
    IterationResult,
    _build_iteration_prompt,
    _extract_code_from_llm_response,
)


class TestExtractCode(unittest.TestCase):
    """Test LLM response code extraction."""

    def test_html_code_block(self):
        response = "Here's the fixed code:\n```html\n<div class=\"hero\">\n  <h1>Title</h1>\n</div>\n```\nLet me know if..."
        result = _extract_code_from_llm_response(response)
        self.assertIn("<div", result)
        self.assertIn("hero", result)

    def test_tsx_code_block(self):
        response = "```tsx\nexport function App() {\n  return <div>Hello</div>\n}\n```"
        result = _extract_code_from_llm_response(response)
        self.assertIn("export function App", result)

    def test_raw_html(self):
        response = "<!DOCTYPE html><html><body>Test</body></html>"
        result = _extract_code_from_llm_response(response)
        self.assertIn("<!DOCTYPE", result)

    def test_empty_response(self):
        result = _extract_code_from_llm_response("")
        self.assertEqual(result, "")

    def test_no_code_block(self):
        result = _extract_code_from_llm_response("I couldn't generate code because...")
        self.assertIn("I couldn", result)

    def test_multiple_code_blocks(self):
        response = "First:\n```html\n<div>A</div>\n```\nSecond:\n```html\n<div>B</div>\n```"
        result = _extract_code_from_llm_response(response)
        self.assertIn("<div>A</div>", result)

    def test_generic_code_block(self):
        response = "```\n<div>generic</div>\n```"
        result = _extract_code_from_llm_response(response)
        self.assertIn("<div>generic</div>", result)


class TestIterationPlan(unittest.TestCase):
    """Test IterationPlan defaults."""

    def test_defaults(self):
        plan = IterationPlan(
            file_path="test.json",
            backend="html_css",
            baseline_path="baseline.png",
        )
        self.assertEqual(plan.max_iterations, 10)
        self.assertEqual(plan.target_ssim, 0.95)
        self.assertEqual(plan.viewport, 1440)
        self.assertTrue(plan.stop_on_plateau)
        self.assertEqual(plan.plateau_threshold, 0.001)

    def test_custom_values(self):
        plan = IterationPlan(
            file_path="test.json",
            backend="react_tailwind",
            baseline_path="baseline.png",
            max_iterations=5,
            target_ssim=0.90,
            viewport=1280,
        )
        self.assertEqual(plan.max_iterations, 5)
        self.assertEqual(plan.target_ssim, 0.90)
        self.assertEqual(plan.viewport, 1280)


class TestIterationResult(unittest.TestCase):
    """Test IterationResult data class."""

    def test_basic_result(self):
        result = IterationResult(
            iteration=1,
            ssim_score=0.85,
            verdict="changed",
            html_path="/tmp/test.html",
            screenshot_path="/tmp/test.png",
        )
        self.assertEqual(result.iteration, 1)
        self.assertEqual(result.ssim_score, 0.85)
        self.assertEqual(result.mismatch_count, 0)
        self.assertEqual(result.fidelity_losses, [])


class TestBuildIterationPrompt(unittest.TestCase):
    """Test prompt construction for LLM feedback."""

    def test_basic_prompt(self):
        spec = {
            "colors": [{"hex": "#ffffff", "count": 10}],
            "typography": [{"font_family": "Arial", "font_size": 16, "font_weight": 400, "count": 5}],
        }
        prompt = _build_iteration_prompt(
            spec=spec,
            current_html="<div>test</div>",
            baseline_screenshot=b"\x89PNG\r\n\x1a\n",
            generated_screenshot=b"\x89PNG\r\n\x1a\n",
            diff_screenshot=None,
            ssim_score=0.75,
            mismatch_regions=[],
            iteration=1,
            max_iterations=10,
            backend="html_css",
        )
        self.assertIn("75.0%", prompt)
        self.assertIn("ITERATION 1/10", prompt)
        self.assertIn("<div>test</div>", prompt)

    def test_prompt_with_mismatches(self):
        spec = {"colors": [], "typography": []}
        mismatches = [
            {"kind": "color", "x": 100, "y": 200, "width": 50, "height": 30, "description": "Wrong blue"},
            {"kind": "layout", "x": 0, "y": 0, "width": 200, "height": 100, "description": "Misaligned"},
        ]
        prompt = _build_iteration_prompt(
            spec=spec,
            current_html="<div>test</div>",
            baseline_screenshot=b"\x89PNG\r\n\x1a\n",
            generated_screenshot=b"\x89PNG\r\n\x1a\n",
            diff_screenshot=None,
            ssim_score=0.60,
            mismatch_regions=mismatches,
            iteration=3,
            max_iterations=10,
            backend="html_css",
        )
        self.assertIn("60.0%", prompt)
        self.assertIn("Color mismatches: 1", prompt)
        self.assertIn("Layout mismatches: 1", prompt)
        self.assertIn("Wrong blue", prompt)


class TestAgentIteratorImport(unittest.TestCase):
    """Verify AgentIterator can be instantiated (no LLM call)."""

    def test_instantiation(self):
        plan = IterationPlan(
            file_path="test.json",
            backend="html_css",
            baseline_path="baseline.png",
            max_iterations=1,
        )
        # Don't actually run — just verify the class loads
        iterator = AgentIterator(plan)
        self.assertEqual(iterator.plan.max_iterations, 1)
        self.assertEqual(iterator.results, [])


if __name__ == "__main__":
    unittest.main()
