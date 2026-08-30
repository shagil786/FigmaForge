#!/usr/bin/env python3
"""Tests for the agent iteration tools."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.agent_iterator import (
    DiffFeedback,
    IterationTracker,
    IterationState,
    analyze_diff,
    get_iteration_guidance,
)


class TestDiffFeedback(unittest.TestCase):
    """Test DiffFeedback data class."""

    def test_basic_feedback(self):
        feedback = DiffFeedback(
            ssim_score=0.85,
            verdict="changed",
            mismatch_count=5,
            mismatch_regions=[],
            color_mismatches=2,
            layout_mismatches=3,
            missing_elements=0,
            extra_elements=0,
            diff_summary="Score: 85%",
        )
        self.assertEqual(feedback.ssim_score, 0.85)
        self.assertEqual(feedback.mismatch_count, 5)
        self.assertEqual(feedback.color_mismatches, 2)
        self.assertEqual(feedback.layout_mismatches, 3)


class TestGetIterationGuidance(unittest.TestCase):
    """Test guidance generation for agents."""

    def test_target_reached(self):
        feedback = DiffFeedback(
            ssim_score=0.96,
            verdict="identical",
            mismatch_count=0,
            mismatch_regions=[],
            color_mismatches=0,
            layout_mismatches=0,
            missing_elements=0,
            extra_elements=0,
            diff_summary="Perfect",
        )
        guidance = get_iteration_guidance(feedback, iteration=3, target_ssim=0.95)
        self.assertIn("Target reached", guidance)
        self.assertIn("Stop iterating", guidance)

    def test_color_issues(self):
        feedback = DiffFeedback(
            ssim_score=0.70,
            verdict="changed",
            mismatch_count=10,
            mismatch_regions=[],
            color_mismatches=5,
            layout_mismatches=5,
            missing_elements=0,
            extra_elements=0,
            diff_summary="Needs work",
        )
        guidance = get_iteration_guidance(feedback, iteration=1, target_ssim=0.95)
        self.assertIn("Color Issues (5 regions)", guidance)
        self.assertIn("Layout Issues (5 regions)", guidance)
        self.assertIn("hex values", guidance)

    def test_missing_elements(self):
        feedback = DiffFeedback(
            ssim_score=0.60,
            verdict="changed",
            mismatch_count=8,
            mismatch_regions=[],
            color_mismatches=0,
            layout_mismatches=0,
            missing_elements=8,
            extra_elements=0,
            diff_summary="Many missing",
        )
        guidance = get_iteration_guidance(feedback, iteration=2, target_ssim=0.95)
        self.assertIn("Missing Elements (8 regions)", guidance)
        self.assertIn("HTML structure", guidance)

    def test_max_iterations(self):
        feedback = DiffFeedback(
            ssim_score=0.80,
            verdict="changed",
            mismatch_count=5,
            mismatch_regions=[],
            color_mismatches=0,
            layout_mismatches=0,
            missing_elements=0,
            extra_elements=0,
            diff_summary="Ok",
        )
        guidance = get_iteration_guidance(feedback, iteration=10, max_iterations=10, target_ssim=0.95)
        self.assertIn("Max iterations reached", guidance)


class TestIterationTracker(unittest.TestCase):
    """Test iteration state tracking."""

    def test_recording(self):
        tracker = IterationTracker(target_ssim=0.95)
        
        feedback1 = DiffFeedback(
            ssim_score=0.70, verdict="changed", mismatch_count=10,
            mismatch_regions=[], color_mismatches=0, layout_mismatches=0,
            missing_elements=0, extra_elements=0, diff_summary="",
        )
        status1 = tracker.record_iteration(feedback1)
        self.assertEqual(status1["iteration"], 1)
        self.assertEqual(status1["current_score"], 0.70)
        self.assertFalse(status1["target_reached"])
        
        feedback2 = DiffFeedback(
            ssim_score=0.85, verdict="changed", mismatch_count=5,
            mismatch_regions=[], color_mismatches=0, layout_mismatches=0,
            missing_elements=0, extra_elements=0, diff_summary="",
        )
        status2 = tracker.record_iteration(feedback2)
        self.assertEqual(status2["iteration"], 2)
        self.assertEqual(status2["best_score"], 0.85)

    def test_plateau_detection(self):
        tracker = IterationTracker(target_ssim=0.95, plateau_threshold=3)
        
        # Initial jump, then 3+ iterations with no improvement
        for score in [0.50, 0.70, 0.7001, 0.7002, 0.7003]:
            feedback = DiffFeedback(
                ssim_score=score, verdict="changed", mismatch_count=5,
                mismatch_regions=[], color_mismatches=0, layout_mismatches=0,
                missing_elements=0, extra_elements=0, diff_summary="",
            )
            status = tracker.record_iteration(feedback)
        
        self.assertTrue(status["plateau_detected"])
        self.assertTrue(status["should_stop"])

    def test_target_reached(self):
        tracker = IterationTracker(target_ssim=0.95)
        
        feedback = DiffFeedback(
            ssim_score=0.96, verdict="identical", mismatch_count=0,
            mismatch_regions=[], color_mismatches=0, layout_mismatches=0,
            missing_elements=0, extra_elements=0, diff_summary="",
        )
        status = tracker.record_iteration(feedback)
        self.assertTrue(status["target_reached"])
        self.assertTrue(status["should_stop"])

    def test_summary(self):
        tracker = IterationTracker(target_ssim=0.95)
        
        for score in [0.60, 0.75, 0.90]:
            feedback = DiffFeedback(
                ssim_score=score, verdict="changed", mismatch_count=5,
                mismatch_regions=[], color_mismatches=0, layout_mismatches=0,
                missing_elements=0, extra_elements=0, diff_summary="",
            )
            tracker.record_iteration(feedback)
        
        summary = tracker.get_summary()
        self.assertIn("3 iterations", summary)
        self.assertIn("60.0%", summary)
        self.assertIn("90.0%", summary)


class TestAnalyzeDiffImport(unittest.TestCase):
    """Verify analyze_diff can be imported (no actual rendering)."""

    def test_import(self):
        from core.agent_iterator import analyze_diff
        self.assertTrue(callable(analyze_diff))


if __name__ == "__main__":
    unittest.main()
