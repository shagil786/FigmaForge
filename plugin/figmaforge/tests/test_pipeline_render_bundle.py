"""
``pipeline.py render --bundle`` (Part 21 Task 3).

Scaffold → build → serve → screenshot in ONE atomic unit: the harness's
scaffold runs for real (pure file writes), the build is injectable
(``builder=``), the screenshot is injectable (``screenshot_fn=``), and the
serve step is a real ephemeral-port static server (the URL is never part
of the deterministic stdout JSON).  Exit codes mirror the family: 2 usage /
unknown backend, 4 missing dir / unreadable asset manifest, 1 build or
browser failure — never a traceback.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from bundler_harness import BundleBuildError  # noqa: E402
from scripts.pipeline import render_main  # noqa: E402


class _FakeResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeBuilder:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.cwd = None

    def __call__(self, out_dir):
        self.cwd = Path(out_dir)
        if self.exc is not None:
            raise self.exc
        if getattr(self.result, "returncode", 0) == 0:
            # A real vite build writes dist/; the fake mirrors that so the
            # static server has something to serve.
            dist = Path(out_dir) / "dist"
            dist.mkdir(parents=True, exist_ok=True)
            (dist / "index.html").write_text("<html></html>", encoding="utf-8")
        return self.result


class FakeScreenshot:
    def __init__(self):
        self.calls = []

    def __call__(self, url, viewport, out_png):
        self.calls.append({
            "url": url,
            "viewport": viewport,
            "png": str(out_png),
        })
        Path(out_png).parent.mkdir(parents=True, exist_ok=True)
        Path(out_png).write_bytes(b"\x89PNG-fake")


def _run(argv, builder, screenshot):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = render_main(argv, builder=builder, screenshot_fn=screenshot)
    return code, stdout.getvalue(), stderr.getvalue()


class _BundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ff-bundle-test-"))
        self.generated = self.tmp / "generated"
        self.generated.mkdir(parents=True, exist_ok=True)
        (self.generated / "Root.tsx").write_text(
            "export function Root() { return <div />; }", encoding="utf-8",
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _parse(self, out):
        return json.loads(out.strip().splitlines()[-1])


class TestBundleSuccess(_BundleTest):
    def test_success_emits_one_json_line(self):
        builder = FakeBuilder(_FakeResult(0))
        shot = FakeScreenshot()
        code, out, err = _run(
            ["--bundle", "--backend", "react_tailwind", "--dir", str(self.generated)],
            builder, shot,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        payload = self._parse(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "bundle")
        self.assertEqual(payload["backend"], "react_tailwind")
        self.assertTrue(payload["build_ok"])
        self.assertEqual(payload["screens"], [
            {"component": "Root", "png": "screens/Root.png", "html": "Root.html"},
        ])

    def test_screenshot_called_with_url_viewport_png(self):
        builder = FakeBuilder(_FakeResult(0))
        shot = FakeScreenshot()
        _run(
            ["--bundle", "--backend", "react_tailwind", "--dir", str(self.generated)],
            builder, shot,
        )
        self.assertEqual(len(shot.calls), 1)
        call = shot.calls[0]
        self.assertTrue(call["url"].startswith("http://127.0.0.1:"), call["url"])
        self.assertTrue(call["url"].endswith("/Root.html"), call["url"])
        self.assertEqual(call["viewport"], {"width": 1440, "height": 900})
        self.assertTrue(call["png"].endswith("screens/Root.png"), call["png"])
        # The screenshot file really exists (fake wrote it).
        self.assertTrue(Path(call["png"]).is_file())

    def test_builder_called_with_bundle_dir(self):
        builder = FakeBuilder(_FakeResult(0))
        _run(
            ["--bundle", "--backend", "react_tailwind", "--dir", str(self.generated),
             "--out", str(self.tmp / "out")],
            builder, FakeScreenshot(),
        )
        self.assertEqual(builder.cwd, self.tmp / "out" / "bundle")


class TestBundleViewport(_BundleTest):
    def test_viewport_threads_to_screenshot(self):
        shot = FakeScreenshot()
        _run(
            ["--bundle", "--backend", "react_tailwind", "--dir", str(self.generated),
             "--viewport", "800x600"],
            FakeBuilder(_FakeResult(0)), shot,
        )
        self.assertEqual(shot.calls[0]["viewport"], {"width": 800, "height": 600})

    def test_bad_viewport_exits_2(self):
        code, out, err = _run(
            ["--bundle", "--backend", "react_tailwind", "--dir", str(self.generated),
             "--viewport", "nope"],
            FakeBuilder(_FakeResult(0)), FakeScreenshot(),
        )
        self.assertEqual(code, 2)
        self.assertIn("viewport", err)


class TestBundleErrors(_BundleTest):
    def test_unknown_backend_exits_2(self):
        code, out, err = _run(
            ["--bundle", "--backend", "flutter", "--dir", str(self.generated)],
            FakeBuilder(_FakeResult(0)), FakeScreenshot(),
        )
        self.assertEqual(code, 2)
        self.assertIn("no bundler harness", err)
        self.assertEqual(out, "")

    def test_missing_backend_exits_2(self):
        code, _, err = _run(
            ["--bundle", "--dir", str(self.generated)],
            FakeBuilder(_FakeResult(0)), FakeScreenshot(),
        )
        self.assertEqual(code, 2)

    def test_missing_dir_exits_4(self):
        code, _, err = _run(
            ["--bundle", "--backend", "react_tailwind", "--dir",
             str(self.tmp / "absent")],
            FakeBuilder(_FakeResult(0)), FakeScreenshot(),
        )
        self.assertEqual(code, 4)
        self.assertIn("generated dir missing", err)

    def test_unreadable_assets_exits_4(self):
        code, _, err = _run(
            ["--bundle", "--backend", "react_tailwind", "--dir", str(self.generated),
             "--assets", str(self.tmp / "nope.json")],
            FakeBuilder(_FakeResult(0)), FakeScreenshot(),
        )
        self.assertEqual(code, 4)
        self.assertIn("asset manifest", err)

    def test_build_failure_exits_1_with_stderr_no_traceback(self):
        builder = FakeBuilder(_FakeResult(1, stdout="building...",
                                          stderr="boom: bad class"))
        code, out, err = _run(
            ["--bundle", "--backend", "react_tailwind", "--dir", str(self.generated)],
            builder, FakeScreenshot(),
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("boom: bad class", err)
        self.assertNotIn("Traceback", err)

    def test_builder_raise_exits_1(self):
        builder = FakeBuilder(exc=BundleBuildError("boom"))
        code, out, err = _run(
            ["--bundle", "--backend", "react_tailwind", "--dir", str(self.generated)],
            builder, FakeScreenshot(),
        )
        self.assertEqual(code, 1)
        self.assertIn("boom", err)
        self.assertNotIn("Traceback", err)

    def test_mode_exclusivity_exits_2(self):
        html = self.tmp / "x.html"
        html.write_text("<html></html>", encoding="utf-8")
        code, _, err = _run(
            ["--bundle", "--backend", "react_tailwind", "--dir", str(self.generated),
             "--html", str(html)],
            FakeBuilder(_FakeResult(0)), FakeScreenshot(),
        )
        self.assertEqual(code, 2)


class TestBundleDeterminism(_BundleTest):
    def test_identical_runs_byte_identical_stdout(self):
        def run():
            return _run(
                ["--bundle", "--backend", "react_tailwind", "--dir", str(self.generated)],
                FakeBuilder(_FakeResult(0)), FakeScreenshot(),
            )[1]
        first = run()
        second = run()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
