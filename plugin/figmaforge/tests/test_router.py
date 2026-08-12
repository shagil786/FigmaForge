#!/usr/bin/env python3
"""
Tests for Router scoring logic.

Verify trigger extraction, phase-match scoring, signal-match scoring,
penalty logic, and execution mode determination.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.catalog import Catalog
from core.detector import RepositoryDetector
from core.router import Router


def _make_router(detection_result: dict) -> Router:
    """Create a router with a mocked detector returning the given result."""
    catalog = Catalog()
    detector = MagicMock(spec=RepositoryDetector)
    detector.detect.return_value = detection_result
    return Router(catalog, detector)


class TestTriggerExtraction(unittest.TestCase):
    def test_extracts_known_triggers(self):
        r = _make_router({"status": "classified", "languages": []})
        triggers = r._extract_triggers("I need to fix a bug in the API")
        self.assertIn("fix", triggers)
        self.assertIn("bug", triggers)
        self.assertIn("api", triggers)

    def test_no_duplicates(self):
        r = _make_router({"status": "classified", "languages": []})
        triggers = r._extract_triggers("test the test suite")
        self.assertEqual(triggers.count("test"), 1)

    def test_empty_request(self):
        r = _make_router({"status": "classified", "languages": []})
        triggers = r._extract_triggers("hello world")
        self.assertEqual(triggers, [])


class TestPhaseMatchScoring(unittest.TestCase):
    def test_design_trigger_maps_to_design_phase(self):
        r = _make_router({"status": "classified", "languages": [], "request": ""})
        triggers = r._extract_triggers("design the UI")
        # Verify the trigger-to-phase mapping exists
        self.assertIn("design", r._TRIGGER_TO_PHASES)
        self.assertIn("design", r._TRIGGER_TO_PHASES["design"])

    def test_test_trigger_maps_to_verify_phase(self):
        r = _make_router({"status": "classified", "languages": [], "request": ""})
        self.assertIn("verify", r._TRIGGER_TO_PHASES["test"])


class TestSignalMatchScoring(unittest.TestCase):
    def test_python_maps_to_application_and_data(self):
        r = _make_router({"status": "classified", "languages": [], "request": ""})
        self.assertIn("application", r._LANGUAGE_TO_DOMAIN["python"])
        self.assertIn("data", r._LANGUAGE_TO_DOMAIN["python"])

    def test_javascript_maps_to_experience_and_application(self):
        r = _make_router({"status": "classified", "languages": [], "request": ""})
        self.assertIn("experience", r._LANGUAGE_TO_DOMAIN["javascript"])


class TestPenaltyLogic(unittest.TestCase):
    def test_no_double_penalty(self):
        """A role in application domain with unclassified+no-languages gets -5, not -8."""
        detection = {
            "status": "unclassified",
            "languages": [],
            "test_commands": [],
            "request": "",
            "lsp_candidates": [],
        }
        r = _make_router(detection)
        roles = [{"id": "backend-eng", "domain": "application", "triggers": [],
                  "phases": [], "deliverables": [], "capability_refs": []}]
        scored = r._score_roles(roles, [], detection)
        # The role gets -5 (stack domain penalty), not -8 (both penalties)
        for role in scored:
            if role["id"] == "backend-eng":
                self.assertGreaterEqual(role["score"], -5)


class TestExecutionMode(unittest.TestCase):
    def test_unclassified_forces_isolated_scout(self):
        r = _make_router({"status": "classified", "languages": []})
        mode = r._determine_execution_mode([], "unclassified")
        self.assertEqual(mode, "isolated_scout")

    def test_direct_mode_for_simple_roles(self):
        r = _make_router({"status": "classified", "languages": []})
        roles = [{"id": "frontend-dev", "phases": ["implement"]}]
        mode = r._determine_execution_mode(roles, "classified")
        self.assertEqual(mode, "direct")

    def test_planner_mode_for_plan_phase(self):
        r = _make_router({"status": "classified", "languages": []})
        roles = [{"id": "architect", "phases": ["plan", "design"]}]
        mode = r._determine_execution_mode(roles, "classified")
        self.assertEqual(mode, "isolated_planner")


class TestApprovalGates(unittest.TestCase):
    def test_deploy_trigger_adds_mutation_gate(self):
        r = _make_router({"status": "classified", "languages": [], "lsp_candidates": []})
        roles = [{"id": "release-eng", "triggers": ["deploy"], "phases": []}]
        gates = r._determine_approval_gates(roles, "direct", "classified", {"lsp_candidates": []})
        self.assertIn("external_mutation", gates)

    def test_unclassified_adds_stack_gate(self):
        r = _make_router({"status": "classified", "languages": [], "lsp_candidates": []})
        gates = r._determine_approval_gates([], "direct", "unclassified", {"lsp_candidates": []})
        self.assertIn("stack_selection", gates)


if __name__ == "__main__":
    unittest.main()
