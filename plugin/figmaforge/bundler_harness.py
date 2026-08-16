"""
Vite bundler harness (Part 21).

A deterministic Vite project scaffold for the web-framework backends so
``figmaforge run`` can render their generated output (which has no
standalone HTML): the harness writes a per-framework project (entry,
``index.html`` per screen, ``vite.config.ts``, pinned ``package.json``),
copies the generated components in, copies resolved assets and rewrites
their ``url(...)`` references, then builds with Vite.

Design points (from the Part 21 spec + Task 0 spike):

- One HTML entry per generated component (multi-page build) so the render
  stage can screenshot each screen separately — mirrors html_css, which
  screenshots one file at a time.
- Exact dependency pins (probe-validated versions) for offline,
  reproducible builds; no network at runtime beyond npm's cache.
- Tailwind v3.4 + PostCSS for react_tailwind (the backend emits a
  v3-style ``tailwind.config.figmaforge.js`` extension; v4 would ignore it).
- ``url(<resolved store path>)`` / ``bg-[url(<path>)]`` references are
  rewritten to ``./assets/<basename>`` and the bytes copied into
  ``src/assets/`` (Part 18 asset contract).
- Svelte 5 (``{#snippet}`` fallbacks from Task 1 need it).
- The real npm build is injectable (``builder=``) — unit tests use a fake;
  failures raise a typed ``BundleBuildError`` carrying the real stderr,
  with an esbuild hint for npm 11's blocked postinstall (spike finding S4).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


class BundleHarnessError(RuntimeError):
    """Base error for the bundler harness."""


class BundleScaffoldError(BundleHarnessError):
    """Invalid input for scaffolding (unknown backend, name collisions, ...)."""


class BundleBuildError(BundleHarnessError):
    """The real bundler build failed (vite/npm stderr preserved)."""


# ---------------------------------------------------------------------------
# Per-framework specs — exact pins (probe-validated, Task 0)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BundleSpec:
    backend: str
    extension: str                      # generated component extension
    entry_extension: str                # entry module extension ("tsx" | "ts")
    entry_header: str                   # imports for the entry module
    entry_mount: str                    # mount expression, {name} placeholder
    vite_plugin: str                    # plugin import lines
    vite_plugin_name: str               # plugin function name (react / vue / svelte)
    dependencies: Dict[str, str] = field(default_factory=dict)
    dev_dependencies: Dict[str, str] = field(default_factory=dict)
    tailwind: bool = False              # write index.css + postcss + tailwind config


SPECS: Dict[str, BundleSpec] = {
    "react_tailwind": BundleSpec(
        backend="react_tailwind",
        extension=".tsx",
        entry_extension="tsx",
        entry_header=(
            "import React from 'react';\n"
            "import {{ createRoot }} from 'react-dom/client';\n"
            "import {name} from '../generated/{name}';\n"
        ),
        entry_mount=(
            "createRoot(document.getElementById('root')!).render(<{name} />);\n"
        ),
        vite_plugin=(
            "import { defineConfig } from 'vite';\n"
            "import react from '@vitejs/plugin-react';\n"
        ),
        vite_plugin_name="react",
        dependencies={
            "react": "18.3.1",
            "react-dom": "18.3.1",
        },
        dev_dependencies={
            "@vitejs/plugin-react": "4.3.4",
            "autoprefixer": "10.4.20",
            "postcss": "8.4.47",
            "tailwindcss": "3.4.14",
            "vite": "5.4.11",
        },
        tailwind=True,
    ),
    "vue": BundleSpec(
        backend="vue",
        extension=".vue",
        entry_extension="ts",
        entry_header=(
            "import {{ createApp }} from 'vue';\n"
            "import {name} from '../generated/{name}.vue';\n"
        ),
        entry_mount="createApp({name}).mount('#app');\n",
        vite_plugin=(
            "import { defineConfig } from 'vite';\n"
            "import vue from '@vitejs/plugin-vue';\n"
        ),
        vite_plugin_name="vue",
        dependencies={"vue": "3.5.13"},
        dev_dependencies={
            "@vitejs/plugin-vue": "5.2.1",
            "vite": "5.4.11",
        },
    ),
    "svelte": BundleSpec(
        backend="svelte",
        extension=".svelte",
        entry_extension="ts",
        entry_header=(
            "import {{ mount }} from 'svelte';\n"
            "import {name} from '../generated/{name}.svelte';\n"
        ),
        entry_mount=(
            "const app = mount({name}, {{ target: "
            "document.getElementById('app')! }});\nexport default app;\n"
        ),
        vite_plugin=(
            "import { defineConfig } from 'vite';\n"
            "import { svelte } from '@sveltejs/vite-plugin-svelte';\n"
        ),
        vite_plugin_name="svelte",
        dependencies={"svelte": "5.16.0"},
        dev_dependencies={
            "@sveltejs/vite-plugin-svelte": "4.0.4",
            "vite": "5.4.11",
        },
    ),
}

_JS_IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def scaffold(
    backend: str,
    generated_dir: Path,
    out_dir: Path,
    assets: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    """Write the Vite project for ``backend`` into ``out_dir``.

    ``generated_dir`` holds the backend's output (one component file per
    screen, plus any sidecar config).  ``assets`` maps node id → ``{"path":
    <resolved asset file>}`` (Part 18 contract); each asset is copied into
    ``src/assets/`` and its ``url(...)`` references rewritten to
    ``./assets/<basename>`` in the copied generated files.

    Returns the relative paths of all written files (sorted).
    """
    spec = SPECS.get(backend)
    if spec is None:
        raise BundleScaffoldError(
            f"no bundler harness for backend {backend!r} — available: "
            + ", ".join(sorted(SPECS))
        )

    generated_dir = Path(generated_dir)
    out_dir = Path(out_dir)
    if not generated_dir.is_dir():
        raise BundleScaffoldError(f"generated dir missing: {generated_dir}")

    components = sorted(
        p.name for p in generated_dir.iterdir()
        if p.is_file() and p.name.endswith(spec.extension)
    )
    if not components:
        raise BundleScaffoldError(
            f"no {spec.extension} components in {generated_dir}"
        )

    # Entry names must be unique valid JS identifiers (one HTML per screen).
    names: List[str] = []
    for comp in components:
        name = comp[: -len(spec.extension)]
        if not _JS_IDENT.fullmatch(name):
            raise BundleScaffoldError(
                f"component file {comp!r} has invalid JS identifier {name!r}"
            )
        if name in names:
            raise BundleScaffoldError(
                f"duplicate entry name {name!r} (from {comp!r}) — "
                "screen names must be unique"
            )
        names.append(name)

    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[str] = []

    def _write(rel: str, content: str) -> None:
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        written.append(rel)

    # --- package.json (exact pins) -------------------------------------
    pkg = {
        "name": f"figmaforge-{spec.backend}",
        "private": True,
        "version": "0.0.0",
        "type": "module",
        "scripts": {"build": "vite build"},
        "dependencies": dict(spec.dependencies),
        "devDependencies": dict(spec.dev_dependencies),
    }
    _write("package.json", json.dumps(pkg, indent=2) + "\n")

    # --- vite.config.ts (multi-page build) -----------------------------
    inputs = ", ".join(f"      {name}: '{name}.html'" for name in names)
    vite_config = (
        f"{spec.vite_plugin}\n"
        "export default defineConfig({\n"
        '  base: "./",\n'
        f"  plugins: [{spec.vite_plugin_name}()],\n"
        "  build: {\n"
        "    rollupOptions: {\n"
        "      input: {\n"
        f"{inputs},\n"
        "      },\n"
        "    },\n"
        "  },\n"
        '  server: { host: "127.0.0.1" },\n'
        '  preview: { host: "127.0.0.1" },\n'
        "});\n"
    )
    _write("vite.config.ts", vite_config)

    # --- tailwind (react only) -----------------------------------------
    if spec.tailwind:
        _write("postcss.config.js",
               "export default { plugins: { tailwindcss: {}, autoprefixer: {} } };\n")
        _write("tailwind.config.js",
               "/** @type {import('tailwindcss').Config} */\n"
               "export default { content: ['./index.html', './src/**/*.{tsx,ts,jsx,js}'], "
               "theme: { extend: {} }, plugins: [] };\n")
        _write("src/index.css",
               "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n")

    # --- copy generated files + rewrite assets -------------------------
    asset_map: Dict[str, Path] = {}
    for node_id, info in (assets or {}).items():
        path = (info or {}).get("path")
        if not path:
            continue
        src = Path(path)
        if not src.is_file():
            raise BundleScaffoldError(
                f"asset for node {node_id!r} missing: {path}"
            )
        basename = src.name
        if basename not in asset_map:
            asset_map[basename] = src

    for comp in components:
        src = generated_dir / comp
        rel = f"src/generated/{comp}"
        content = src.read_text(encoding="utf-8")
        for basename, asset_path in asset_map.items():
            content = content.replace(str(asset_path), f"./assets/{basename}")
        _write(rel, content)

    for basename, asset_path in asset_map.items():
        dst = out_dir / "src" / "assets" / basename
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(asset_path, dst)
        written.append(f"src/assets/{basename}")

    # also copy any sidecar config (e.g. the tailwind token extension)
    for extra in sorted(generated_dir.iterdir()):
        if extra.is_file() and extra.name.endswith((".js", ".json", ".css")):
            dst = out_dir / "src" / "generated" / extra.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(extra.read_text(encoding="utf-8"), encoding="utf-8")
            written.append(f"src/generated/{extra.name}")

    # --- one index.html + entry per component --------------------------
    for comp, name in zip(components, names):
        entry_imports = spec.entry_header.format(name=name)
        entry = entry_imports + "\n" + spec.entry_mount.format(name=name)
        _write(f"src/main/{name}.{spec.entry_extension}", entry)
        script_src = f"/src/main/{name}.{spec.entry_extension}"
        # The same global reset as the reference render (``generate_render_html``
        # in web_common): Figma widths are border-box, so ``width: 1440px`` +
        # padding must NOT overflow (Part 21, Task 6 — without this, vue/svelte
        # screens rendered 56px wider than the reference and the SSIM compare
        # degraded to similarity 0).
        _write(name + ".html",
               "<!doctype html>\n<html lang=\"en\">\n<head>\n"
               "  <meta charset=\"UTF-8\" />\n"
               "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />\n"
               "  <title>{name}</title>\n"
               "  <style>* { margin: 0; padding: 0; box-sizing: border-box; }</style>\n"
               "</head>\n<body>\n"
               "  <div id=\"root\"></div>\n"
               "  <div id=\"app\"></div>\n"
               f"  <script type=\"module\" src=\"{script_src}\"></script>\n"
               "</body>\n</html>\n")

    return sorted(written)


Builder = Callable[[Path], Any]


def _npm_build(out_dir: Path) -> Any:
    """Real builder: install deps when missing, approve esbuild, build.

    Self-contained: a fresh scaffold has no ``node_modules``, so the real
    path installs the pinned deps first (npm's cache makes repeat installs
    fast).  npm 11 blocks esbuild's postinstall — approve it best-effort
    when supported (S4).  Returns the ``npm run build`` result, or the
    failed install result so ``build()`` surfaces the real error.
    """
    out_dir = Path(out_dir)
    install_timeout = float(os.environ.get("FIGMAFORGE_NPM_INSTALL_TIMEOUT", "60"))
    build_timeout = float(os.environ.get("FIGMAFORGE_NPM_BUILD_TIMEOUT", "300"))
    if install_timeout <= 0 or build_timeout <= 0:
        raise BundleBuildError("npm timeouts must be positive")

    if not (out_dir / "node_modules").is_dir():
        install_args = [
            "npm", "install", "--no-audit", "--no-fund",
            "--fetch-retries=0", "--fetch-timeout=30000",
        ]
        if os.environ.get("FIGMAFORGE_NPM_OFFLINE") == "1":
            install_args.append("--offline")
        try:
            install = subprocess.run(
                install_args,
                cwd=out_dir, capture_output=True, text=True, timeout=install_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise BundleBuildError(
                f"npm install timed out after {install_timeout:g}s; "
                "check registry access or use FIGMAFORGE_NPM_OFFLINE=1"
            ) from exc
        if install.returncode != 0:
            return install
    try:
        subprocess.run(
            ["npm", "approve-scripts", "esbuild"], cwd=out_dir,
            capture_output=True, text=True, timeout=min(60.0, build_timeout),
        )
        return subprocess.run(
            ["npm", "run", "build"], cwd=out_dir,
            capture_output=True, text=True, timeout=build_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BundleBuildError(
            f"npm run build timed out after {build_timeout:g}s"
        ) from exc


def build(out_dir: Path, builder: Optional[Builder] = None) -> None:
    """Run the bundler build; raise ``BundleBuildError`` on failure.

    The injected builder must return an object with ``returncode``,
    ``stdout`` and ``stderr`` (``subprocess.CompletedProcess`` shape).
    """
    runner = builder if builder is not None else _npm_build
    result = runner(Path(out_dir))
    if getattr(result, "returncode", 0) != 0:
        stderr = getattr(result, "stderr", "") or ""
        stdout = getattr(result, "stdout", "") or ""
        hint = ""
        if "esbuild" in (stderr + stdout):
            hint = (
                "\n\nHint: if the build failed while installing esbuild, npm 11 "
                "blocks its postinstall — run `npm approve-scripts esbuild` "
                "in the project before building (spike finding S4)."
            )
        raise BundleBuildError(
            f"vite build failed (exit {result.returncode})\n--- stdout ---\n"
            f"{stdout}\n--- stderr ---\n{stderr}{hint}"
        )


# ---------------------------------------------------------------------------
# Serve + screenshot (Part 21 Task 3)
# ---------------------------------------------------------------------------


def serve_built(dist_dir: Path) -> Tuple[str, Callable[[], None]]:
    """Serve a built ``dist`` on an ephemeral port; return ``(url, stop)``.

    A stdlib static server (python ``http.server``) on ``127.0.0.1`` with a
    readiness probe — deterministic, no npm at runtime.  The port is chosen
    at serve time and never hardcoded (concurrent runs are safe).
    """
    dist_dir = Path(dist_dir)
    if not dist_dir.is_dir():
        raise BundleHarnessError(f"built dist dir missing: {dist_dir}")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
         "--directory", str(dist_dir)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/"

    def _stop() -> None:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

    # Readiness probe (fail fast with a clean error, not a later timeout).
    for _ in range(20):
        if server.poll() is not None:
            _stop()
            raise BundleHarnessError("static server exited during startup")
        try:
            with urllib.request.urlopen(url, timeout=1):
                return url, _stop
        except Exception:  # noqa: BLE001 — server still warming up
            time.sleep(0.1)
    _stop()
    raise BundleHarnessError(f"static server did not become ready at {url}")


def screenshot_url(
    url: str,
    viewport: Dict[str, int],
    out_png: Path,
) -> None:
    """Screenshot a served URL with headless chromium (lazy playwright).

    Mirrors ``RenderHarness.render`` (Part 19): network-idle wait, fonts
    ready, full-page shot.  Missing playwright raises a typed error with the
    install hint — never a traceback.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BundleHarnessError(
            "playwright is required for browser rendering. Install it with: "
            "pip install playwright && playwright install chromium"
        ) from exc
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport=viewport, device_scale_factor=1)
                page.goto(url, timeout=15_000)
                page.wait_for_load_state("networkidle", timeout=15_000)
                page.evaluate("document.fonts.ready")
                page.screenshot(path=str(out_png), full_page=True)
            finally:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
    except BundleHarnessError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BundleHarnessError(
            f"browser rendering failed: {exc} — if chromium is not "
            "installed, run: playwright install chromium"
        ) from exc
