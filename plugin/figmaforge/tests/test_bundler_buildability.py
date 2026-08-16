"""
Buildability audit for the web backends (Part 21 Task 1, S2 regression lock).

The canonical honesty-audit fixture's output for react_tailwind / vue / svelte
must BUILD with the real Vite toolchain AND RENDER in real chromium with zero
console/page errors.  Before the S2 fix, react and svelte crashed with a blank
page (``ReferenceError: ButtonCard is not defined``) and vue relied on the
accidental unknown-element fallback.

Skipped when chromium or npm are unavailable so suites stay green elsewhere
(render-test convention).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))
tests_dir = Path(__file__).resolve().parent
if str(tests_dir) not in sys.path:
    sys.path.insert(0, str(tests_dir))

from backends.react_tailwind import ReactTailwindBackend  # noqa: E402
from backends.svelte import SvelteBackend  # noqa: E402
from backends.vue import VueBackend  # noqa: E402
from test_backend_honesty_audit import canonical_fixture  # noqa: E402


def _chromium_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


_HAVE_TOOLS = _chromium_available() and shutil.which("npm") is not None


def _toolchain_tests_enabled() -> bool:
    """Return whether tests that may contact the npm registry are enabled."""
    return os.environ.get("FIGMAFORGE_RUN_TOOLCHAIN_TESTS") == "1"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _serve_and_probe(dist: Path) -> dict:
    """Serve dist on an ephemeral port, render, return errors + node presence."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
         "--directory", str(dist)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.8)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            console: list[str] = []
            page_errors: list[str] = []
            page.on("console", lambda m: console.append(m.text)
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/", timeout=15000)
            page.wait_for_load_state("networkidle", timeout=15000)
            page.evaluate("document.fonts.ready")
            comp = page.evaluate(
                "() => document.querySelector('[data-figma-id=\"comp:1\"]') !== null")
            inst = page.evaluate(
                "() => document.querySelector('[data-figma-id=\"inst:1\"]') !== null")
            browser.close()
            return {"console": console, "page_errors": page_errors,
                    "comp": comp, "inst": inst}
    finally:
        server.terminate()
        server.wait()


def _react_project(root: Path, tsx: str) -> None:
    _write(root / "package.json", json.dumps({
        "name": "audit-react", "private": True, "version": "0.0.0",
        "type": "module",
        "scripts": {"build": "vite build"},
        "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"},
        "devDependencies": {
            "@vitejs/plugin-react": "^4.3.4", "autoprefixer": "^10.4.20",
            "postcss": "^8.4.47", "tailwindcss": "^3.4.14", "vite": "^5.4.11",
        },
    }))
    _write(root / "vite.config.ts",
           "import { defineConfig } from 'vite';\nimport react from '@vitejs/plugin-react';\n"
           "export default defineConfig({ base: './', plugins: [react()], server: { host: '127.0.0.1' } });\n")
    _write(root / "postcss.config.js",
           "export default { plugins: { tailwindcss: {}, autoprefixer: {} } };\n")
    _write(root / "tailwind.config.js",
           "/** @type {import('tailwindcss').Config} */\n"
           "export default { content: ['./index.html', './src/**/*.{tsx,ts,jsx,js}'], "
           "theme: { extend: {} }, plugins: [] };\n")
    _write(root / "index.html",
           '<!doctype html><html lang="en"><head><meta charset="UTF-8" />'
           '<title>audit</title></head><body><div id="root"></div>'
           '<script type="module" src="/src/main.tsx"></script></body></html>\n')
    _write(root / "src/index.css", "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n")
    _write(root / "src/main.tsx",
           "import React from 'react';\nimport { createRoot } from 'react-dom/client';\n"
           "import './index.css';\nimport { Root } from './generated/Root';\n"
           "createRoot(document.getElementById('root')!).render(<Root />);\n")
    _write(root / "src/generated/Root.tsx", tsx)


def _vue_project(root: Path, vue_sfc: str) -> None:
    _write(root / "package.json", json.dumps({
        "name": "audit-vue", "private": True, "version": "0.0.0", "type": "module",
        "scripts": {"build": "vite build"},
        "dependencies": {"vue": "^3.5.13"},
        "devDependencies": {"@vitejs/plugin-vue": "^5.2.1", "vite": "^5.4.11"},
    }))
    _write(root / "vite.config.ts",
           "import { defineConfig } from 'vite';\nimport vue from '@vitejs/plugin-vue';\n"
           "export default defineConfig({ base: './', plugins: [vue()], server: { host: '127.0.0.1' } });\n")
    _write(root / "index.html",
           '<!doctype html><html lang="en"><head><meta charset="UTF-8" />'
           '<title>audit</title></head><body><div id="app"></div>'
           '<script type="module" src="/src/main.ts"></script></body></html>\n')
    _write(root / "src/main.ts",
           "import { createApp } from 'vue';\nimport Root from '../Root.vue';\n"
           "createApp(Root).mount('#app');\n")
    _write(root / "Root.vue", vue_sfc)


