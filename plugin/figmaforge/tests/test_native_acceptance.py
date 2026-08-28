#!/usr/bin/env python3
"""Acceptance tests for generated SwiftUI and Flutter artifacts."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from scripts import native_acceptance


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PLUGIN_ROOT / "scripts" / "native_acceptance.py"
FIXTURE = PLUGIN_ROOT / "fixtures" / "figma" / "layout_desktop.json"


class TestNativeAcceptance(unittest.TestCase):
    def test_native_acceptance_validates_both_backend_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--fixture",
                    str(FIXTURE),
                    "--out-dir",
                    str(Path(tmp) / "generated"),
                ],
                cwd=str(PLUGIN_ROOT),
                capture_output=True,
                text=True,
                timeout=120,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["backends"]["swiftui"]["manifest_backend"], "swiftui")
        self.assertEqual(payload["backends"]["flutter"]["manifest_backend"], "flutter")
        self.assertGreater(payload["backends"]["swiftui"]["generated_files"], 0)
        self.assertGreater(payload["backends"]["flutter"]["generated_files"], 0)

        swift_status = payload["backends"]["swiftui"]["validation"]["status"]
        self.assertIn(swift_status, {"passed", "skipped"})
        if shutil.which("swiftc") is not None:
            self.assertEqual(swift_status, "passed")
        flutter_status = payload["backends"]["flutter"]["validation"]["status"]
        self.assertIn(flutter_status, {"passed", "skipped"})

    def test_native_acceptance_rejects_missing_fixture_with_structured_error(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--fixture", str(PLUGIN_ROOT / "missing.json")],
            cwd=str(PLUGIN_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(json.loads(result.stderr)["error"])

    def test_native_acceptance_resolves_repo_relative_fixture_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--fixture",
                    "plugin/figmaforge/fixtures/figma/layout_desktop.json",
                    "--out-dir",
                    str(Path(tmp) / "generated"),
                ],
                cwd=str(PLUGIN_ROOT.parent.parent),
                capture_output=True,
                text=True,
                timeout=120,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["schema_version"], 1)

    def test_flutter_docker_validation_builds_analyzable_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "generated"
            flutter_dir = out_dir / "flutter"
            flutter_dir.mkdir(parents=True)
            (flutter_dir / "desktop_screen.dart").write_text(
                "import 'package:flutter/material.dart';\n"
                "class DesktopScreen extends StatelessWidget {\n"
                "  const DesktopScreen({super.key});\n"
                "  @override Widget build(BuildContext context) => const Scaffold();\n"
                "}\n",
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(
                ["docker"], 0, stdout="No issues found!\n", stderr="",
            )
            with mock.patch.object(
                native_acceptance.subprocess, "run", return_value=completed,
            ) as run:
                report = native_acceptance._validate_flutter_docker(
                    out_dir, "ghcr.io/cirruslabs/flutter:stable", "/usr/local/bin/docker",
                )

            self.assertEqual(report["status"], "passed")
            self.assertTrue((out_dir / "flutter_project" / "lib" / "main.dart").is_file())
            command = run.call_args.args[0]
            self.assertEqual(command[0:2], ["/usr/local/bin/docker", "run"])
            self.assertIn("sh", command)
            self.assertIn("flutter pub get", command[-1])
            self.assertIn("flutter analyze", command[-1])
            self.assertIn("flutter test", command[-1])

    def test_swiftui_sdk_validation_typechecks_generated_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "generated"
            swift_dir = out_dir / "swiftui"
            swift_dir.mkdir(parents=True)
            (swift_dir / "DesktopScreen.swift").write_text(
                "import SwiftUI\nstruct DesktopScreenView: View {\n"
                " var body: some View { Text(\"ok\") }\n}\n",
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(
                ["swiftc"], 0, stdout="", stderr="",
            )
            with mock.patch.object(
                native_acceptance.subprocess, "run", return_value=completed,
            ) as run:
                report = native_acceptance._validate_swiftui_sdk(
                    out_dir, "/usr/bin/swiftc", "/Applications/Xcode.app/SDKs/iPhoneSimulator.sdk",
                )

            self.assertEqual(report["status"], "passed")
            command = run.call_args.args[0]
            self.assertEqual(command[0:4], ["/usr/bin/swiftc", "-typecheck", "-sdk", "/Applications/Xcode.app/SDKs/iPhoneSimulator.sdk"])
            self.assertIn("-target", command)


if __name__ == "__main__":
    unittest.main()
