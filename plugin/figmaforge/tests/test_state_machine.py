#!/usr/bin/env python3
"""
Tests for the lifecycle state machine (Part 3).

Verify forward-only transitions, phase skipping prevention, and state
persistence.
"""
import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.state import LifecycleState, StateMachine

PHASES = [
    "intake", "discover", "define", "design", "plan",
    "implement", "verify", "release", "operate", "learn",
]


class TestStateMachine(unittest.TestCase):
    def _fresh_machine(self) -> StateMachine:
        m = StateMachine(run_id="test-run")
        # Mock _write_state to avoid filesystem dependency in tests
        m._write_state = lambda: None
        m.initialize(
            request="test request",
            selected_roles=["frontend-dev"],
            selected_capabilities=[],
        )
        return m

    def test_starts_at_intake(self):
        m = self._fresh_machine()
        self.assertEqual(m.state.phase, "intake")

    def test_forward_transition_succeeds(self):
        m = self._fresh_machine()
        m.advance_to("discover")
        self.assertEqual(m.state.phase, "discover")

    def test_skip_phase_rejected(self):
        """intake -> define should fail (skips discover)."""
        m = self._fresh_machine()
        with self.assertRaises(ValueError):
            m.advance_to("define")

    def test_backward_transition_rejected(self):
        m = self._fresh_machine()
        m.advance_to("discover")
        with self.assertRaises(ValueError):
            m.advance_to("intake")

    def test_full_lifecycle_walk(self):
        """Walk through all 10 phases in order."""
        m = self._fresh_machine()
        for i in range(1, len(PHASES)):
            m.advance_to(PHASES[i])
        self.assertEqual(m.state.phase, "learn")

    def test_invalid_phase_rejected(self):
        m = self._fresh_machine()
        with self.assertRaises(ValueError):
            m.advance_to("nonexistent")

    def test_status_defaults_to_active(self):
        m = self._fresh_machine()
        self.assertEqual(m.state.status, "active")

    def test_advance_without_init_raises(self):
        m = StateMachine(run_id="no-init")
        m._write_state = lambda: None
        with self.assertRaises(ValueError):
            m.advance_to("discover")


class TestLifecycleStateDataclass(unittest.TestCase):
    def test_to_dict(self):
        state = LifecycleState(
            run_id="r1",
            request="test",
            phase="intake",
            status="active",
            risk="low",
            selected_roles=[],
            selected_capabilities=[],
            decisions=[],
            artifacts={},
            evidence=[],
            validations=[],
            approvals=[],
            blockers=[],
        )
        d = state.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["run_id"], "r1")
        self.assertEqual(d["phase"], "intake")
        self.assertEqual(d["status"], "active")


if __name__ == "__main__":
    unittest.main()
