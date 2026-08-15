"""
Pipeline ``render`` subcommand tests (Part 19).

Covers the two core rendering modes through an injectable harness (no
browser in the test suite):

- ``--html`` (generated shot): renders a standalone HTML file → PNG + meta.
- ``--ir + --layout`` (reference baseline): computes the intended VStyles
  from the layout plan via the shared web lowering, builds the reference
  document via ``generate_render_html``, renders it → PNG + meta.

The CLI contract mirrors the pipeline CLI: exactly one JSON line on stdout
for a successful invocation; errors to stderr with a fixed exit code
(2 = bad invocation, 4 = unreadable input, 1 = render failure) — never a
traceback.

Run:  PYTHON_BIN=/opt/homebrew/bin/python3.14 python3 -m unittest tests.test_pipeline_render -v
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.figma_types import FigmaFile  # noqa: E402
from core.ir_builder import IRBuilder  # noqa: E402
from core.layout_analyzer import LayoutAnalyzer  # noqa: E402
from core.library_types import LibraryLoader  # noqa: E402
from core.render_harness import RenderHarnessError, RenderResult  # noqa: E402
from scripts.pipeline import _load_file_payload, render_main  # noqa: E402

FIXTURE_DIR = plugin_root / "fixtures" / "figma"
FIXTURE_RAW = FIXTURE_DIR / "layout_desktop.json"


class FakeHarness:
    """Callable duck-typed RenderHarness: records calls, returns canned meta."""

    def __init__(self, meta=None):
        self.meta = meta or {"n1": {"x": 0, "y": 0, "width": 200, "height": 100}}
        self.calls = []
        self.out_dir = None

    def __call__(self, out_dir):
        # The CLI instantiates ``harness_cls(out_dir)``; an instance returns
        # itself so tests can both inject and inspect the same object.
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return self

    def render(self, content_html, viewport_spec, build_id, full_page=True):
        self.calls.append({
            "html": content_html,
            "viewport": viewport_spec,
            "build_id": build_id,
            "full_page": full_page,
        })
        screenshot = self.out_dir / f"{build_id}.png"
        screenshot.write_bytes(b"\x89PNG-fake")
        return RenderResult(
            screenshot_path=screenshot,
            layout_metadata=dict(self.meta),
        )


class FailingHarness:
    """Callable harness whose render raises the Playwright-missing error."""

    def __call__(self, out_dir):
        return self

    def render(self, content_html, viewport_spec, build_id, full_page=True):
        raise RenderHarnessError(
            "playwright is required for browser rendering. "
            "Install it with: pip install playwright && playwright install chromium"
        )


def _run(argv, harness):
    """Run render_main, returning (exit_code, stdout_text, stderr_text)."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = render_main(argv, harness_cls=harness)
    return code, stdout.getvalue(), stderr.getvalue()


def _parse_stdout(text):
    """Parse the single JSON line render_main prints on success."""
    lines = [l for l in text.splitlines() if l.strip()]
    assert len(lines) == 1, f"expected exactly one stdout line, got {len(lines)}"
    return json.loads(lines[0])


def _reference_fixtures(tmp: Path):
    """Write ir.json + layout.json for the offline fixture; return their paths."""
    raw = _load_file_payload(str(FIXTURE_RAW))
    doc = IRBuilder().build(FigmaFile.from_dict(raw.get("file_key") or "fixture", raw))
    ir_path = tmp / "ir.json"
    ir_path.write_text(json.dumps(doc.to_dict()), encoding="utf-8")

    plan = LayoutAnalyzer().analyze(
        doc, library=LibraryLoader().load(), viewport=1440.0,
    )
    layout_path = tmp / "layout.json"
    layout_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
    return ir_path, layout_path


