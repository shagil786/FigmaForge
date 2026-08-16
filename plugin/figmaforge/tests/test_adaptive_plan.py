#!/usr/bin/env python3
"""
Tests for the adaptive preflight planning CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


plugin_root = Path(__file__).resolve().parent.parent
SCRIPT = plugin_root / "scripts" / "adaptive_plan.py"


def run_plan(request: str, root: Path | None = None, installed_capabilities: list[str] | None = None):
    """Run the adaptive plan CLI as a subprocess."""
    args = [
        sys.executable,
        str(SCRIPT),
        "--root",
        str(root or plugin_root),
        "--request",
        request,
    ]
    for capability in installed_capabilities or []:
        args.extend(["--installed-capability", capability])
    return subprocess.run(
        args,
        cwd=str(plugin_root),
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestAdaptivePlan(unittest.TestCase):
    def test_plan_is_deterministic_for_classified_repo(self):
        first = run_plan("Convert this Figma design into React")
        second = run_plan("Convert this Figma design into React")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)

        payload = json.loads(first.stdout)
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["route"]["phases"])

    def test_installed_capability_reaches_router(self):
        without_cap = run_plan("Research the competitor landscape")
        with_cap = run_plan(
            "Research the competitor landscape",
            installed_capabilities=["deep-research"],
        )
        self.assertEqual(without_cap.returncode, 0, without_cap.stderr)
        self.assertEqual(with_cap.returncode, 0, with_cap.stderr)

        without_payload = json.loads(without_cap.stdout)
        with_payload = json.loads(with_cap.stdout)
        without_roles = [role["id"] for role in without_payload["route"]["roles"]]
        with_roles = [role["id"] for role in with_payload["route"]["roles"]]
        self.assertNotIn("discovery-researcher", without_roles)
        self.assertIn("discovery-researcher", with_roles)
        reasons = with_payload["route"]["roles"][0]["reasons"]
        self.assertIn("Installed capability ref: deep-research", reasons)

    def test_unclassified_repo_returns_valid_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_plan("Inspect this repository", root=Path(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["detection"]["status"], "unclassified")

    def test_whitespace_request_returns_structured_error(self):
        result = run_plan("   ")
        self.assertEqual(result.returncode, 2)
        self.assertTrue(json.loads(result.stderr)["error"])

    def test_missing_root_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_plan("Inspect", root=Path(tmp) / "missing")
        self.assertEqual(result.returncode, 2)
        self.assertTrue(json.loads(result.stderr)["error"])


if __name__ == "__main__":
    unittest.main()
