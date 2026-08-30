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
    """Environment with both environment and OAuth credentials removed."""
    env = {k: v for k, v in os.environ.items() if k != "FIGMA_TOKEN"}
    # OAuth login is intentionally supported by the CLI, so removing only
    # FIGMA_TOKEN is no longer sufficient for a deterministic missing-auth
    # test when a developer has connected a local Figma account.
    env["FIGMAFORGE_CREDENTIALS_PATH"] = "/nonexistent/figmaforge-test-creds.json"
    return env


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
                    self.assertIn("accessibility_report", manifest)
                    self.assertIn("findings", manifest["accessibility_report"])
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

    def _fixture_with_image_fill(self, tmp: str):
        """Write a Figma file JSON whose first filled node has an IMAGE paint;
        return (path, node_id)."""
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))

        def find(node):
            # Skip structural containers: a fill must land on a node that the
            # layout plan actually renders (not the DOCUMENT/CANVAS roots).
            if (isinstance(node, dict) and node.get("id") and node.get("type")
                    and node.get("type") not in ("DOCUMENT", "CANVAS", "PAGE")):
                return node
            for child in node.get("children") or []:
                hit = find(child)
                if hit:
                    return hit
            return None

        target = find(data.get("document") or {})
        self.assertIsNotNone(target, "fixture has no renderable node to attach an IMAGE fill to")
        target["fills"] = [{
            "type": "IMAGE", "imageRef": "img:1", "visible": True,
            "opacity": 1.0, "blendMode": "NORMAL", "scaleMode": "FILL",
        }]
        out = Path(tmp) / "with_image.json"
        out.write_text(json.dumps(data), encoding="utf-8")
        return str(out), target["id"]

    @staticmethod
    def _asset_manifest_for(tmp: str, node_id: str) -> str:
        manifest = Path(tmp) / "manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "file_key": "layout_desktop",
            "assets": [{
                "node_id": node_id, "url": "file:///tmp/photo.png",
                "image_ref": "img:1", "kind": "image",
                "status": "downloaded", "content_hash": "abc123",
                "local_path": "/tmp/ff-a18/photo.png",
            }],
            "counts": {"total": 1, "downloaded": 1, "unresolved": 0},
            "assets_dir": "/tmp/ff-a18/assets",
        }), encoding="utf-8")
        return str(manifest)

    def _generated_content(self, tmp: str, out_sub: str, manifest: dict) -> str:
        """Read back every generated file's content for one generate run."""
        parts = []
        for entry in manifest["files"]:
            p = Path(tmp) / out_sub / entry["path"]
            parts.append(p.read_text(encoding="utf-8"))
        return "\n".join(parts)

    def test_generate_assets_rejects_invalid_manifest(self):
        """--assets with a non-manifest JSON exits 4."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad_manifest.json"
            bad.write_text(json.dumps({"foo": 1}), encoding="utf-8")
            proc = _run(["generate", "--file", str(FIXTURE), "--backend", "html_css",
                         "--assets", str(bad)])
            self.assertEqual(proc.returncode, 4)
            self.assertIn("asset manifest", proc.stderr)

    def test_generate_assets_emits_image_url(self):
        """generate --assets threads the resolved path into html_css output;
        without it the honest fallback + marker stays."""
        with tempfile.TemporaryDirectory() as tmp:
            file_path, node_id = self._fixture_with_image_fill(tmp)
            manifest_path = self._asset_manifest_for(tmp, node_id)

            with_assets = _run(["generate", "--file", file_path,
                                "--backend", "html_css", "--assets", manifest_path,
                                "--out-dir", str(Path(tmp) / "wa")])
            without = _run(["generate", "--file", file_path,
                            "--backend", "html_css",
                            "--out-dir", str(Path(tmp) / "wo")])
            self.assertEqual(with_assets.returncode, 0, with_assets.stderr)
            self.assertEqual(without.returncode, 0, without.stderr)

            wa = self._generated_content(
                tmp, "wa/html_css", json.loads(with_assets.stdout))
            wo = self._generated_content(
                tmp, "wo/html_css", json.loads(without.stdout))
            self.assertIn("background-image: url(/tmp/ff-a18/photo.png)", wa)
            self.assertIn("background-size: cover", wa)
            self.assertNotIn("fills_image approximated", wa)
            self.assertIn("background: #f0f0f0", wo)
            self.assertIn("fills_image approximated", wo)

    def test_generate_assets_staged_equals_file_mode(self):
        """generate --ir/--layout --assets is byte-identical to --file --assets."""
        with tempfile.TemporaryDirectory() as tmp:
            file_path, node_id = self._fixture_with_image_fill(tmp)
            manifest_path = self._asset_manifest_for(tmp, node_id)
            ir_path = Path(tmp) / "ir.json"
            proc = _run(["normalize", "--file", file_path, "--out", str(ir_path)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            layout_path = Path(tmp) / "layout.json"
            proc = _run(["layout", "--file", str(ir_path), "--out", str(layout_path)])
            self.assertEqual(proc.returncode, 0, proc.stderr)

            file_run = _run(["generate", "--file", file_path,
                             "--backend", "html_css", "--assets", manifest_path,
                             "--out-dir", str(Path(tmp) / "fm")])
            staged_run = _run(["generate", "--ir", str(ir_path),
                               "--layout", str(layout_path),
                               "--backend", "html_css", "--assets", manifest_path,
                               "--out-dir", str(Path(tmp) / "sm")])
            self.assertEqual(file_run.returncode, 0, file_run.stderr)
            self.assertEqual(staged_run.returncode, 0, staged_run.stderr)
            self.assertEqual(file_run.stdout, staged_run.stdout)

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


class TestAssetsStage(unittest.TestCase):
    """assets subcommand: download + content-address IR asset refs (Part 17)."""

    def _normalize_to(self, tmp: str) -> Path:
        out_path = Path(tmp) / "ir.json"
        proc = _run(["normalize", "--file", str(FIXTURE), "--out", str(out_path)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return out_path

    def _ir_with_doc_asset(self, tmp: str, url: str) -> str:
        """Normalize the fixture and add a document-level asset URL."""
        ir_path = self._normalize_to(tmp)
        data = json.loads(ir_path.read_text(encoding="utf-8"))
        data["assets"]["2:1"] = url
        ir_path.write_text(json.dumps(data), encoding="utf-8")
        return str(ir_path)

    @staticmethod
    def _first_node_id(node):
        if isinstance(node, dict) and node.get("id"):
            return node["id"]
        for child in node.get("children") or []:
            found = TestAssetsStage._first_node_id(child)
            if found:
                return found
        return None

    def _ir_with_node_asset(self, tmp: str, asset_dict: dict) -> str:
        """Normalize the fixture and attach ``asset_dict`` to its first node."""
        ir_path = self._normalize_to(tmp)
        data = json.loads(ir_path.read_text(encoding="utf-8"))
        target = self._first_node_id(data["root"])

        def inject(node):
            if isinstance(node, dict) and node.get("id") == target:
                node["asset"] = asset_dict
            for child in node.get("children") or []:
                inject(child)
            return node

        inject(data["root"])
        ir_path.write_text(json.dumps(data), encoding="utf-8")
        return str(ir_path)

    def test_assets_empty_manifest_deterministic(self):
        """Fixture IR (no assets) emits an empty manifest; runs byte-identical."""
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = self._normalize_to(tmp)
            assets_dir = Path(tmp) / "assets"
            args = ["assets", "--ir", str(ir_path), "--assets-dir", str(assets_dir)]
            first = _run(args)
            second = _run(args)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first.stdout, second.stdout)

            payload = json.loads(first.stdout)
            self.assertEqual(payload["assets"], [])
            self.assertEqual(payload["counts"], {"total": 0, "downloaded": 0, "unresolved": 0})
            self.assertEqual(payload["assets_dir"], str(assets_dir.resolve()))

    def test_assets_downloads_file_url_and_stores(self):
        """A file:// URL is fetched, hashed, content-addressed, and recorded."""
        import hashlib

        png = b"\x89PNG\r\n\x1a\nfake-asset-bytes"
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "photo.png"
            src.write_bytes(png)
            url = "file://" + str(src)
            ir_path = self._ir_with_doc_asset(tmp, url)
            assets_dir = Path(tmp) / "assets"
            proc = _run(["assets", "--ir", ir_path, "--assets-dir", str(assets_dir)])
            self.assertEqual(proc.returncode, 0, proc.stderr)

            payload = json.loads(proc.stdout)
            expected_hash = hashlib.sha256(png).hexdigest()
            self.assertEqual(payload["counts"], {"total": 1, "downloaded": 1, "unresolved": 0})
            (entry,) = payload["assets"]
            self.assertEqual(entry["node_id"], "2:1")
            self.assertEqual(entry["url"], url)
            self.assertEqual(entry["kind"], "image")
            self.assertEqual(entry["status"], "downloaded")
            self.assertEqual(entry["content_hash"], expected_hash)
            self.assertEqual(
                entry["local_path"],
                str(assets_dir.resolve() / expected_hash[:2] / expected_hash),
            )
            self.assertTrue(Path(entry["local_path"]).read_bytes() == png)

            # Manifest records kind/extension in the store.
            meta_path = assets_dir / "manifest.json"
            self.assertTrue(meta_path.exists())
            store = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(store["assets"][expected_hash]["kind"], "image")
            self.assertEqual(store["assets"][expected_hash]["extension"], "png")

    def test_assets_svg_kind_downloaded(self):
        """An .svg URL downloads as an SVG-validated, content-addressed asset."""
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "icon.svg"
            src.write_bytes(svg)
            url = "file://" + str(src)
            ir_path = self._ir_with_doc_asset(tmp, url)
            assets_dir = Path(tmp) / "assets"
            proc = _run(["assets", "--ir", ir_path, "--assets-dir", str(assets_dir)])
            self.assertEqual(proc.returncode, 0, proc.stderr)

            payload = json.loads(proc.stdout)
            (entry,) = payload["assets"]
            self.assertEqual(entry["kind"], "svg")
            self.assertEqual(entry["status"], "downloaded")

            store = json.loads((assets_dir / "manifest.json").read_text(encoding="utf-8"))
            meta = store["assets"][entry["content_hash"]]
            self.assertEqual(meta["kind"], "svg")
            self.assertEqual(meta["extension"], "svg")

    def test_assets_rejects_unsafe_svg(self):
        """An embedded-script SVG fails the stage with a clear error (exit 1)."""
        bad = b"<svg><script>alert(1)</script></svg>"
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "evil.svg"
            src.write_bytes(bad)
            url = "file://" + str(src)
            ir_path = self._ir_with_doc_asset(tmp, url)
            proc = _run(["assets", "--ir", ir_path, "--assets-dir", str(Path(tmp) / "assets")])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("SVG", proc.stderr)
            self.assertEqual(proc.stdout.strip(), "")

    def test_assets_missing_token_exit_3(self):
        """An unresolved image_ref (no URL) without FIGMA_TOKEN exits 3."""
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = self._ir_with_node_asset(tmp, {"node_id": "x", "url": None, "image_ref": "img:9"})
            proc = _run(
                ["assets", "--ir", ir_path, "--assets-dir", str(Path(tmp) / "assets")],
                env=_no_token_env(),
            )
            self.assertEqual(proc.returncode, 3)
            self.assertIn("FIGMA_TOKEN", proc.stderr)

    def test_assets_invalid_ir(self):
        """A non-IR JSON input exits 4."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "not_ir.json"
            bad.write_text(json.dumps({"foo": 1}), encoding="utf-8")
            proc = _run(["assets", "--ir", str(bad), "--assets-dir", str(Path(tmp) / "assets")])
            self.assertEqual(proc.returncode, 4)
            self.assertTrue(proc.stderr.strip())


# ---------------------------------------------------------------------------
# spec subcommand
# ---------------------------------------------------------------------------

RICH_FIXTURE = plugin_root / "fixtures" / "figma" / "rich_landing.json"
IR_FIXTURE = plugin_root / "fixtures" / "figma" / "layout_desktop.json"


class TestSpec(unittest.TestCase):
    """Test the ``spec`` subcommand (semantic design spec generation)."""

    def test_spec_from_raw_figma_json(self):
        """spec --file <raw Figma JSON> produces a valid design spec."""
        proc = _run(["spec", "--file", str(RICH_FIXTURE)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        spec = json.loads(lines[0])
        self.assertIn("page", spec)
        self.assertIn("sections", spec)
        self.assertIn("design_tokens", spec)
        self.assertIsInstance(spec["sections"], list)
        self.assertGreater(len(spec["sections"]), 0)

    def test_spec_from_raw_figma_matches_design_spec_module(self):
        """spec output must be byte-identical to DesignSpecGenerator."""
        proc = _run(["spec", "--file", str(RICH_FIXTURE)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        spec = json.loads(proc.stdout.strip())

        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(RICH_FIXTURE, encoding="utf-8") as f:
            figma = json.load(f)
        expected = gen.generate_from_figma(figma)
        self.assertEqual(spec, expected)

    def test_spec_json_serializable(self):
        """The spec must round-trip through JSON."""
        proc = _run(["spec", "--file", str(RICH_FIXTURE)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        spec = json.loads(proc.stdout.strip())
        serialized = json.dumps(spec, sort_keys=True)
        reparsed = json.loads(serialized)
        self.assertEqual(spec, reparsed)

    def test_spec_sections_have_required_fields(self):
        """Every section must have id, name, type, layout."""
        proc = _run(["spec", "--file", str(RICH_FIXTURE)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        spec = json.loads(proc.stdout.strip())
        for section in spec["sections"]:
            self.assertIn("id", section)
            self.assertIn("name", section)
            self.assertIn("type", section)
            self.assertIn("layout", section)

    def test_spec_out_flag_writes_file(self):
        """--out writes the spec to disk."""
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "spec.json"
            proc = _run(["spec", "--file", str(RICH_FIXTURE), "--out", str(out_path)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out_path.exists())
            on_disk = json.loads(out_path.read_text(encoding="utf-8"))
            stdout_spec = json.loads(proc.stdout.strip())
            self.assertEqual(on_disk, stdout_spec)

    def test_spec_deterministic(self):
        """Two spec runs produce byte-identical output."""
        first = _run(["spec", "--file", str(RICH_FIXTURE)])
        second = _run(["spec", "--file", str(RICH_FIXTURE)])
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)

    def test_spec_empty_document(self):
        """A document with no children produces empty sections."""
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.json"
            empty.write_text(json.dumps({
                "file_key": "empty",
                "name": "Empty",
                "document": {"type": "DOCUMENT", "id": "0:0", "children": []},
            }), encoding="utf-8")
            proc = _run(["spec", "--file", str(empty)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            spec = json.loads(proc.stdout.strip())
            self.assertEqual(spec["sections"], [])

    def test_spec_bad_json_exit_4(self):
        """Non-JSON input exits 4."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("not json", encoding="utf-8")
            proc = _run(["spec", "--file", str(bad)])
            self.assertEqual(proc.returncode, 4)
            self.assertTrue(proc.stderr.strip())

    def test_spec_missing_file_flag(self):
        """spec without --file is rejected by argparse."""
        proc = _run(["spec"])
        self.assertNotEqual(proc.returncode, 0)