class TestRenderMain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ff-render-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------ --html mode

    def test_html_mode_renders_file(self):
        html = self.tmp / "screen.html"
        html.write_text(
            '<div id="x" style="width:100px;height:100px;background:#ff0000"></div>',
            encoding="utf-8",
        )
        harness = FakeHarness()
        code, out, err = _run(["--html", str(html), "--out", str(self.tmp)], harness)
        self.assertEqual(code, 0, err)
        payload = _parse_stdout(out)
        self.assertTrue(payload.pop("ok"))
        self.assertEqual(payload["kind"], "generated")
        self.assertEqual(
            set(payload.keys()), {"kind", "screenshot", "html", "meta", "viewport"},
        )
        # The harness received the file content + the default viewport.
        self.assertEqual(len(harness.calls), 1)
        call = harness.calls[0]
        self.assertIn('width:100px;height:100px', call["html"])
        self.assertEqual(call["viewport"], {"width": 1440, "height": 900})
        self.assertRegex(call["build_id"], r"^ff-shot-[0-9a-f]{8}$")
        # Screenshot + written html files exist.
        self.assertTrue(Path(payload["screenshot"]).exists())
        self.assertTrue(Path(payload["html"]).exists())

    def test_html_mode_viewport_flag(self):
        html = self.tmp / "screen.html"
        html.write_text("<p>v</p>", encoding="utf-8")
        harness = FakeHarness()
        code, out, err = _run(
            ["--html", str(html), "--viewport", "390x844"], harness,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(harness.calls[0]["viewport"], {"width": 390, "height": 844})

    def test_bad_viewport_exits_2(self):
        html = self.tmp / "screen.html"
        html.write_text("<p>v</p>", encoding="utf-8")
        code, out, err = _run(["--html", str(html), "--viewport", "nope"], FakeHarness())
        self.assertEqual(code, 2)
        self.assertEqual(out, "")  # no JSON line on failure
        self.assertIn("viewport", err)

    def test_missing_html_file_exits_4(self):
        code, out, err = _run(
            ["--html", str(self.tmp / "absent.html")], FakeHarness(),
        )
        self.assertEqual(code, 4)
        self.assertEqual(out, "")
        self.assertIn("cannot read", err)

    # ------------------------------------------------- reference (--ir --layout)

    def test_reference_mode_uses_layout_styles(self):
        ir_path, layout_path = _reference_fixtures(self.tmp)
        harness = FakeHarness()
        code, out, err = _run(
            ["--ir", str(ir_path), "--layout", str(layout_path),
             "--out", str(self.tmp)],
            harness,
        )
        self.assertEqual(code, 0, err)
        payload = _parse_stdout(out)
        self.assertTrue(payload.pop("ok"))
        self.assertEqual(payload["kind"], "reference")
        self.assertEqual(
            set(payload.keys()), {"kind", "screenshot", "html", "meta", "viewport"},
        )
        # The reference HTML carries node ids AND layout-derived styles.
        html = harness.calls[0]["html"]
        self.assertIn("data-node-id=", html)
        self.assertIn("px", html)  # inline CSS width/height from the layout plan
        self.assertRegex(harness.calls[0]["build_id"], r"^ff-ref-[0-9a-f]{8}$")
        self.assertTrue(Path(payload["screenshot"]).exists())

    def test_reference_mode_no_layout_exits_2(self):
        ir_path, _ = _reference_fixtures(self.tmp)
        code, out, err = _run(["--ir", str(ir_path)], FakeHarness())
        self.assertEqual(code, 2)
        self.assertIn("--layout", err)

    def test_reference_mode_no_ir_exits_2(self):
        _, layout_path = _reference_fixtures(self.tmp)
        code, out, err = _run(["--layout", str(layout_path)], FakeHarness())
        self.assertEqual(code, 2)

    def test_both_html_and_ir_exits_2(self):
        html = self.tmp / "screen.html"
        html.write_text("<p>x</p>", encoding="utf-8")
        ir_path, layout_path = _reference_fixtures(self.tmp)
        code, out, err = _run(
            ["--html", str(html), "--ir", str(ir_path), "--layout", str(layout_path)],
            FakeHarness(),
        )
        self.assertEqual(code, 2)

    def test_invalid_ir_exits_4(self):
        bad = self.tmp / "bad.json"
        bad.write_text(json.dumps({"not": "an ir"}), encoding="utf-8")
        _, layout_path = _reference_fixtures(self.tmp)
        code, out, err = _run(
            ["--ir", str(bad), "--layout", str(layout_path)], FakeHarness(),
        )
        self.assertEqual(code, 4)
        self.assertIn("design IR", err)

    # ---------------------------------------------------------- failure modes

    def test_missing_playwright_clean_error(self):
        html = self.tmp / "screen.html"
        html.write_text("<p>p</p>", encoding="utf-8")
        code, out, err = _run(["--html", str(html)], FailingHarness())
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("playwright", err)
        self.assertNotIn("Traceback", err)

    def test_render_failure_never_tracebacks(self):
        class BoomHarness:
            def __call__(self, out_dir):
                return self

            def render(self, *a, **k):
                raise RuntimeError("chromium exploded")

        html = self.tmp / "screen.html"
        html.write_text("<p>p</p>", encoding="utf-8")
        code, out, err = _run(["--html", str(html)], BoomHarness())
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        self.assertIn("chromium exploded", err)

    def test_success_payload_json_contract(self):
        # The success payload must be exactly ONE deterministic JSON line.
        html = self.tmp / "screen.html"
        html.write_text("<p>j</p>", encoding="utf-8")
        code, out, err = _run(["--html", str(html)], FakeHarness())
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\n"), 1)  # exactly one trailing newline
        payload = json.loads(out)
        self.assertEqual(payload["viewport"], {"width": 1440, "height": 900})
        self.assertIsInstance(payload["meta"], dict)


if __name__ == "__main__":
    unittest.main()