def _svelte_project(root: Path, svelte_sfc: str) -> None:
    _write(root / "package.json", json.dumps({
        "name": "audit-svelte", "private": True, "version": "0.0.0", "type": "module",
        "scripts": {"build": "vite build"},
        "dependencies": {"svelte": "^5.16.0"},
        "devDependencies": {"@sveltejs/vite-plugin-svelte": "^4.0.4", "vite": "^5.4.11"},
    }))
    _write(root / "vite.config.ts",
           "import { defineConfig } from 'vite';\nimport { svelte } from '@sveltejs/vite-plugin-svelte';\n"
           "export default defineConfig({ base: './', plugins: [svelte()], server: { host: '127.0.0.1' } });\n")
    _write(root / "index.html",
           '<!doctype html><html lang="en"><head><meta charset="UTF-8" />'
           '<title>audit</title></head><body><div id="app"></div>'
           '<script type="module" src="/src/main.ts"></script></body></html>\n')
    _write(root / "src/main.ts",
           "import { mount } from 'svelte';\nimport Root from '../Root.svelte';\n"
           "const app = mount(Root, { target: document.getElementById('app')! });\n"
           "export default app;\n")
    _write(root / "Root.svelte", svelte_sfc)


def _build(root: Path) -> None:
    try:
        install_args = ["npm", "install", "--no-audit", "--no-fund"]
        if os.environ.get("FIGMAFORGE_NPM_OFFLINE") == "1":
            install_args.append("--offline")
        result = subprocess.run(
            install_args,
            cwd=root, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            "npm install timed out after 60s; check registry access or provide a cached/offline environment"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no npm output"
        raise AssertionError(
            f"npm install failed with exit code {result.returncode}:\n{detail}"
        )
    # npm 11 blocks esbuild's postinstall; approve it when supported (S4).
    approve = subprocess.run(
        ["npm", "approve-scripts", "esbuild"], cwd=root,
        capture_output=True, text=True, timeout=60,
    )
    build = subprocess.run(
        ["npm", "run", "build"], cwd=root, capture_output=True, text=True,
        timeout=300,
    )
    if build.returncode != 0:
        raise AssertionError(
            f"vite build failed (npm install rc={result.returncode}, "
            f"approve rc={approve.returncode}):\n{build.stdout}\n{build.stderr}"
        )


class TestBuildDependencyFailure(unittest.TestCase):
    def test_npm_install_failure_stops_before_build(self):
        failed_install = subprocess.CompletedProcess(
            args=["npm", "install"], returncode=1,
            stdout="", stderr="npm ERR! registry unavailable",
        )

        with patch("test_bundler_buildability.subprocess.run", return_value=failed_install) as run:
            with self.assertRaisesRegex(AssertionError, "npm install failed"):
                _build(Path("/tmp/figmaforge-test-build"))

        self.assertEqual(run.call_count, 1)

    def test_npm_install_timeout_is_reported_as_dependency_failure(self):
        timeout = subprocess.TimeoutExpired(["npm", "install"], 60)
        with patch("test_bundler_buildability.subprocess.run", side_effect=timeout):
            with self.assertRaisesRegex(AssertionError, "npm install timed out"):
                _build(Path("/tmp/figmaforge-test-build"))


class TestToolchainGate(unittest.TestCase):
    def test_real_toolchain_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_toolchain_tests_enabled())
        with patch.dict(os.environ, {"FIGMAFORGE_RUN_TOOLCHAIN_TESTS": "1"}, clear=True):
            self.assertTrue(_toolchain_tests_enabled())


@unittest.skipUnless(
    _HAVE_TOOLS and _toolchain_tests_enabled(),
    "set FIGMAFORGE_RUN_TOOLCHAIN_TESTS=1 with npm and chromium available",
)
class TestWebBackendBuildability(unittest.TestCase):
    """Canonical web-backend output must build AND render with zero errors."""

    def test_canonical_output_builds_and_renders(self):
        doc, plan, resolution = canonical_fixture()
        outputs = {
            "react": (ReactTailwindBackend(), _react_project),
            "vue": (VueBackend(), _vue_project),
            "svelte": (SvelteBackend(), _svelte_project),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for label, (backend, scaffold) in outputs.items():
                out = backend.generate(doc, plan, resolution=resolution, viewport=1440.0)
                files = {f.path: f.content for f in out.files}
                root = Path(tmp) / label
                with self.subTest(backend=label):
                    if label == "react":
                        scaffold(root, files["Root.tsx"])
                    elif label == "vue":
                        scaffold(root, files["Root.vue"])
                    else:
                        scaffold(root, files["Root.svelte"])
                    _build(root)
                    probe = _serve_and_probe(root / "dist")
                    self.assertEqual(
                        probe["page_errors"], [],
                        f"{label}: page errors: {probe['page_errors']}",
                    )
                    self.assertEqual(
                        probe["console"], [],
                        f"{label}: console errors: {probe['console']}",
                    )
                    self.assertTrue(
                        probe["comp"] and probe["inst"],
                        f"{label}: component/instance nodes did not render: {probe}",
                    )


if __name__ == "__main__":
    unittest.main()
