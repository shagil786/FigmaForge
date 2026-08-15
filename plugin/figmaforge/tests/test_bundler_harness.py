"""
Bundler harness module (Part 21 Task 2).

A deterministic Vite project scaffold for the web-framework backends
(react_tailwind / vue / svelte): per-framework entry + config + pinned
deps, generated components copied in, resolved assets copied and their
``url(...)`` references rewritten.  ``build`` runs an injectable builder
and raises a typed ``BundleBuildError`` on failure (real npm never runs
in these unit tests).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from bundler_harness import (  # noqa: E402
    BundleBuildError,
    BundleScaffoldError,
    build,
    scaffold,
)


class _FakeResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeBuilder:
    def __init__(self, result):
        self.result = result
        self.cwd = None

    def __call__(self, out_dir):
        self.cwd = out_dir
        return self.result


def _write(dir_path: Path, rel: str, content) -> Path:
    p = dir_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


def _generated(tmp: Path, files) -> Path:
    d = tmp / "generated"
    for rel, content in files.items():
        _write(d, rel, content)
    return d


class TestScaffoldStructure(unittest.TestCase):

    def test_react_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            generated = _generated(tmp, {"Root.tsx": "export function Root() { return <div />; }"})
            out = tmp / "out"
            files = scaffold("react_tailwind", generated, out)

            self.assertIn("package.json", files)
            pkg = json.loads((out / "package.json").read_text())
            self.assertEqual(pkg["scripts"]["build"], "vite build")
            self.assertIn("react", pkg["dependencies"])
            self.assertIn("@vitejs/plugin-react", pkg["devDependencies"])
            self.assertIn("tailwindcss", pkg["devDependencies"])
            self.assertEqual(pkg["devDependencies"]["vite"], "5.4.11")  # exact pin

            vite_config = (out / "vite.config.ts").read_text()
            self.assertIn("react()", vite_config)
            self.assertIn('base: "./"', vite_config)
            self.assertIn("Root: 'Root.html'", vite_config)

            html = (out / "Root.html").read_text()
            self.assertIn("/src/main/Root.tsx", html)

            entry = (out / "src/main/Root.tsx").read_text()
            self.assertIn("from '../generated/Root'", entry)
            self.assertIn("createRoot", entry)

            copied = (out / "src/generated/Root.tsx").read_text()
            self.assertIn("export function Root", copied)

    def test_vue_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            generated = _generated(tmp, {"Root.vue": "<template><div /></template>"})
            out = tmp / "out"
            files = scaffold("vue", generated, out)

            self.assertIn("package.json", files)
            pkg = json.loads((out / "package.json").read_text())
            self.assertIn("vue", pkg["dependencies"])
            self.assertIn("@vitejs/plugin-vue", pkg["devDependencies"])
            self.assertIn("vue()", (out / "vite.config.ts").read_text())
            entry = (out / "src/main/Root.ts").read_text()
            self.assertIn("from '../generated/Root.vue'", entry)
            self.assertIn("createApp", entry)

    def test_svelte_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            generated = _generated(tmp, {"Root.svelte": "<div>hi</div>"})
            out = tmp / "out"
            files = scaffold("svelte", generated, out)

            self.assertIn("package.json", files)
            pkg = json.loads((out / "package.json").read_text())
            self.assertIn("svelte", pkg["dependencies"])
            self.assertIn("@sveltejs/vite-plugin-svelte", pkg["devDependencies"])
            self.assertIn("svelte()", (out / "vite.config.ts").read_text())
            entry = (out / "src/main/Root.ts").read_text()
            self.assertIn("from '../generated/Root.svelte'", entry)
            self.assertIn("mount", entry)

    def test_unknown_backend_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            generated = _generated(tmp, {"Root.tsx": "x"})
            with self.assertRaises(Exception):
                scaffold("flutter", generated, tmp / "out")


class TestAssetRewrite(unittest.TestCase):
    def test_url_and_tailwind_arbitrary_url_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            generated = tmp / "generated"
            asset_dir = tmp / "store"
            asset = _write(asset_dir, "abc123.png", b"\x89PNG-fake")
            url = str(asset)
            _write(generated, "Root.tsx",
                   f"className=\"bg-[url({url})] bg-cover\"\n"
                   f"style={{ backgroundImage: 'url({url})' }}")
            assets = {"node:1": {"path": url}}
            out = tmp / "out"
            scaffold("react_tailwind", generated, out, assets=assets)

            copied = (out / "src/generated/Root.tsx").read_text()
            self.assertIn("bg-[url(./assets/abc123.png)]", copied)
            self.assertIn("url(./assets/abc123.png)", copied)
            self.assertNotIn(url, copied)
            self.assertEqual(
                (out / "src/assets/abc123.png").read_bytes(),
                b"\x89PNG-fake",
            )

    def test_shared_asset_copied_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            generated = tmp / "generated"
            asset = _write(tmp / "store", "shared.png", b"bytes")
            _write(generated, "Root.tsx", f"url({asset})")
            _write(generated, "Other.tsx", f"bg-[url({asset})]")
            assets = {"a:1": {"path": str(asset)}, "b:1": {"path": str(asset)}}
            out = tmp / "out"
            scaffold("react_tailwind", generated, out, assets=assets)
            self.assertEqual(
                len(list((out / "src/assets").iterdir())), 1,
            )

    def test_missing_asset_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            generated = _generated(tmp, {"Root.tsx": "url(/nope/missing.png)"})
            assets = {"n:1": {"path": "/nope/missing.png"}}
            with self.assertRaises(BundleScaffoldError):
                scaffold("react_tailwind", generated, tmp / "out", assets=assets)


class TestDeterminism(unittest.TestCase):
    def test_identical_scaffolds_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            generated = tmp / "generated"
            _write(generated, "Root.tsx", "export function Root() { return <div />; }")
            _write(generated, "Other.tsx", "export function Other() { return <p />; }")

            def run():
                out = tmp / "out-x"
                scaffold("react_tailwind", generated, out)
                return {
                    p.relative_to(out).as_posix(): p.read_bytes()
                    for p in sorted(out.rglob("*")) if p.is_file()
                }

            a = run()
            # second run into a fresh dir
            scaffold("react_tailwind", generated, tmp / "out-y")
            b = {
                p.relative_to(tmp / "out-y").as_posix(): p.read_bytes()
                for p in sorted((tmp / "out-y").rglob("*")) if p.is_file()
            }
            self.assertEqual(set(a), set(b))
            for rel, content in a.items():
                self.assertEqual(content, b[rel], f"drifted: {rel}")


class TestCollision(unittest.TestCase):
    # Same-named screens are overwritten at the backend (one file per name),
    # so file-level duplicate entry names are unreachable — the meaningful
    # namespace collision is a component file whose name is not a valid JS
    # identifier (the harness must import it in the entry module).
    def test_invalid_entry_name_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            generated = _generated(tmp, {
                "My Screen.tsx": "export function x() {}",
            })
            with self.assertRaises(BundleScaffoldError):
                scaffold("react_tailwind", generated, tmp / "out")


class TestBuild(unittest.TestCase):
    def test_builder_called_with_out_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = _FakeBuilder(_FakeResult(0))
            build(tmp, builder=fake)
            self.assertEqual(fake.cwd, Path(tmp))

    def test_failure_raises_bundle_build_error_with_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = _FakeBuilder(_FakeResult(1, stdout="building...", stderr="boom: bad class"))
            with self.assertRaises(BundleBuildError) as ctx:
                build(tmp, builder=fake)
            self.assertIn("boom: bad class", str(ctx.exception))
            self.assertIn("building...", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