# ---------------------------------------------------------------------------
# compare subcommand
# ---------------------------------------------------------------------------


class TestCompare(unittest.TestCase):
    """Test the ``compare`` subcommand (pixel-diff feedback for agents)."""

    def _make_png(self, path: Path, width: int, height: int, r: int, g: int, b: int):
        """Write a solid-color PNG to *path*."""
        from core.png_codec import PngImage, encode_png
        pixels = bytes([r, g, b]) * (width * height)
        path.write_bytes(encode_png(PngImage(width=width, height=height, channels=3, pixels=pixels)))

    def test_identical_images_score_1(self):
        """Two identical PNGs produce similarity 1.0 and no mismatches."""
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.png"
            b = Path(tmp) / "b.png"
            self._make_png(a, 10, 10, 255, 0, 0)
            self._make_png(b, 10, 10, 255, 0, 0)
            proc = _run(["compare", "--baseline", str(a), "--generated", str(b)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout.strip())
            self.assertAlmostEqual(result["similarity_score"], 1.0, places=4)
            self.assertEqual(result["verdict"], "identical")
            self.assertEqual(len(result["mismatches"]), 0)

    def test_different_images_score_below_1(self):
        """Two different PNGs with different structure produce similarity < 1.0."""
        from core.png_codec import PngImage, encode_png
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.png"
            b = Path(tmp) / "b.png"
            # Left half red, right half red → solid red
            a_pixels = bytes([255, 0, 0]) * 50 + bytes([255, 0, 0]) * 50
            a.write_bytes(encode_png(PngImage(width=10, height=10, channels=3, pixels=a_pixels)))
            # Left half red, right half black → structural difference
            b_pixels = bytes([255, 0, 0]) * 50 + bytes([0, 0, 0]) * 50
            b.write_bytes(encode_png(PngImage(width=10, height=10, channels=3, pixels=b_pixels)))
            proc = _run(["compare", "--baseline", str(a), "--generated", str(b)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout.strip())
            self.assertLess(result["similarity_score"], 1.0)
            self.assertEqual(result["verdict"], "changed")
            self.assertGreater(len(result["mismatches"]), 0)

    def test_out_flag(self):
        """--out writes the comparison result to disk."""
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.png"
            b = Path(tmp) / "b.png"
            out = Path(tmp) / "result.json"
            self._make_png(a, 10, 10, 255, 0, 0)
            self._make_png(b, 10, 10, 0, 0, 255)
            proc = _run(["compare", "--baseline", str(a), "--generated", str(b), "--out", str(out)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.exists())
            on_disk = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(on_disk, json.loads(proc.stdout.strip()))

    def test_missing_files_exit_4(self):
        """Missing baseline or generated file exits 4."""
        proc = _run(["compare", "--baseline", "/nonexistent/a.png", "--generated", "/nonexistent/b.png"])
        self.assertEqual(proc.returncode, 4)
        self.assertTrue(proc.stderr.strip())

    def test_json_serializable(self):
        """The compare output must be JSON-serializable."""
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.png"
            b = Path(tmp) / "b.png"
            self._make_png(a, 10, 10, 255, 0, 0)
            self._make_png(b, 10, 10, 0, 0, 255)
            proc = _run(["compare", "--baseline", str(a), "--generated", str(b)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout.strip())
            serialized = json.dumps(result, sort_keys=True)
            self.assertEqual(result, json.loads(serialized))


# ---------------------------------------------------------------------------
# agent-loop subcommand
# ---------------------------------------------------------------------------


class TestAgentLoop(unittest.TestCase):
    """Test the ``agent-loop`` subcommand (full pipeline with feedback)."""

    def test_agent_loop_produces_spec_and_feedback(self):
        """agent-loop on a raw Figma file produces spec + compare feedback."""
        proc = _run(["agent-loop", "--file", str(RICH_FIXTURE), "--backend", "html_css"])
        # May need a baseline — this tests the spec + generate path at minimum
        self.assertIn(proc.returncode, (0, 3), proc.stderr)  # 3 = no baseline
        result = json.loads(proc.stdout.strip())
        self.assertIn("spec", result)
        self.assertIn("sections", result["spec"])
        self.assertIn("generated", result)
        self.assertIn("backend", result["generated"])

    def test_agent_loop_spec_matches_spec_command(self):
        """The spec inside agent-loop output matches ``figmaforge spec``."""
        proc_loop = _run(["agent-loop", "--file", str(RICH_FIXTURE), "--backend", "html_css"])
        proc_spec = _run(["spec", "--file", str(RICH_FIXTURE)])
        self.assertEqual(proc_loop.returncode, 0, proc_loop.stderr)
        self.assertEqual(proc_spec.returncode, 0, proc_spec.stderr)
        loop_result = json.loads(proc_loop.stdout.strip())
        spec_result = json.loads(proc_spec.stdout.strip())
        self.assertEqual(loop_result["spec"], spec_result)

    def test_agent_loop_missing_file_exit_4(self):
        """agent-loop with a nonexistent file exits 4."""
        proc = _run(["agent-loop", "--file", "/nonexistent/file.json", "--backend", "html_css"])
        self.assertEqual(proc.returncode, 4)
        self.assertTrue(proc.stderr.strip())

    def test_agent_loop_bad_backend_exit_2(self):
        """agent-loop with an unknown backend exits 2."""
        proc = _run(["agent-loop", "--file", str(RICH_FIXTURE), "--backend", "nonexistent"])
        self.assertEqual(proc.returncode, 2)

    def test_agent_loop_output_structure(self):
        """The output must have the expected top-level keys."""
        proc = _run(["agent-loop", "--file", str(RICH_FIXTURE), "--backend", "html_css"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.strip())
        # Must have spec
        self.assertIn("spec", result)
        self.assertIn("page", result["spec"])
        self.assertIn("sections", result["spec"])
        # Must have generated
        self.assertIn("generated", result)
        self.assertIn("backend", result["generated"])
        self.assertIn("files", result["generated"])
        self.assertIn("manifest", result["generated"])
        # Must have feedback (even without baseline)
        self.assertIn("feedback", result)
        self.assertIn("verdict", result["feedback"])


if __name__ == "__main__":
    unittest.main()
