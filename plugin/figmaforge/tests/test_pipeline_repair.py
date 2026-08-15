"""
Repair CLI tests (Part 20, Task 3).

Exercises ``scripts/pipeline.py repair`` in-process with an injected fake
harness (no browser): the loop runs against staged IR + layout + baseline,
repaired styles serialize, html_css regenerates with the styles_override,
and the one-JSON-line / exit-code contract holds.

Run:  python3 -m unittest tests.test_pipeline_repair -v
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))
tests_dir = Path(__file__).resolve().parent
if str(tests_dir) not in sys.path:
    sys.path.insert(0, str(tests_dir))

from core.ir_types import (  # noqa: E402
    IRColor,
    IRDocument,
    IRFill,
    IRNode,
    IRSource,
    IRStyle,
    KIND_FRAME,
    KIND_PAGE,
)
from core.layout_types import (  # noqa: E402
    Box,
    DISPLAY_FLEX,
    LayoutNodePlan,
    LayoutPlan,
)
from core.png_codec import PngImage, encode_png  # noqa: E402
from core.resolver import report_to_json  # noqa: E402
from test_backend_honesty_audit import canonical_fixture  # noqa: E402


def _solid_png(width, height, rgb, rect=None):
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            if (rect is not None
                    and rect[0] <= x < rect[0] + rect[2]
                    and rect[1] <= y < rect[1] + rect[3]):
                pixels.extend(rect[4])
            else:
                pixels.extend(rgb)
    return encode_png(PngImage(width=width, height=height, channels=3,
                               pixels=bytes(pixels)))


def _solid_and_image_fixture():
    """Two children: ``n1`` (solid fill, inside the harness's diff region)
    and ``img:1`` (image fill, OUTSIDE it) — so repair patches only the
    solid node and the image node keeps its resolved asset."""
    n1 = IRNode(
        id="n1", name="Box", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="repaira", node_id="n1"),
        style=IRStyle(fills=[IRFill(
            kind="solid", color=IRColor(r=0.1, g=0.1, b=0.1, a=1.0),
        )]),
    )
    img = IRNode(
        id="img:1", name="Photo", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="repaira", node_id="img:1"),
        style=IRStyle(fills=[IRFill(kind="image", image_ref="img/photo")]),
    )
    root = IRNode(
        id="frame-root", name="Root", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="repaira", node_id="frame-root"),
        children=[n1, img],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="repaira", node_id="page-1"),
        children=[root],
    )
    doc = IRDocument(file_key="repaira", name="RepairAssets", pages=[page])
    doc.root = root
    screen = LayoutNodePlan(
        node_id="frame-root", name="Root", kind="frame",
        display=DISPLAY_FLEX, box=Box(x=0, y=0, width=800, height=600),
    )
    screen.children.append(LayoutNodePlan(
        node_id="n1", name="Box", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=100),
    ))
    screen.children.append(LayoutNodePlan(
        node_id="img:1", name="Photo", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=300, width=100, height=40),
    ))
    plan = LayoutPlan(file_key="repaira", viewport=800.0, screens=[screen])
    return doc, plan


def _make_fixture():
    """A single screen (frame-root) with one child (n1, solid #1a1a1a fill)."""
    box = IRNode(
        id="n1", name="Box", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="repair", node_id="n1"),
        style=IRStyle(fills=[IRFill(
            kind="solid", color=IRColor(r=0.1, g=0.1, b=0.1, a=1.0),
        )]),
    )
    root = IRNode(
        id="frame-root", name="Root", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="repair", node_id="frame-root"),
        children=[box],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="repair", node_id="page-1"),
        children=[root],
    )
    doc = IRDocument(file_key="repair", name="Repair", pages=[page])
    doc.root = root
    screen = LayoutNodePlan(
        node_id="frame-root", name="Root", kind="frame",
        display=DISPLAY_FLEX, box=Box(x=0, y=0, width=800, height=600),
    )
    screen.children.append(LayoutNodePlan(
        node_id="n1", name="Box", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=100),
    ))
    plan = LayoutPlan(file_key="repair", viewport=800.0, screens=[screen])
    return doc, plan


META = {
    "frame-root": {"x": 0, "y": 0, "width": 800, "height": 600},
    "n1": {"x": 0, "y": 0, "width": 200, "height": 100},
}

ASSET_META = dict(META)
ASSET_META["img:1"] = {"x": 0, "y": 300, "width": 100, "height": 40}


