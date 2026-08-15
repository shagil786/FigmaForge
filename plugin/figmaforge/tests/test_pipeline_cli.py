"""
Pipeline CLI tests (Part 15, Task 1).

Exercises ``scripts/pipeline.py`` as a real subprocess (the way the TS
runtime will invoke it): the ``ingest`` subcommand (local file, token
requirement, ``--out``) and the ``generate`` subcommand (all six backends,
deterministic manifest, unknown backend, missing file, node coverage).

Run:  python3 -m unittest tests.test_pipeline_cli -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

SCRIPT = plugin_root / "scripts" / "pipeline.py"
FIXTURE = plugin_root / "fixtures" / "figma" / "layout_desktop.json"

ALL_BACKENDS = (
    "html_css",
    "react_tailwind",
    "vue",
    "svelte",
    "swiftui",
    "flutter",
)


def _run(args, env=None):
    """Run the pipeline CLI in a subprocess from the plugin root."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(plugin_root),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def _no_token_env():
    """Environment with FIGMA_TOKEN removed (may not be set at all)."""
    return {k: v for k, v in os.environ.items() if k != "FIGMA_TOKEN"}


class TestIngest(unittest.TestCase):
    def test_ingest_local_file_deterministic(self):
        """ingest --file prints one JSON line (file_key, name, pages) that
        FigmaFile.from_dict accepts; two runs are byte-identical."""
        first = _run(["ingest", "--file", str(FIXTURE)])
        second = _run(["ingest", "--file", str(FIXTURE)])
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)

        lines = [ln for ln in first.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["file_key"], "layout_desktop")
        self.assertEqual(payload["name"], "FigmaForge Layout — Desktop 1440")
        self.assertTrue(isinstance(payload["pages"], list))
        self.assertTrue(payload["pages"])
        self.assertEqual(payload["pages"][0]["name"], "Desktop")

        # Round-trips through the same loader the pipeline uses.
        from core.figma_types import FigmaFile

        figma_file = FigmaFile.from_dict(payload["file_key"], payload)
        self.assertEqual(figma_file.name, payload["name"])
        self.assertTrue(figma_file.pages())

    def test_ingest_out_file(self):
        """ingest --out writes the same normalized payload to disk."""
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "file.json"
            proc = _run(["ingest", "--file", str(FIXTURE), "--out", str(out_path)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out_path.exists())
            written = json.loads(out_path.read_text(encoding="utf-8"))
            stdout_payload = json.loads(
                [ln for ln in proc.stdout.splitlines() if ln.strip()][0]
            )
            self.assertEqual(written["file_key"], stdout_payload["file_key"])
            self.assertEqual(written["name"], stdout_payload["name"])
            self.assertEqual(written["pages"], stdout_payload["pages"])

    def test_ingest_missing_token_error(self):
        """ingest --file-key without FIGMA_TOKEN exits 3 with a clear error."""
        proc = _run(
            ["ingest", "--file-key", "abc123"],
            env=_no_token_env(),
        )
        self.assertEqual(proc.returncode, 3)
        self.assertIn("FIGMA_TOKEN", proc.stderr)

    def test_ingest_missing_file_error(self):
        """ingest --file with an unreadable path exits 4."""
        proc = _run(["ingest", "--file", str(plugin_root / "nope.json")])
        self.assertEqual(proc.returncode, 4)
        self.assertTrue(proc.stderr.strip())


class TestFrontHalfStages(unittest.TestCase):
    """normalize / resolve / layout subcommands + staged generate (Part 16)."""

    def _normalize_to(self, tmp: str) -> str:
        """Run normalize with --out; return the IR JSON path."""
        out_path = Path(tmp) / "ir.json"
        proc = _run(["normalize", "--file", str(FIXTURE), "--out", str(out_path)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return str(out_path)

    def test_normalize_deterministic_and_valid(self):
        """normalize prints one JSON line IRDocument.from_dict accepts; runs
        are byte-identical."""
        from core.ir_types import IRDocument

        first = _run(["normalize", "--file", str(FIXTURE)])
        second = _run(["normalize", "--file", str(FIXTURE)])
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)

        lines = [ln for ln in first.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["file_key"], "layout_desktop")
        self.assertIn("root", payload)
        doc = IRDocument.from_dict(payload)
        self.assertTrue(doc.all_nodes())

    def test_normalize_invalid_file(self):
        """normalize with a malformed JSON file exits 4."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            proc = _run(["normalize", "--file", str(bad)])
            self.assertEqual(proc.returncode, 4)
            self.assertTrue(proc.stderr.strip())

    def test_resolve_consumes_normalize_output(self):
        """resolve accepts normalize output and emits a report-shaped JSON;
        two runs byte-identical."""
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = self._normalize_to(tmp)
            first = _run(["resolve", "--file", ir_path])
            second = _run(["resolve", "--file", ir_path])
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first.stdout, second.stdout)

            payload = json.loads(first.stdout)
            self.assertIn("counts", payload)
            self.assertIn("resolved", payload)
            self.assertIn("tokens", payload)

    def test_layout_consumes_normalize_output(self):
        """layout accepts normalize output and emits a plan-shaped JSON;
        two runs byte-identical."""
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = self._normalize_to(tmp)
            first = _run(["layout", "--file", ir_path])
            second = _run(["layout", "--file", ir_path])
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first.stdout, second.stdout)

            payload = json.loads(first.stdout)
            self.assertIn("screens", payload)
            self.assertTrue(payload["screens"])

    def test_layout_invalid_ir(self):
        """layout with a non-IR JSON exits 4 (loader failure surfaced)."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "not_ir.json"
            bad.write_text(json.dumps({"foo": 1}), encoding="utf-8")
            proc = _run(["layout", "--file", str(bad)])
            self.assertEqual(proc.returncode, 4)
            self.assertTrue(proc.stderr.strip())

    def test_generate_staged_equals_file_mode(self):
        """generate --ir/--layout/--resolution is byte-identical to --file."""
        for backend in ("react_tailwind", "flutter"):
            with self.subTest(backend=backend):
                with tempfile.TemporaryDirectory() as tmp:
                    ir_path = self._normalize_to(tmp)
                    layout_path = Path(tmp) / "layout.json"
                    resolution_path = Path(tmp) / "resolution.json"
                    proc = _run(["layout", "--file", ir_path, "--out", str(layout_path)])
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    proc = _run(["resolve", "--file", ir_path, "--out", str(resolution_path)])
                    self.assertEqual(proc.returncode, 0, proc.stderr)

                    out_file = Path(tmp) / "file-mode"
                    out_staged = Path(tmp) / "staged"
                    file_run = _run(
                        ["generate", "--file", str(FIXTURE), "--backend", backend,
                         "--out-dir", str(out_file)]
                    )
                    staged_run = _run(
                        ["generate", "--ir", ir_path, "--layout", str(layout_path),
                         "--resolution", str(resolution_path), "--backend", backend,
                         "--out-dir", str(out_staged)]
                    )
                    self.assertEqual(file_run.returncode, 0, file_run.stderr)
                    self.assertEqual(staged_run.returncode, 0, staged_run.stderr)
                    self.assertEqual(file_run.stdout, staged_run.stdout)

                    manifest = json.loads(file_run.stdout)
                    for entry in manifest["files"]:
                        p1 = (out_file / backend / entry["path"]).read_bytes()
                        p2 = (out_staged / backend / entry["path"]).read_bytes()
                        self.assertEqual(p1, p2, entry["path"])

    def test_generate_staged_requires_both(self):
        """--ir without --layout, or --file with --ir, exits 2."""
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = self._normalize_to(tmp)
            proc = _run(["generate", "--ir", ir_path, "--backend", "html_css"])
            self.assertEqual(proc.returncode, 2)
            self.assertIn("--layout", proc.stderr)

            proc = _run(
                ["generate", "--file", str(FIXTURE), "--ir", ir_path,
                 "--backend", "html_css"]
            )
            self.assertEqual(proc.returncode, 2)


class TestGenerate(unittest.TestCase):
    def test_generate_all_six_backends(self):
        """Every registry backend generates >=1 file with a full manifest."""
        from backends.registry import get_registry

        names = get_registry().names()
        self.assertEqual(names, sorted(ALL_BACKENDS))
        for backend in names:
            with self.subTest(backend=backend):
                with tempfile.TemporaryDirectory() as tmp:
                    proc = _run(
                        ["generate", "--file", str(FIXTURE),
                         "--backend", backend, "--out-dir", tmp]
                    )
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
                    self.assertEqual(len(lines), 1)
                    manifest = json.loads(lines[0])
                    self.assertEqual(manifest["backend"], backend)
                    self.assertIn("files", manifest)
                    self.assertIn("fidelity_losses", manifest)
                    self.assertIn("metadata", manifest)
                    self.assertTrue(manifest["files"])

                    out_dir = Path(tmp) / backend
                    self.assertTrue(out_dir.exists())
                    for entry in manifest["files"]:
                        self.assertIn("path", entry)
                        self.assertIn("language", entry)
                        self.assertIn("node_ids", entry)
                        target = out_dir / entry["path"]
                        self.assertTrue(target.exists(), target)
                        self.assertEqual(
                            len(target.read_bytes()),
                            entry["size_bytes"],
                        )

    def test_generate_manifest_deterministic(self):
        """Two generate runs produce identical manifests and file bytes."""
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            first = _run(
                ["generate", "--file", str(FIXTURE),
                 "--backend", "vue", "--out-dir", tmp1]
            )
            second = _run(
                ["generate", "--file", str(FIXTURE),
                 "--backend", "vue", "--out-dir", tmp2]
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first.stdout, second.stdout)

            manifest = json.loads(first.stdout)
            for entry in manifest["files"]:
                p1 = (Path(tmp1) / "vue" / entry["path"]).read_bytes()
                p2 = (Path(tmp2) / "vue" / entry["path"]).read_bytes()
                self.assertEqual(p1, p2)

    def test_generate_unknown_backend(self):
        """Unknown backend exits 2 and lists the valid names on stderr."""
        proc = _run(
            ["generate", "--file", str(FIXTURE), "--backend", "nope"],
        )
        self.assertEqual(proc.returncode, 2)
        for name in ALL_BACKENDS:
            self.assertIn(name, proc.stderr)

    def test_generate_missing_file(self):
        """Unreadable input file exits 4 with a clear message."""
        proc = _run(
            ["generate", "--file", str(plugin_root / "missing.json"),
             "--backend", "html_css"],
        )
        self.assertEqual(proc.returncode, 4)
        self.assertIn("missing.json", proc.stderr)

    def test_generate_invalid_json(self):
        """Malformed input JSON exits 4."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            proc = _run(
                ["generate", "--file", str(bad), "--backend", "html_css"],
            )
            self.assertEqual(proc.returncode, 4)
            self.assertTrue(proc.stderr.strip())

    def test_generate_node_coverage(self):
        """react_tailwind's screen file node_ids cover the plan's screen."""
        from core.figma_types import FigmaFile
        from core.ir_builder import IRBuilder
        from core.layout_analyzer import LayoutAnalyzer
        from core.library_types import LibraryLoader

        doc = IRBuilder().build(FigmaFile.from_dict("layout_desktop", json.loads(FIXTURE.read_text(encoding="utf-8"))))
        plan = LayoutAnalyzer().analyze(doc, library=LibraryLoader().load())

        with tempfile.TemporaryDirectory() as tmp:
            proc = _run(
                ["generate", "--file", str(FIXTURE),
                 "--backend", "react_tailwind", "--out-dir", tmp]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            manifest = json.loads(proc.stdout)

            covered = set()
            for entry in manifest["files"]:
                covered.update(entry["node_ids"])
            expected = {n.node_id for n in plan.screens[0].walk() if n.node_id}
            self.assertTrue(expected)
            self.assertTrue(expected <= covered)





if __name__ == "__main__":
    unittest.main()