class _FakeHarness:
    """Deterministic harness: iteration 0 renders a red block over n1's
    bbox; every later iteration renders the (white) baseline — so the loop
    must patch the background to converge.  ``always=True`` renders the
    baseline every time (no mismatch to repair)."""

    def __init__(self, output_dir, shot, baseline, meta=None,
                 always=False, fail=False):
        self._output_dir = Path(output_dir)
        self._shot = shot
        self._baseline = baseline
        self._meta = meta or META
        self._always = always
        self._fail = fail

    def render(self, content_html, viewport_spec, build_id, full_page=True):
        if self._fail:
            raise RuntimeError("simulated render failure")
        if self._always or build_id != "repair-iter-0":
            path = str(self._baseline)
        else:
            path = str(self._shot)
        return SimpleNamespace(
            layout_metadata=dict(self._meta), screenshot_path=path,
        )


class TestRepairCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.baseline = self.dir / "baseline.png"
        self.baseline.write_bytes(_solid_png(800, 600, (255, 255, 255)))
        self.shot = self.dir / "shot.png"
        self.shot.write_bytes(_solid_png(
            800, 600, (255, 255, 255), rect=(0, 0, 200, 100, (255, 0, 0)),
        ))
        doc, plan = _make_fixture()
        self.ir_file = self.dir / "ir.json"
        self.ir_file.write_text(
            json.dumps(doc.to_dict(), sort_keys=True), encoding="utf-8",
        )
        self.layout_file = self.dir / "layout.json"
        self.layout_file.write_text(
            json.dumps(plan.to_dict(), sort_keys=True), encoding="utf-8",
        )

    def _run(self, extra, out_dir=None, harness=None):
        from scripts.pipeline import repair_main
        argv = [
            "--ir", str(self.ir_file),
            "--layout", str(self.layout_file),
            "--baseline", str(self.baseline),
            "--out", str(out_dir or (self.dir / "out")),
        ]
        argv += extra
        default_harness = (
            lambda d: _FakeHarness(d, self.shot, self.baseline)
        )
        buf = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            code = repair_main(argv, harness_cls=harness or default_harness)
        return code, buf.getvalue(), err.getvalue()

    def test_repair_converges_and_regenerates(self):
        code, out, _ = self._run(["--threshold", "1.0", "--max-iterations", "5"])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)  # one JSON line
        payload = json.loads(out.strip())
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["success"])
        self.assertGreaterEqual(payload["iterations_run"], 1)
        self.assertEqual(payload["stop_reason"], "threshold_satisfied")
        self.assertEqual(payload["final_score"], 1.0)
        self.assertGreaterEqual(payload["repairs"][0]["applied"], 1)
        self.assertEqual(payload["repaired_styles"], "styles.repaired.json")
        self.assertEqual(payload["generated"]["backend"], "html_css")
        self.assertTrue(payload["generated"]["files"])

        out_dir = self.dir / "out"
        # Repaired styles serialize with the real baseline color.
        styles = json.loads((out_dir / "styles.repaired.json").read_text())
        self.assertEqual(styles["n1"]["base"]["background"], "#ffffff")
        # Full history artifact written.
        history = json.loads((out_dir / "repair_history.json").read_text())
        self.assertEqual(history["status"], "completed")
        # Regenerated html_css carries the repaired background, not the
        # computed fill.
        css = (out_dir / "generated" / "html_css" / "styles.css").read_text()
        self.assertIn("background: #ffffff", css)
        self.assertNotIn("background: #1a1a1a", css)

    def test_no_mismatch_short_circuits(self):
        harness = lambda d: _FakeHarness(  # noqa: E731
            d, self.shot, self.baseline, always=True,
        )
        code, out, _ = self._run(["--threshold", "1.0"], harness=harness)
        self.assertEqual(code, 0)
        payload = json.loads(out.strip())
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["success"])
        self.assertEqual(payload["iterations_run"], 0)
        # The stopping iteration is still recorded, but no work was done.
        self.assertEqual(payload["repairs"][0]["applied"], 0)
        self.assertIsNone(payload["generated"])  # nothing to regenerate

    def test_missing_ir_or_layout_or_baseline_exits_2(self):
        from scripts.pipeline import repair_main
        default_harness = (
            lambda d: _FakeHarness(d, self.shot, self.baseline)
        )
        cases = [
            ["--layout", str(self.layout_file), "--baseline", str(self.baseline)],
            ["--ir", str(self.ir_file), "--baseline", str(self.baseline)],
            ["--ir", str(self.ir_file), "--layout", str(self.layout_file)],
        ]
        for argv in cases:
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                code = repair_main(argv, harness_cls=default_harness)
            self.assertEqual(code, 2)

    def test_baseline_missing_file_exits_4(self):
        code, out, err = self._run(["--baseline", str(self.dir / "nope.png")])
        self.assertEqual(code, 4)
        self.assertEqual(out, "")
        self.assertIn("baseline", err)

    def test_invalid_ir_exits_4(self):
        bad = self.dir / "bad.json"
        bad.write_text(json.dumps({"not": "an ir"}), encoding="utf-8")
        code, out, err = self._run(["--ir", str(bad)])
        self.assertEqual(code, 4)
        self.assertEqual(out, "")
        self.assertIn("ir", err.lower())

    def test_unsupported_backend_exits_2(self):
        code, out, err = self._run(["--backend", "flutter"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("html_css", err)

    def test_bad_viewport_exits_2(self):
        code, out, err = self._run(["--viewport", "abc"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")

    def test_bad_threshold_exits_2(self):
        code, out, _ = self._run(["--threshold", "1.5"])
        self.assertEqual(code, 2)

    def test_render_failure_exits_1_without_traceback(self):
        harness = lambda d: _FakeHarness(  # noqa: E731
            d, self.shot, self.baseline, fail=True,
        )
        code, out, err = self._run([], harness=harness)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")  # nothing emitted on failure
        self.assertIn("simulated render failure", err)
        self.assertNotIn("Traceback", err)

    def test_deterministic_two_runs(self):
        code1, out1, _ = self._run(
            ["--threshold", "1.0"], out_dir=self.dir / "out-a",
        )
        code2, out2, _ = self._run(
            ["--threshold", "1.0"], out_dir=self.dir / "out-b",
        )
        self.assertEqual(code1, 0)
        self.assertEqual(code2, 0)
        self.assertEqual(out1, out2)

    def test_repair_regenerates_react_tailwind(self):
        """repair --backend react_tailwind regenerates TSX with the override."""
        code, out, _ = self._run([
            "--backend", "react_tailwind", "--threshold", "1.0",
            "--max-iterations", "5",
        ])
        self.assertEqual(code, 0)
        payload = json.loads(out.strip())
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["iterations_run"], 1)
        self.assertEqual(payload["generated"]["backend"], "react_tailwind")
        tsx = (self.dir / "out" / "generated" / "react_tailwind" / "Root.tsx").read_text()
        # The repaired background (baseline white) wins over the IR fill.
        self.assertIn("bg-[#ffffff]", tsx)
        self.assertNotIn("bg-[#1a1a1a]", tsx)

    def test_repair_regenerates_vue(self):
        code, out, _ = self._run([
            "--backend", "vue", "--threshold", "1.0", "--max-iterations", "5",
        ])
        self.assertEqual(code, 0)
        payload = json.loads(out.strip())
        self.assertEqual(payload["generated"]["backend"], "vue")
        vue = (self.dir / "out" / "generated" / "vue" / "Root.vue").read_text()
        self.assertIn("background: #ffffff", vue)
        self.assertNotIn("background: #1a1a1a", vue)

    def test_repair_regenerates_svelte(self):
        code, out, _ = self._run([
            "--backend", "svelte", "--threshold", "1.0", "--max-iterations", "5",
        ])
        self.assertEqual(code, 0)
        payload = json.loads(out.strip())
        self.assertEqual(payload["generated"]["backend"], "svelte")
        svelte = (self.dir / "out" / "generated" / "svelte" / "Root.svelte").read_text()
        self.assertIn("background: #ffffff", svelte)
        self.assertNotIn("background: #1a1a1a", svelte)

    def test_repair_rejects_native_and_unknown_backends(self):
        for backend in ("flutter", "swiftui", "no-such-backend"):
            code, out, err = self._run(["--backend", backend])
            self.assertEqual(code, 2, backend)
            self.assertEqual(out, "", backend)
            self.assertIn("html_css", err, backend)

    def test_repair_unreadable_resolution_or_assets_exits_4(self):
        missing = self.dir / "missing.json"
        code, out, err = self._run(["--resolution", str(missing)])
        self.assertEqual(code, 4)
        self.assertIn("resolution", err.lower())
        code, out, err = self._run(["--assets", str(missing)])
        self.assertEqual(code, 4)
        self.assertIn("asset", err.lower())

    def test_repair_resolution_keeps_component_refs(self):
        """--resolution keeps component/instance resolution in regenerated web
        output (design review F1 — without it the Part-21 fallback machinery
        would vanish)."""
        doc, plan, resolution = canonical_fixture()
        ir_file = self.dir / "canon-ir.json"
        ir_file.write_text(json.dumps(doc.to_dict(), sort_keys=True), encoding="utf-8")
        layout_file = self.dir / "canon-layout.json"
        layout_file.write_text(json.dumps(plan.to_dict(), sort_keys=True), encoding="utf-8")
        res_file = self.dir / "canon-resolution.json"
        res_file.write_text(report_to_json(resolution), encoding="utf-8")

        default_harness = lambda d: _FakeHarness(d, self.shot, self.baseline)  # noqa: E731
        from scripts.pipeline import repair_main
        argv = [
            "--ir", str(ir_file), "--layout", str(layout_file),
            "--baseline", str(self.baseline), "--resolution", str(res_file),
            "--backend", "react_tailwind", "--threshold", "1.0",
            "--max-iterations", "5", "--out", str(self.dir / "out-res"),
        ]
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            code = repair_main(argv, harness_cls=default_harness)
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue().strip())
        self.assertEqual(payload["generated"]["backend"], "react_tailwind")
        tsx = (self.dir / "out-res" / "generated" / "react_tailwind" / "Root.tsx").read_text()
        # Component ref + self-contained fallback survive regeneration (F1).
        self.assertIn("<ButtonCard", tsx)
        self.assertIn("function ButtonCard(", tsx)

    def test_repair_assets_threaded_into_regeneration(self):
        """--assets keeps real image references in the regenerated output
        (Part 18 contract): the patched solid node gets the override, while
        the image node (outside the diff region) keeps its resolved url."""
        doc, plan = _solid_and_image_fixture()
        ir_file = self.dir / "img-ir.json"
        ir_file.write_text(json.dumps(doc.to_dict(), sort_keys=True), encoding="utf-8")
        layout_file = self.dir / "img-layout.json"
        layout_file.write_text(json.dumps(plan.to_dict(), sort_keys=True), encoding="utf-8")
        assets_file = self.dir / "assets.json"
        assets_file.write_text(json.dumps({"assets": [
            {"node_id": "img:1", "local_path": "/store/photo.png",
             "kind": "image", "status": "downloaded"},
        ]}), encoding="utf-8")

        def harness(d):
            return _FakeHarness(d, self.shot, self.baseline, meta=ASSET_META)

        from scripts.pipeline import repair_main
        argv = [
            "--ir", str(ir_file), "--layout", str(layout_file),
            "--baseline", str(self.baseline), "--assets", str(assets_file),
            "--backend", "react_tailwind", "--threshold", "1.0",
            "--max-iterations", "5", "--out", str(self.dir / "out-assets"),
        ]
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            code = repair_main(argv, harness_cls=harness)
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue().strip())
        self.assertEqual(payload["generated"]["backend"], "react_tailwind")
        tsx = (self.dir / "out-assets" / "generated" / "react_tailwind" / "Root.tsx").read_text()
        # The image node was NOT patched — its resolved url survives.
        self.assertIn("bg-[url(/store/photo.png)]", tsx)
        self.assertNotIn("fills_image approximated", tsx)
        # The patched solid node carries the repaired background.
        self.assertIn("bg-[#ffffff]", tsx)
        self.assertIn("fills_solid approximated (overridden)", tsx)

        # The scoped backends must keep the image too: without assets in the
        # style layer, the serialized override carried the unresolved-fill
        # fallback ``background: #f0f0f0`` which — emitted after
        # ``background-image`` — would have reset the image to a flat color.
        for backend, suffix in (("vue", ".vue"), ("svelte", ".svelte")):
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                code = repair_main([
                    "--ir", str(ir_file), "--layout", str(layout_file),
                    "--baseline", str(self.baseline), "--assets", str(assets_file),
                    "--backend", backend, "--threshold", "1.0",
                    "--max-iterations", "5",
                    "--out", str(self.dir / f"out-assets-{backend}"),
                ], harness_cls=harness)
            self.assertEqual(code, 0, backend)
            out = (self.dir / f"out-assets-{backend}" / "generated" / backend
                   / f"Root{suffix}").read_text()
            self.assertIn("background-image: url(/store/photo.png)", out, backend)
            self.assertNotIn("background: #f0f0f0", out, backend)
            self.assertIn("background: #ffffff", out, backend)

    def test_require_approval_denies_non_interactively(self):
        code, out, _ = self._run(
            ["--threshold", "1.0", "--require-approval"],
        )
        self.assertEqual(code, 0)
        payload = json.loads(out.strip())
        self.assertFalse(payload["success"])
        self.assertEqual(payload["stop_reason"], "approval_denied")


if __name__ == "__main__":
    unittest.main()
