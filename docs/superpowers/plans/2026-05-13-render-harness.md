# Render Harness (Part 11) Implementation Plan

> **For agentic workers:** This plan is written for `superpowers:subagent-driven-development`.
> Execute it task-by-task with a fresh subagent per task; each task is a self-contained
> TDD cycle (write failing test → run and expect FAIL → minimal implementation → run and
> expect PASS → commit). Never batch tasks, never skip the failing-test step, and verify
> the exact expected output shown in each step before committing. Python commands run from
> `plugin/figmaforge` unless stated otherwise; TypeScript commands run from the repo root
> (`/Users/mdshagilnizami/code/projects/FigmaForge`). `pytest` is NOT installed — always
> use `python3 -m unittest`.

**Goal:** Give the FigmaForge repair loop real browser rendering by replacing the placeholder
`RenderHarness` with a Playwright implementation, generating real HTML from the Design IR,
wiring it into `RepairLoop` via the existing `RenderCallable` injection point, and fixing the
dead `tryBrowserRender` bridge in the TypeScript runtime.

**Architecture:** The Python plugin owns rendering: `render_harness.py` drives headless
chromium through `playwright.sync_api` (screenshot + `window.__figmaforge_meta` extraction),
`render_html.py` turns an `IRDocument` + `VStyle` map into the rendered HTML document, and
`render_adapter.py` builds the `RenderCallable` closure injected via `RepairLoop(render_fn=...)`
with zero changes to `repair_loop.py`. The TS runtime's `render_handler.ts` is fixed to pipe
its Python bridge script to `python3` via stdin and parse real output. Pixel diffing, Figma
baseline download, and backend implementations remain out of scope per the spec.

**Tech Stack:** Python 3 (stdlib + Playwright — a user-approved REQUIRED dependency, per
`docs/superpowers/specs/2026-05-13-render-harness-design.md`), TypeScript on Node.js stdlib
(child_process bridge to Python), `unittest` for Python tests, the custom runtime test
framework for TS tests, git/gh for the branch → PR → merge workflow.

**Approved spec:** `docs/superpowers/specs/2026-05-13-render-harness-design.md`

## Contract facts (verified against source at plan-writing time)

- `core/render_harness.py` (44 lines): `RenderResult` dataclass with
  `screenshot_path: Path`, `layout_metadata: Dict[str, Any]`;
  `RenderHarness(output_dir: Path)`; `render(content_html: str, viewport_spec: Dict[str, int],
  build_id: str) -> RenderResult` currently touches an empty PNG and returns
  `{"viewport": ..., "computed_styles": {}}`. Public API (names/signatures) is kept identical;
  the `layout_metadata` *shape* changes to the node-id-keyed `DiffEngine` shape (required by
  the spec so the repair loop can consume it).
- `core/repair_loop.py`: `RenderCallable` protocol —
  `(plan: LayoutPlan, styles: Dict[str, VStyle], document: IRDocument, iteration: int) -> tuple`
  returning `(render_meta: Dict, screenshot_path: str)`. `_default_render` is synthetic.
  Injection point: `RepairLoop(render_fn=...)`. Loop internals are NOT modified.
- `core/diff_engine.py`: `DiffEngine.diff(plan, render_meta)` expects `render_meta` keyed by
  `node_id`, each entry `{x, y, width, height}` plus optional `styles` sub-dict whose
  `fontSize` is compared against `node.text.font_size`. IMPORTANT: every `plan.nodes()` entry
  (including the top-level screen node) missing from `render_meta` produces a
  `missing_in_render` mismatch.
- `runtime/src/core/render_handler.ts`: `generateFullHtml` emits `data-node-id` attrs + inline
  script populating `window.__figmaforge_meta`. `tryBrowserRender` (~lines 399–443) is dead:
  it builds a Playwright Python script string but calls `execFileAsync(ctx_pythonBin(), {...})`
  with no args/stdin and always returns `null`. `cmdRender` in `runtime/src/cli/main.ts` is
  the working reference (inline python via `execFileSync(config.pythonBin, ["-c", pyScript])`).
- Viewport keys: harness callers use `{w, h}` (see `tests/test_render_pipeline.py`); runtime
  config uses `{width, height}`. The harness normalizes both.
- `LayoutPlan.viewport` is a `float` (width only) — there is no height field; the adapter
  uses a default height of 900.
- Baseline suite state at plan time: `Ran 241 tests ... OK` (Python, from `plugin/figmaforge`),
  `100 passing, 0 failing (100 total)` (TS), `claude plugin validate --strict` passes.
- `playwright` is currently NOT installed in the dev environment; browser-dependent tests use
  `unittest.skipUnless` so suites stay green (skips are counted in the `Ran N tests` total).

---

## Task 1: Merge `feat/part-10-final-fixes-part-one` into `main` (PR workflow)

**Files:** None (git operations only).

The current branch `feat/part-10-final-fixes-part-one` contains the Part 10 final fixes and
the Part 11 design spec (`docs/superpowers/specs/2026-05-13-render-harness-design.md`,
commit `b0f7810`), one commit ahead of `origin/feat/part-10-final-fixes-part-one`. The
established workflow is branch → PR → merge (see `Merge pull request #8` in history).

- [ ] **Step 1: Confirm clean index and expected state**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git branch --show-current
git diff --cached --name-only
git status --short | head -20
```

Expected: branch is `feat/part-10-final-fixes-part-one`; `git diff --cached --name-only`
prints NOTHING (empty index). The working tree may show pre-existing unrelated changes in
`.gitignore` and `.qoder/repowiki/**` — leave them unstaged and uncommitted; never
`git add -A` in this plan.

- [ ] **Step 2: Verify `gh` CLI is available (stop if not)**

```bash
command -v gh && gh auth status
```

Expected: path to `gh` printed and `Logged in to github.com` output. **If `gh` is not
installed or not authenticated: STOP here and report to the leader — do not attempt a
manual merge.**

- [ ] **Step 3: Push the branch**

```bash
git push origin feat/part-10-final-fixes-part-one
```

Expected: push succeeds (includes commit `b0f7810 docs: add render harness (Part 11) design spec`).

- [ ] **Step 4: Create and merge the PR**

```bash
gh pr create --base main --head feat/part-10-final-fixes-part-one \
  --title "feat: Part 10 final fixes (part one) + Part 11 render harness spec" \
  --body "Part 10 final fixes plus the approved Part 11 render harness design spec (docs only)."
gh pr merge --merge --delete-branch
```

Expected: PR created and merged (the repo convention is merge commits, matching
`Merge pull request #8 ...` in history).

- [ ] **Step 5: Update local main**

```bash
git checkout main
git pull origin main
git log --oneline -3
```

Expected: `main` now contains the merge commit and the spec commit `b0f7810`. The unrelated
`.gitignore` / `.qoder/repowiki` working-tree changes carry over unstaged — leave them alone.

- [ ] **Step 6: Commit** — nothing to commit in this task.

---

## Task 2: Create the Part 11 feature branch off `main`

**Files:** None (git operations only).

- [ ] **Step 1: Create the branch**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git checkout -b feat/part-11-render-harness main
git branch --show-current
```

Expected: `feat/part-11-render-harness`.

- [ ] **Step 2: Commit** — nothing to commit in this task.

---

## Task 3: Real Playwright implementation of the Python render harness (TDD)

**Files:**
- Modify: `plugin/figmaforge/core/render_harness.py`
- Create: `plugin/figmaforge/tests/test_render_harness.py`
- Test: `plugin/figmaforge/tests/test_render_harness.py`

Design: `render()` lazily imports `playwright.sync_api` inside the method (so the module
imports cleanly without playwright), normalizes the viewport (`{w,h}` and `{width,height}`),
writes the HTML next to the screenshot, launches chromium, waits for `networkidle`, takes a
full-page screenshot, evaluates `window.__figmaforge_meta || {}`, and returns metadata keyed
by `data-node-id` — the exact `DiffEngine.diff(plan, render_meta)` shape. Missing playwright
or a browser failure raises `RenderHarnessError` naming the install command.

- [ ] **Step 1: Write the failing tests**

Create `plugin/figmaforge/tests/test_render_harness.py`:

```python
"""
Render Harness contract tests (Part 11).

Mocked-Playwright tests: no browser is launched. A fake ``playwright``
package is injected into ``sys.modules`` so the harness's sync-API contract
is verified deterministically.

Run:  python3 -m unittest tests.test_render_harness -v
"""

from __future__ import annotations

import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.render_harness import (
    PLAYWRIGHT_INSTALL_HINT,
    RenderHarness,
    RenderHarnessError,
    normalize_viewport,
)


class _FakePlaywright:
    """Installs a fake ``playwright.sync_api`` into ``sys.modules``."""

    def __init__(self, meta_payload):
        self.meta_payload = meta_payload
        self.page = mock.MagicMock(name="page")
        self.page.evaluate.return_value = meta_payload
        self.browser = mock.MagicMock(name="browser")
        self.browser.new_page.return_value = self.page
        self.p = mock.MagicMock(name="playwright_instance")
        self.p.chromium.launch.return_value = self.browser
        self.context = mock.MagicMock(name="sync_playwright_context")
        self.context.__enter__.return_value = self.p
        self.context.__exit__.return_value = False
        self.sync_playwright = mock.MagicMock(
            name="sync_playwright", return_value=self.context
        )

    def install(self, testcase: unittest.TestCase) -> None:
        module = types.ModuleType("playwright")
        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.sync_playwright = self.sync_playwright
        module.sync_api = sync_api
        patcher = mock.patch.dict(
            sys.modules, {"playwright": module, "playwright.sync_api": sync_api}
        )
        patcher.start()
        testcase.addCleanup(patcher.stop)


META_FIXTURE = {
    "node-a": {
        "x": 0,
        "y": 0,
        "width": 200,
        "height": 100,
        "styles": {
            "fontSize": 16,
            "color": "rgb(0, 0, 0)",
            "backgroundColor": "rgba(0, 0, 0, 0)",
            "padding": "0px",
            "margin": "0px",
        },
    }
}


class TestNormalizeViewport(unittest.TestCase):
    def test_wh_keys(self):
        self.assertEqual(normalize_viewport({"w": 320, "h": 640}),
                         {"width": 320, "height": 640})

    def test_width_height_keys(self):
        self.assertEqual(normalize_viewport({"width": 1440, "height": 900}),
                         {"width": 1440, "height": 900})

    def test_missing_keys_raises(self):
        with self.assertRaises(ValueError):
            normalize_viewport({"w": 320})

    def test_non_numeric_raises(self):
        with self.assertRaises(ValueError):
            normalize_viewport({"w": "wide", "h": 640})

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            normalize_viewport("320x640")


class TestRenderHarnessContract(unittest.TestCase):
    def setUp(self):
        self.out_dir = Path("/tmp/figmaforge_harness_test")
        shutil.rmtree(self.out_dir, ignore_errors=True)
        self.harness = RenderHarness(self.out_dir)

    def test_output_dir_created(self):
        self.assertTrue(self.out_dir.is_dir())

    def test_render_contract(self):
        fake = _FakePlaywright(META_FIXTURE)
        fake.install(self)

        result = self.harness.render("<html></html>", {"w": 1440, "h": 900}, "build1")

        fake.p.chromium.launch.assert_called_once_with()
        fake.browser.new_page.assert_called_once_with(
            viewport={"width": 1440, "height": 900}
        )
        fake.page.goto.assert_called_once_with((self.out_dir / "build1.html").as_uri())
        fake.page.wait_for_load_state.assert_called_once_with("networkidle")
        fake.page.screenshot.assert_called_once_with(
            path=str(self.out_dir / "build1.png"), full_page=True
        )
        fake.page.evaluate.assert_called_once_with("window.__figmaforge_meta || {}")
        fake.browser.close.assert_called_once_with()

        self.assertEqual(result.screenshot_path, self.out_dir / "build1.png")
        self.assertEqual(result.layout_metadata, META_FIXTURE)

    def test_render_writes_html_file(self):
        fake = _FakePlaywright({})
        fake.install(self)
        self.harness.render("<html><body>x</body></html>", {"w": 800, "h": 600}, "b2")
        self.assertEqual(
            (self.out_dir / "b2.html").read_text(encoding="utf-8"),
            "<html><body>x</body></html>",
        )

    def test_render_normalizes_width_height_keys(self):
        fake = _FakePlaywright({})
        fake.install(self)
        self.harness.render("<html></html>", {"width": 390, "height": 844}, "mobile")
        fake.browser.new_page.assert_called_once_with(
            viewport={"width": 390, "height": 844}
        )

    def test_render_non_dict_meta_coerced_to_empty(self):
        fake = _FakePlaywright(["not", "a", "dict"])
        fake.install(self)
        result = self.harness.render("<html></html>", {"w": 800, "h": 600}, "b3")
        self.assertEqual(result.layout_metadata, {})

    def test_missing_playwright_raises_clear_error(self):
        # A sys.modules entry set to None makes the import raise ImportError.
        with mock.patch.dict(
            sys.modules, {"playwright": None, "playwright.sync_api": None}
        ):
            with self.assertRaises(RenderHarnessError) as ctx:
                self.harness.render("<html></html>", {"w": 800, "h": 600}, "b4")
        self.assertEqual(str(ctx.exception), PLAYWRIGHT_INSTALL_HINT)
        self.assertIn("pip install playwright", str(ctx.exception))

    def test_browser_failure_raises_render_harness_error(self):
        fake = _FakePlaywright({})
        fake.install(self)
        fake.p.chromium.launch.side_effect = RuntimeError("Executable doesn't exist")
        with self.assertRaises(RenderHarnessError) as ctx:
            self.harness.render("<html></html>", {"w": 800, "h": 600}, "b5")
        self.assertIn("browser rendering failed", str(ctx.exception))
        self.assertIn("playwright install chromium", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and expect FAIL**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest tests.test_render_harness -v
```

Expected: FAIL with `ImportError: cannot import name 'PLAYWRIGHT_INSTALL_HINT' from
'core.render_harness'` (the current module has none of the new names).

- [ ] **Step 3: Minimal implementation**

Replace the ENTIRE contents of `plugin/figmaforge/core/render_harness.py` with:

```python
"""
Render Harness (Part 7; real Playwright implementation added in Part 11).

Deterministic browser-rendering harness using Playwright's sync API.

Playwright is a REQUIRED dependency of the render stage (user-approved
decision — see docs/superpowers/specs/2026-05-13-render-harness-design.md).
This module still imports cleanly when the ``playwright`` package is absent:
``RenderHarness.render`` raises :class:`RenderHarnessError` with a message
naming the install command instead of leaking an ``ImportError`` traceback.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


class RenderHarnessError(RuntimeError):
    """Raised when the harness cannot produce a browser render."""


PLAYWRIGHT_INSTALL_HINT = (
    "playwright is required for browser rendering. "
    "Install it with: pip install playwright && playwright install chromium"
)


def normalize_viewport(viewport_spec: Dict[str, Any]) -> Dict[str, int]:
    """Normalize a viewport spec to ``{"width": w, "height": h}``.

    Accepts both key forms found in the codebase:

    - ``{"w": ..., "h": ...}`` — used by harness callers (Part 7 tests).
    - ``{"width": ..., "height": ...}`` — used by the runtime config.

    Raises ``ValueError`` when the spec is not a dict with numeric
    dimensions.
    """
    if not isinstance(viewport_spec, dict):
        raise ValueError(
            f"viewport_spec must be a dict, got {type(viewport_spec).__name__}"
        )
    width = viewport_spec.get("width", viewport_spec.get("w"))
    height = viewport_spec.get("height", viewport_spec.get("h"))
    if width is None or height is None:
        raise ValueError(
            "viewport_spec must contain {'w', 'h'} or {'width', 'height'}, "
            f"got keys {sorted(viewport_spec.keys())}"
        )
    try:
        return {"width": int(width), "height": int(height)}
    except (TypeError, ValueError):
        raise ValueError(
            f"viewport dimensions must be numeric, got {viewport_spec!r}"
        ) from None


@dataclass
class RenderResult:
    screenshot_path: Path
    layout_metadata: Dict[str, Any]


class RenderHarness:
    """Wrapper to interact with a Playwright rendering context."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render(self, content_html: str, viewport_spec: Dict[str, int], build_id: str) -> RenderResult:
        """
        Renders the provided HTML to a screenshot and extracts layout metadata.

        Writes ``content_html`` next to the screenshot, opens it in headless
        chromium at the normalized viewport, waits for ``networkidle``, takes
        a full-page screenshot, and evaluates ``window.__figmaforge_meta``
        (populated by the inline script emitted by
        ``core.render_html.generate_render_html``).

        ``layout_metadata`` is keyed by ``data-node-id``:
        ``{node_id: {"x", "y", "width", "height", "styles": {"fontSize",
        "color", "backgroundColor", "padding", "margin"}}}`` — the exact
        shape ``DiffEngine.diff(plan, render_meta)`` consumes.
        """
        viewport = normalize_viewport(viewport_spec)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RenderHarnessError(PLAYWRIGHT_INSTALL_HINT) from exc

        html_path = self.output_dir / f"{build_id}.html"
        html_path.write_text(content_html, encoding="utf-8")
        screenshot_path = self.output_dir / f"{build_id}.png"

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page(viewport=viewport)
                    page.goto(html_path.as_uri())
                    page.wait_for_load_state("networkidle")
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    meta = page.evaluate("window.__figmaforge_meta || {}")
                finally:
                    browser.close()
        except RenderHarnessError:
            raise
        except Exception as exc:
            raise RenderHarnessError(
                f"browser rendering failed: {exc} — if chromium is not "
                "installed, run: playwright install chromium"
            ) from exc

        if not isinstance(meta, dict):
            meta = {}

        return RenderResult(
            screenshot_path=screenshot_path,
            layout_metadata=meta,
        )
```

- [ ] **Step 4: Run the tests and expect PASS**

```bash
python3 -m unittest tests.test_render_harness -v
```

Expected: `Ran 12 tests ... OK`.

- [ ] **Step 5: Guard the pre-existing placeholder test**

`tests/test_render_pipeline.py::test_harness_determinism` asserts the OLD placeholder
behavior (a touched empty PNG) and would fail without a browser. Replace the ENTIRE
contents of `plugin/figmaforge/tests/test_render_pipeline.py` with:

```python
#!/usr/bin/env python3
"""
Rendering Pipeline Tests (Part 7; browser render guarded in Part 11).
"""
import shutil
import unittest
from pathlib import Path

from core.asset_manager import AssetManager
from core.render_harness import RenderHarness


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


CHROMIUM_AVAILABLE = _chromium_available()


class TestRenderPipeline(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("/tmp/figmaforge_test")
        shutil.rmtree(self.test_dir, ignore_errors=True)
        self.asset_dir = self.test_dir / "assets"
        self.render_dir = self.test_dir / "render"
        self.am = AssetManager(self.asset_dir)
        self.harness = RenderHarness(self.render_dir)

    def test_asset_hashing(self):
        data = b"image-content"
        h1 = self.am.ingest(data, "url1", "image", "png")
        h2 = self.am.ingest(data, "url2", "image", "png")
        self.assertEqual(h1, h2)
        self.assertIn(h1, self.am.manifest.assets)

    def test_svg_validation(self):
        unsafe = b"<svg><script>alert(1)</script></svg>"
        with self.assertRaises(ValueError):
            self.am.ingest(unsafe, "bad", "svg", "svg")

        safe = b"<svg><rect/></svg>"
        h = self.am.ingest(safe, "good", "svg", "svg")
        self.assertIn(h, self.am.manifest.assets)

    @unittest.skipUnless(
        CHROMIUM_AVAILABLE,
        "headless chromium not available — install with: "
        "pip install playwright && playwright install chromium",
    )
    def test_harness_determinism(self):
        # Part 11: the harness now performs a real browser render.
        res = self.harness.render(
            '<html><body><div data-node-id="n1"></div></body></html>',
            {"w": 320, "h": 640},
            "build1",
        )
        self.assertTrue(res.screenshot_path.exists())
        self.assertGreater(res.screenshot_path.stat().st_size, 0)
        self.assertIsInstance(res.layout_metadata, dict)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Confirm the whole suite is still green**

```bash
python3 -m unittest discover -s tests
```

Expected: `Ran 253 tests ... OK` (241 baseline + 12 new), with `OK (skipped=1)` when
playwright/chromium is not installed (the guarded `test_harness_determinism`) or plain
`OK` when it is.

- [ ] **Step 7: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add plugin/figmaforge/core/render_harness.py plugin/figmaforge/tests/test_render_harness.py plugin/figmaforge/tests/test_render_pipeline.py
git commit -m "feat(render): real Playwright render harness with viewport normalization"
```

---

## Task 4: HTML generation util (IRDocument + styles → renderable HTML) (TDD)

**Files:**
- Create: `plugin/figmaforge/core/render_html.py`
- Create: `plugin/figmaforge/tests/test_render_html.py`
- Test: `plugin/figmaforge/tests/test_render_html.py`

Design: mirrors the intent of `runtime/src/core/render_handler.ts::generateFullHtml` —
`body` and `#figmaforge-root` fixed to the viewport in px, `data-node-id` on every element,
inline `VStyle.base` styles (camelCase → kebab-case), escaped text content, and the inline
script that populates `window.__figmaforge_meta` which the harness evaluates.

- [ ] **Step 1: Write the failing tests**

Create `plugin/figmaforge/tests/test_render_html.py`:

```python
"""
Render HTML generation tests (Part 11).

Run:  python3 -m unittest tests.test_render_html -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.generator_types import VStyle
from core.ir_types import (
    IRDimensions,
    IRDocument,
    IRNode,
    IRSource,
    IRTextContent,
    KIND_FRAME,
    KIND_PAGE,
    KIND_TEXT,
)
from core.render_html import generate_render_html


def _make_document():
    """Page 'page-1' containing frame 'frame-1' with text child 'text-1'."""
    text = IRNode(
        id="text-1", name="Label", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="fk", node_id="text-1"),
        text=IRTextContent(characters="Hello"),
    )
    frame = IRNode(
        id="frame-1", name="Card", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="fk", node_id="frame-1"),
        dimensions=IRDimensions(width=200, height=100),
        children=[text],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="fk", node_id="page-1"),
        children=[frame],
    )
    return IRDocument(file_key="fk", name="Doc", pages=[page])


class TestGenerateRenderHtml(unittest.TestCase):
    def test_root_fixed_to_viewport(self):
        html = generate_render_html(_make_document(), {}, {"w": 1440, "h": 900})
        self.assertIn("width: 1440px", html)
        self.assertIn("height: 900px", html)
        self.assertIn('id="figmaforge-root"', html)

    def test_accepts_width_height_keys(self):
        html = generate_render_html(_make_document(), {}, {"width": 390, "height": 844})
        self.assertIn("width: 390px", html)
        self.assertIn("height: 844px", html)

    def test_data_node_ids_emitted(self):
        html = generate_render_html(_make_document(), {}, {"w": 800, "h": 600})
        self.assertIn('data-node-id="frame-1"', html)
        self.assertIn('data-node-id="text-1"', html)

    def test_inline_styles_from_vstyle(self):
        styles = {
            "frame-1": VStyle(base={"backgroundColor": "#ff0000", "width": "200px"}),
            "text-1": VStyle(base={"fontSize": "16px"}),
        }
        html = generate_render_html(_make_document(), styles, {"w": 800, "h": 600})
        self.assertIn("background-color: #ff0000", html)
        self.assertIn("font-size: 16px", html)

    def test_text_content_escaped(self):
        doc = _make_document()
        doc.pages[0].children[0].children[0].text.characters = "<b>hi</b> & more"
        html = generate_render_html(doc, {}, {"w": 800, "h": 600})
        self.assertNotIn("<b>hi</b>", html)
        self.assertIn("&lt;b&gt;hi&lt;/b&gt; &amp; more", html)

    def test_meta_script_present(self):
        html = generate_render_html(_make_document(), {}, {"w": 800, "h": 600})
        self.assertIn("window.__figmaforge_meta", html)
        self.assertIn("getBoundingClientRect", html)

    def test_empty_document(self):
        html = generate_render_html(IRDocument(file_key="fk"), {}, {"w": 800, "h": 600})
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn('id="figmaforge-root"', html)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and expect FAIL**

```bash
python3 -m unittest tests.test_render_html -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'core.render_html'`.

- [ ] **Step 3: Minimal implementation**

Create `plugin/figmaforge/core/render_html.py`:

```python
"""
Render HTML generation (Part 11).

Converts an ``IRDocument`` plus per-node ``VStyle`` dictionaries into a full
HTML document suitable for rendering through
:class:`core.render_harness.RenderHarness`. Mirrors the intent of
``runtime/src/core/render_handler.ts``:

- ``body`` and ``#figmaforge-root`` are fixed to the viewport size in px.
- Every node element carries a ``data-node-id`` attribute.
- An inline script populates ``window.__figmaforge_meta`` with per-node
  box-model + computed styles (``getBoundingClientRect`` +
  ``getComputedStyle``).

Standard library only.
"""

from __future__ import annotations

import html as _html
from typing import Any, Dict, Optional

from .generator_types import VStyle
from .ir_types import IRDocument, IRNode, KIND_TEXT
from .render_harness import normalize_viewport


def _camel_to_kebab(key: str) -> str:
    return "".join(f"-{c.lower()}" if c.isupper() else c for c in key)


def _style_to_css(style: Dict[str, Any]) -> str:
    """Serialize a ``VStyle.base`` dict to inline CSS.

    Keys may be camelCase (``fontSize``) — they are emitted kebab-case
    (``font-size``). Values are emitted verbatim, so they must carry their
    units (``"16px"``, ``"#ff0000"``). ``None`` values are dropped.
    """
    parts = []
    for key, value in style.items():
        if value is None:
            continue
        parts.append(f"{_camel_to_kebab(key)}: {value}")
    return "; ".join(parts)


def _node_to_html(node: IRNode, styles: Dict[str, VStyle]) -> str:
    tag = "span" if node.kind == KIND_TEXT else "div"
    attrs = f'data-node-id="{_html.escape(node.id, quote=True)}"'
    vstyle = styles.get(node.id)
    css = _style_to_css(vstyle.base) if vstyle is not None else ""
    if css:
        attrs += f' style="{_html.escape(css, quote=True)}"'
    if node.kind == KIND_TEXT:
        characters = node.text.characters if node.text else ""
        return f"<{tag} {attrs}>{_html.escape(characters)}</{tag}>"
    children = "".join(_node_to_html(child, styles) for child in node.children)
    return f"<{tag} {attrs}>{children}</{tag}>"


def generate_render_html(
    document: IRDocument,
    styles: Dict[str, VStyle],
    viewport_spec: Dict[str, int],
    title: str = "FigmaForge Render",
) -> str:
    """Generate a full renderable HTML document from the IR + styles.

    Renders the children of the first page (``document.pages[0]``, or the
    first child of ``document.root`` as a fallback) into
    ``#figmaforge-root``. ``viewport_spec`` accepts ``{"w", "h"}`` or
    ``{"width", "height"}`` keys.
    """
    viewport = normalize_viewport(viewport_spec)

    page: Optional[IRNode] = None
    if document.pages:
        page = document.pages[0]
    elif document.root is not None:
        page = document.root.children[0] if document.root.children else document.root

    body_html = ""
    if page is not None:
        body_html = "".join(
            _node_to_html(child, styles) for child in page.children
        )

    width = viewport["width"]
    height = viewport["height"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_html.escape(title)}</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      width: {width}px;
      height: {height}px;
      overflow: hidden;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }}
    /* FigmaForge render container */
    #figmaforge-root {{
      width: {width}px;
      height: {height}px;
      position: relative;
    }}
  </style>
</head>
<body>
  <div id="figmaforge-root">
{body_html}
  </div>
  <script>
    // Populate window.__figmaforge_meta for RenderHarness extraction.
    (function () {{
      const root = document.getElementById("figmaforge-root");
      if (!root) return;
      const meta = {{}};
      root.querySelectorAll("[data-node-id]").forEach(el => {{
        const id = el.getAttribute("data-node-id");
        const rect = el.getBoundingClientRect();
        const computed = window.getComputedStyle(el);
        meta[id] = {{
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          styles: {{
            fontSize: parseFloat(computed.fontSize),
            color: computed.color,
            backgroundColor: computed.backgroundColor,
            fontFamily: computed.fontFamily,
            padding: computed.padding,
            margin: computed.margin,
          }}
        }};
      }});
      window.__figmaforge_meta = meta;
    }})();
  </script>
</body>
</html>
"""
```

- [ ] **Step 4: Run the tests and expect PASS**

```bash
python3 -m unittest tests.test_render_html -v
```

Expected: `Ran 7 tests ... OK`.

- [ ] **Step 5: Confirm the whole suite is still green**

```bash
python3 -m unittest discover -s tests
```

Expected: `Ran 260 tests ... OK` (or `OK (skipped=1)` without chromium).

- [ ] **Step 6: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add plugin/figmaforge/core/render_html.py plugin/figmaforge/tests/test_render_html.py
git commit -m "feat(render): HTML generation util for browser rendering"
```

---

## Task 5: RenderCallable adapter wiring the harness into RepairLoop (TDD)

**Files:**
- Create: `plugin/figmaforge/core/render_adapter.py`
- Create: `plugin/figmaforge/tests/test_render_adapter.py`
- Test: `plugin/figmaforge/tests/test_render_adapter.py`

Design: `make_render_callable(harness, default_height=900)` returns a closure matching the
`RenderCallable` protocol `(plan, styles, document, iteration) -> (render_meta, screenshot_path)`.
It generates the HTML, renders through the harness at `plan.viewport` width, and returns
`layout_metadata` (node-id-keyed) as `render_meta`. Injected via `RepairLoop(render_fn=...)`;
`repair_loop.py` is NOT modified. Tests use a duck-typed fake harness, and one test drives a
real `RepairLoop` to prove the loop consumes the real render_meta shape.

- [ ] **Step 1: Write the failing tests**

Create `plugin/figmaforge/tests/test_render_adapter.py`:

```python
"""
Render adapter tests (Part 11).

Proves the RepairLoop consumes the real harness render_meta shape through
the RenderCallable injection point — using a fake harness (no browser).

Run:  python3 -m unittest tests.test_render_adapter -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.generator_types import VStyle
from core.ir_types import IRDocument, IRNode, IRSource, KIND_FRAME, KIND_PAGE
from core.layout_types import Box, DISPLAY_FLEX, LayoutNodePlan, LayoutPlan
from core.render_adapter import make_render_callable
from core.render_harness import RenderResult
from core.repair_loop import RepairConfig, RepairLoop, STOP_THRESHOLD


class FakeHarness:
    """Duck-typed RenderHarness that records calls and returns canned meta."""

    def __init__(self, meta):
        self.meta = meta
        self.calls = []

    def render(self, content_html, viewport_spec, build_id):
        self.calls.append({
            "html": content_html,
            "viewport": viewport_spec,
            "build_id": build_id,
        })
        return RenderResult(
            screenshot_path=Path(f"/tmp/figmaforge_fake/{build_id}.png"),
            layout_metadata=dict(self.meta),
        )


def _make_plan():
    """Screen 'frame-root' (viewport-sized) with child 'n1' at 0,0 200x100."""
    screen = LayoutNodePlan(
        node_id="frame-root", name="Root", kind="frame",
        display=DISPLAY_FLEX, box=Box(x=0, y=0, width=1440, height=900),
    )
    screen.children.append(LayoutNodePlan(
        node_id="n1", name="Box", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=100),
    ))
    return LayoutPlan(file_key="fk", viewport=1440.0, screens=[screen])


def _make_document():
    """Page containing frame 'frame-root' with child 'n1'."""
    box = IRNode(
        id="n1", name="Box", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="fk", node_id="n1"),
    )
    root = IRNode(
        id="frame-root", name="Root", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="fk", node_id="frame-root"),
        children=[box],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="fk", node_id="page-1"),
        children=[root],
    )
    return IRDocument(file_key="fk", name="Doc", pages=[page])


# render_meta matching the plan EXACTLY (similarity score 1.0). Both plan
# nodes — including the top-level screen node — must be present, otherwise
# DiffEngine reports missing_in_render.
MATCHING_META = {
    "frame-root": {"x": 0, "y": 0, "width": 1440, "height": 900},
    "n1": {"x": 0, "y": 0, "width": 200, "height": 100,
           "styles": {"fontSize": 16}},
}


class TestRenderAdapter(unittest.TestCase):
    def test_returns_diff_engine_shaped_meta(self):
        harness = FakeHarness(MATCHING_META)
        render_fn = make_render_callable(harness)
        meta, screenshot = render_fn(_make_plan(), {}, _make_document(), 0)
        self.assertEqual(screenshot, "/tmp/figmaforge_fake/repair-iter-0.png")
        self.assertIn("n1", meta)
        for key in ("x", "y", "width", "height"):
            self.assertIn(key, meta["n1"])
        self.assertEqual(len(harness.calls), 1)

    def test_generates_html_with_node_ids(self):
        harness = FakeHarness(MATCHING_META)
        render_fn = make_render_callable(harness)
        render_fn(
            _make_plan(),
            {"n1": VStyle(base={"width": "200px"})},
            _make_document(),
            0,
        )
        html = harness.calls[0]["html"]
        self.assertIn('data-node-id="frame-root"', html)
        self.assertIn('data-node-id="n1"', html)
        self.assertIn("width: 200px", html)

    def test_viewport_uses_plan_width(self):
        harness = FakeHarness(MATCHING_META)
        render_fn = make_render_callable(harness, default_height=800)
        plan = _make_plan()
        plan.viewport = 390.0
        render_fn(plan, {}, _make_document(), 1)
        self.assertEqual(
            harness.calls[0]["viewport"], {"width": 390, "height": 800}
        )
        self.assertEqual(harness.calls[0]["build_id"], "repair-iter-1")

    def test_repair_loop_consumes_adapter_meta(self):
        harness = FakeHarness(MATCHING_META)
        loop = RepairLoop(
            config=RepairConfig(similarity_threshold=0.95, max_iterations=3),
            render_fn=make_render_callable(harness),
        )
        result = loop.run(_make_plan(), _make_document(), run_id="adapter-test")
        self.assertEqual(result.stop_reason, STOP_THRESHOLD)
        self.assertEqual(result.final_score, 1.0)
        self.assertEqual(
            result.history.iterations[0].screenshot_path,
            "/tmp/figmaforge_fake/repair-iter-0.png",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and expect FAIL**

```bash
python3 -m unittest tests.test_render_adapter -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'core.render_adapter'`.

- [ ] **Step 3: Minimal implementation**

Create `plugin/figmaforge/core/render_adapter.py`:

```python
"""
Repair-loop render adapter (Part 11).

Bridges the real :class:`core.render_harness.RenderHarness` into the Part 8
``RepairLoop`` via the existing ``RenderCallable`` dependency-injection point
— zero changes to ``repair_loop.py`` internals.

Standard library only.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Tuple

from .generator_types import VStyle
from .ir_types import IRDocument
from .layout_types import LayoutPlan
from .render_harness import RenderHarness
from .render_html import generate_render_html

DEFAULT_VIEWPORT_WIDTH = 1440
DEFAULT_VIEWPORT_HEIGHT = 900


def make_render_callable(
    harness: RenderHarness,
    default_height: int = DEFAULT_VIEWPORT_HEIGHT,
) -> Callable[[LayoutPlan, Dict[str, VStyle], IRDocument, int], Tuple[Dict[str, Any], str]]:
    """Build a ``RenderCallable`` closure for ``RepairLoop(render_fn=...)``.

    Each invocation:

    1. Generates render HTML from the document + styles
       (:func:`core.render_html.generate_render_html`).
    2. Renders it through the harness at the plan's viewport width
       (``plan.viewport`` is a float width; falls back to 1440), using
       ``default_height`` for the height.
    3. Returns ``(layout_metadata, screenshot_path_str)`` — metadata keyed
       by node id with ``{x, y, width, height, styles}``, exactly the shape
       ``DiffEngine.diff(plan, render_meta)`` consumes.
    """

    def render_fn(
        plan: LayoutPlan,
        styles: Dict[str, VStyle],
        document: IRDocument,
        iteration: int,
    ) -> Tuple[Dict[str, Any], str]:
        width = int(plan.viewport) if plan.viewport else DEFAULT_VIEWPORT_WIDTH
        viewport = {"width": width, "height": int(default_height)}
        content_html = generate_render_html(document, styles, viewport)
        result = harness.render(
            content_html, viewport, build_id=f"repair-iter-{iteration}"
        )
        return result.layout_metadata, str(result.screenshot_path)

    return render_fn
```

- [ ] **Step 4: Run the tests and expect PASS**

```bash
python3 -m unittest tests.test_render_adapter -v
```

Expected: `Ran 4 tests ... OK`.

- [ ] **Step 5: Confirm the whole suite is still green**

```bash
python3 -m unittest discover -s tests
```

Expected: `Ran 264 tests ... OK` (or `OK (skipped=1)` without chromium).

- [ ] **Step 6: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add plugin/figmaforge/core/render_adapter.py plugin/figmaforge/tests/test_render_adapter.py
git commit -m "feat(render): RenderCallable adapter wiring harness into RepairLoop"
```

---

## Task 6: Real-browser smoke test (skips without chromium)

**Files:**
- Create: `plugin/figmaforge/tests/test_render_harness_smoke.py`
- Test: `plugin/figmaforge/tests/test_render_harness_smoke.py`

Design: end-to-end verification with REAL headless chromium — HTML generation → harness
render → screenshot on disk → `window.__figmaforge_meta` extraction → `DiffEngine`
consumption. Guarded with `unittest.skipUnless` so suites stay green where playwright or
chromium is absent.

- [ ] **Step 1: Write the test (it exercises already-implemented code; it FAILS by skipping or passing, never by logic errors — the "red" step is the skip-verification run below)**

Create `plugin/figmaforge/tests/test_render_harness_smoke.py`:

```python
"""
Real-browser smoke test for the render harness (Part 11).

Skipped when headless chromium is unavailable so suites stay green on
machines without Playwright installed.

Run:  python3 -m unittest tests.test_render_harness_smoke -v
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.diff_engine import DiffEngine
from core.generator_types import VStyle
from core.ir_types import IRDocument, IRNode, IRSource, KIND_FRAME, KIND_PAGE
from core.layout_types import Box, DISPLAY_FLEX, LayoutNodePlan, LayoutPlan
from core.render_harness import RenderHarness
from core.render_html import generate_render_html


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


CHROMIUM_AVAILABLE = _chromium_available()


def _build_html() -> str:
    """One 200x100 red box ('node-a') as the page's only child."""
    frame = IRNode(
        id="node-a", name="Box", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="fk", node_id="node-a"),
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="fk", node_id="page-1"),
        children=[frame],
    )
    doc = IRDocument(file_key="fk", name="Smoke", pages=[page])
    styles = {
        "node-a": VStyle(base={
            "width": "200px",
            "height": "100px",
            "backgroundColor": "#ff0000",
        }),
    }
    return generate_render_html(doc, styles, {"w": 800, "h": 600})


@unittest.skipUnless(
    CHROMIUM_AVAILABLE,
    "headless chromium not available — install with: "
    "pip install playwright && playwright install chromium",
)
class TestRenderHarnessSmoke(unittest.TestCase):
    def setUp(self):
        self.out_dir = Path("/tmp/figmaforge_smoke")
        shutil.rmtree(self.out_dir, ignore_errors=True)

    def test_smoke_render_screenshot_and_metadata(self):
        harness = RenderHarness(self.out_dir)
        result = harness.render(_build_html(), {"w": 800, "h": 600}, "smoke")

        self.assertTrue(result.screenshot_path.exists())
        self.assertGreater(result.screenshot_path.stat().st_size, 0)
        self.assertIn("node-a", result.layout_metadata)
        entry = result.layout_metadata["node-a"]
        self.assertEqual(entry["width"], 200)
        self.assertEqual(entry["height"], 100)
        self.assertIn("fontSize", entry["styles"])
        self.assertIn("backgroundColor", entry["styles"])

    def test_smoke_metadata_feeds_diff_engine(self):
        harness = RenderHarness(self.out_dir)
        result = harness.render(_build_html(), {"w": 800, "h": 600}, "smoke2")

        screen = LayoutNodePlan(
            node_id="node-a", name="Box", kind="frame",
            display=DISPLAY_FLEX, box=Box(x=0, y=0, width=200, height=100),
        )
        plan = LayoutPlan(file_key="fk", viewport=800.0, screens=[screen])

        report = DiffEngine().diff(plan, result.layout_metadata)
        types = [m["type"] for m in report.mismatches]
        self.assertNotIn("missing_in_render", types)
        self.assertNotIn("geometry_mismatch", types)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run and verify expected behavior**

```bash
python3 -m unittest tests.test_render_harness_smoke -v
```

Expected WITHOUT chromium installed: `Ran 2 tests ... OK (skipped=2)`.
Expected WITH chromium installed: `Ran 2 tests ... OK` (tests actually render; if they
fail here, fix the harness/HTML util before continuing — this is the first real-browser
exercise of Tasks 3–5).

- [ ] **Step 3: Confirm the whole suite is still green**

```bash
python3 -m unittest discover -s tests
```

Expected: `Ran 266 tests ... OK` — with `OK (skipped=3)` when chromium is unavailable
(guarded `test_harness_determinism` + 2 smoke tests), plain `OK` when it is.

- [ ] **Step 4: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add plugin/figmaforge/tests/test_render_harness_smoke.py
git commit -m "test(render): real-browser smoke tests (skip without chromium)"
```

---

## Task 7: Fix TS `tryBrowserRender` to pipe the script via stdin (TDD)

**Files:**
- Modify: `runtime/src/core/render_handler.ts`
- Modify: `runtime/tests/test_all.ts`
- Test: `runtime/tests/test_all.ts` (suite grows from 100 to 105)

Design: extract two exported, pure, testable helpers — `buildBrowserRenderScript` (the
Python bridge script, mirroring the working `cmdRender` variant in
`runtime/src/cli/main.ts`) and `parseBrowserRenderOutput` (last-line JSON parser) — then
rewrite `tryBrowserRender` to spawn `python3 -`, pipe the script via stdin, and parse
`{"screenshot": ..., "meta": ...}` into the screenshot path. `RenderOutput
{htmlPath, screenshotPath, layoutMeta, htmlHash, viewport}` produced by `renderHandler`
is unchanged.

- [ ] **Step 1: Write the failing tests**

In `runtime/tests/test_all.ts`, change the import line:

```ts
import { vnodeToHtml } from "../src/core/render_handler.js";
```

to:

```ts
import { vnodeToHtml, buildBrowserRenderScript, parseBrowserRenderOutput } from "../src/core/render_handler.js";
```

Then, immediately AFTER the existing `// 15. Render Handler (VNode → HTML)` describe block
(whose closing lines are `  }));` followed by a blank line and `  return results;`), insert:

```ts
  // 16. Browser render bridge (tryBrowserRender helpers)
  results.push(await describe("browser render bridge", async () => {
    await it("buildBrowserRenderScript embeds viewport and paths", async () => {
      const script = buildBrowserRenderScript(
        "/tmp/r/render_abc.html",
        "/tmp/r/screenshot_abc.png",
        { width: 1440, height: 900 },
      );
      assert(script.includes('"width": 1440'), `Expected width in: ${script}`);
      assert(script.includes('"height": 900'), `Expected height in: ${script}`);
      assert(script.includes("/tmp/r/render_abc.html"), "Expected html path");
      assert(script.includes("/tmp/r/screenshot_abc.png"), "Expected screenshot path");
      assert(script.includes("sync_playwright"), "Expected playwright usage");
      assert(script.includes("window.__figmaforge_meta"), "Expected meta extraction");
    });

    await it("parseBrowserRenderOutput parses valid payload", async () => {
      const parsed = parseBrowserRenderOutput(
        JSON.stringify({ screenshot: "/tmp/s.png", meta: { n1: { x: 0 } } }),
      );
      assert(parsed !== null, "Should parse");
      assertEqual(parsed!.screenshotPath, "/tmp/s.png");
      assertEqual((parsed!.meta.n1 as Record<string, number>).x, 0);
    });

    await it("parseBrowserRenderOutput takes the last stdout line", async () => {
      const payload = JSON.stringify({ screenshot: "/tmp/s2.png", meta: {} });
      const parsed = parseBrowserRenderOutput(`warning: something\n${payload}\n`);
      assert(parsed !== null, "Should parse last line");
      assertEqual(parsed!.screenshotPath, "/tmp/s2.png");
    });

    await it("parseBrowserRenderOutput returns null for error payload", async () => {
      const parsed = parseBrowserRenderOutput(
        JSON.stringify({ error: "playwright_not_installed" }),
      );
      assertEqual(parsed, null);
    });

    await it("parseBrowserRenderOutput returns null for garbage", async () => {
      assertEqual(parseBrowserRenderOutput("not json at all"), null);
      assertEqual(parseBrowserRenderOutput(""), null);
    });
  }));
```

- [ ] **Step 2: Run the tests and expect FAIL**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
npx tsc
```

Expected: FAIL — compile errors such as
`error TS2305: Module '"../src/core/render_handler.js"' has no exported member 'buildBrowserRenderScript'`
(do NOT run the test runner yet; compilation is the red signal).

- [ ] **Step 3: Minimal implementation**

In `runtime/src/core/render_handler.ts`, REPLACE the entire dead block — everything from the
comment `/**` line starting ` * Attempt browser rendering using Playwright via Python bridge.`
through the end of the `ctx_pythonBin` function (current lines 395–447, i.e. the
`tryBrowserRender` function plus `ctx_pythonBin`) — with:

```ts
/**
 * Build the Python bridge script that renders an HTML file in headless
 * chromium and prints a single JSON payload:
 * {"screenshot": "<path>", "meta": {...}} — or {"error": "..."} on failure.
 * The script is piped to the interpreter via stdin (python3 -).
 */
export function buildBrowserRenderScript(
  htmlPath: string,
  screenshotPath: string,
  viewport: { width: number; height: number },
): string {
  return `
import sys, json
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": ${viewport.width}, "height": ${viewport.height}})
        page.goto("file://${htmlPath}")
        page.wait_for_load_state("networkidle")
        page.screenshot(path="${screenshotPath}", full_page=True)
        meta = page.evaluate("window.__figmaforge_meta || {}")
        browser.close()
        print(json.dumps({"screenshot": "${screenshotPath}", "meta": meta}))
except ImportError:
    print(json.dumps({"error": "playwright_not_installed"}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
`;
}

/**
 * Parse the JSON payload printed by the Python bridge script.
 * Returns null when the output is missing, malformed, or reports an error.
 */
export function parseBrowserRenderOutput(
  stdout: string,
): { screenshotPath: string; meta: Record<string, unknown> } | null {
  const line = stdout.trim().split("\n").pop()?.trim();
  if (!line) return null;
  try {
    const parsed = JSON.parse(line) as {
      screenshot?: string;
      meta?: Record<string, unknown>;
      error?: string;
    };
    if (parsed.error || !parsed.screenshot) return null;
    return { screenshotPath: parsed.screenshot, meta: parsed.meta ?? {} };
  } catch {
    return null;
  }
}

/**
 * Attempt browser rendering using Playwright via the Python bridge.
 * Pipes the bridge script to python via stdin (python3 -) and parses the
 * JSON output. Returns the screenshot path if successful, null otherwise.
 */
async function tryBrowserRender(
  htmlPath: string,
  outputDir: string,
  hash: string,
  viewport: { width: number; height: number },
): Promise<string | null> {
  const { spawn } = await import("node:child_process");
  const screenshotPath = path.join(outputDir, `screenshot_${hash}.png`);
  const script = buildBrowserRenderScript(htmlPath, screenshotPath, viewport);

  return new Promise<string | null>((resolve) => {
    const child = spawn(ctx_pythonBin(), ["-"], {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });

    let stdout = "";
    let settled = false;
    const timer = setTimeout(() => {
      child.kill();
      finish(null);
    }, 30_000);

    const finish = (value: string | null): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf-8");
    });
    child.on("error", () => finish(null));
    child.on("close", (code) => {
      if (code !== 0) {
        finish(null);
        return;
      }
      const parsed = parseBrowserRenderOutput(stdout);
      finish(parsed ? parsed.screenshotPath : null);
    });

    child.stdin.on("error", () => finish(null));
    child.stdin.write(script);
    child.stdin.end();
  });
}

function ctx_pythonBin(): string {
  return process.env.PYTHON_BIN ?? "python3";
}
```

Note: keep the declaration order exactly as shown (`timer` is declared before `finish`
uses it inside the timeout callback; `finish` is only ever invoked from event callbacks
and the timeout, both after initialization).

- [ ] **Step 4: Run the tests and expect PASS**

```bash
npx tsc && node dist/runtime/tests/run_all.js
```

Expected output tail:

```
  browser render bridge
    ✓ buildBrowserRenderScript embeds viewport and paths
    ✓ parseBrowserRenderOutput parses valid payload
    ✓ parseBrowserRenderOutput takes the last stdout line
    ✓ parseBrowserRenderOutput returns null for error payload
    ✓ parseBrowserRenderOutput returns null for garbage

  ────────────────────────────────────
  105 passing, 0 failing (105 total)
```

Do NOT modify the `npm test` script in `runtime/package.json` — out of scope.

- [ ] **Step 5: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add runtime/src/core/render_handler.ts runtime/tests/test_all.ts
git commit -m "fix(runtime): wire tryBrowserRender Python bridge via stdin"
```

---

## Task 8: Docs — README + CLAUDE.md setup steps, repair-loop harness section, dev log

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs/repair-loop.md`
- Modify: `docs/DEVELOPMENT_LOG.md`

This is a docs-only task (no tests); verification is `claude plugin validate --strict`
plus a full-suite re-run to make sure nothing else drifted.

- [ ] **Step 1: README.md — add browser rendering setup**

In `README.md`, locate this exact text (end of the Installation steps):

````markdown
4. **Test the detector:**
   ```bash
   python3 plugin/figmaforge/tests/test_detector.py
   ```

---
````

Replace it with:

````markdown
4. **Test the detector:**
   ```bash
   python3 plugin/figmaforge/tests/test_detector.py
   ```

### Browser rendering dependencies (required)

The render stage (Part 11) uses Playwright with headless chromium to produce real
screenshots and layout metadata:

```bash
pip install playwright && playwright install chromium
```

Without chromium, browser-render tests are skipped and the TS runtime falls back to
HTML-only output.

---
````

- [ ] **Step 2: CLAUDE.md — technology stack exception, test counts, setup command**

Apply these exact replacements in `CLAUDE.md`:

1. Section 2, first bullet:

   Original:
   ```
   - **Code:** Python 3 (standard library only) for detection, routing, lifecycle state, and hooks. TypeScript (Node.js stdlib only) for the orchestration runtime.
   ```
   New:
   ```
   - **Code:** Python 3 (standard library only, with one user-approved exception: `playwright` for browser rendering — Part 11) for detection, routing, lifecycle state, and hooks. TypeScript (Node.js stdlib only) for the orchestration runtime.
   ```

2. Section 3 test counts:

   Original:
   ```
     - `tests/` (22 test files, 241 tests): Unit, integration, snapshot, property-based, repair-loop, and backend adapter tests.
   ```
   New:
   ```
     - `tests/` (26 test files, 266 tests): Unit, integration, snapshot, property-based, repair-loop, backend adapter, and render-harness tests.
   ```

   Original:
   ```
     - `tests/` (3 files, 100 tests): Comprehensive test suite with custom test framework
   ```
   New:
   ```
     - `tests/` (3 files, 105 tests): Comprehensive test suite with custom test framework
   ```

3. Section 5 commands — update counts and add the setup command. Replace:
   ```
   * **Run all tests (241 tests):**
   ```
   with:
   ```
   * **Run all tests (266 tests):**
   ```
   Replace:
   ```
   * **Run runtime tests (100 tests):**
     `npx tsc && node dist/runtime/tests/run_all.js`
   ```
   with:
   ```
   * **Run runtime tests (105 tests):**
     `npx tsc && node dist/runtime/tests/run_all.js`
   * **Install browser rendering dependencies (required for the render stage):**
     `pip install playwright && playwright install chromium`
   ```

4. Section 7 — replace:
   ```
   - All 241 Python tests across 22 test files must pass (`python3 -m unittest discover -s tests`).
   - All 100 TypeScript runtime tests must pass (`npx tsc && node dist/runtime/tests/run_all.js`).
   ```
   with:
   ```
   - All 266 Python tests across 26 test files must pass (`python3 -m unittest discover -s tests`); browser-render tests skip cleanly without chromium.
   - All 105 TypeScript runtime tests must pass (`npx tsc && node dist/runtime/tests/run_all.js`).
   ```

- [ ] **Step 3: docs/repair-loop.md — harness section**

Locate the line `## Repair Candidate Categories` and insert this section IMMEDIATELY BEFORE it
(the block below is fenced with four backticks only so the inner bash fence survives; write
the CONTENT — including the inner ```bash fence — verbatim into `docs/repair-loop.md`):

````markdown
## Render Harness (Part 11)

Since Part 11 the render step produces **real browser output** via Playwright
(a user-approved required dependency) instead of synthetic fixtures:

- `core/render_harness.py` — `RenderHarness(output_dir).render(content_html,
  viewport_spec, build_id)` launches headless chromium, screenshots the page, and
  evaluates `window.__figmaforge_meta`. Returns `RenderResult(screenshot_path,
  layout_metadata)` where `layout_metadata` is keyed by `data-node-id`:
  `{node_id: {x, y, width, height, styles: {fontSize, color, backgroundColor,
  padding, margin}}}` — exactly what `DiffEngine.diff(plan, render_meta)` consumes.
- `core/render_html.py` — `generate_render_html(document, styles, viewport)` turns an
  `IRDocument` + `VStyle` map into the rendered HTML (`#figmaforge-root` fixed to the
  viewport, `data-node-id` on every element, inline metadata-extraction script).
- `core/render_adapter.py` — `make_render_callable(harness)` builds the
  `RenderCallable` closure injected via `RepairLoop(render_fn=...)`. Loop internals
  are unchanged.
- Viewport specs accept both `{w, h}` and `{width, height}` key forms.

Setup (required):

```bash
pip install playwright && playwright install chromium
```

When chromium is unavailable, `RenderHarness.render` raises `RenderHarnessError` naming
the install command, and browser-dependent tests skip. Pixel diffing (`_diff_raster`)
remains a placeholder.
````

- [ ] **Step 4: docs/DEVELOPMENT_LOG.md — Part 11 entry**

Append to the END of `docs/DEVELOPMENT_LOG.md`:

```markdown
## Part 11: Real Browser Render Harness (2026-08-13)

### Overview
Replaced every synthetic render path with real headless-chromium rendering via Playwright (a user-approved required dependency). The Part 8 repair loop now diffs actual browser output: screenshots plus per-node box-model and computed-style metadata keyed by `data-node-id`.

### What Changed
1. **`core/render_harness.py`** — placeholder replaced with a real `playwright.sync_api` implementation: chromium launch, viewport-normalized page (`{w,h}` and `{width,height}` both accepted), `networkidle` wait, full-page screenshot, `window.__figmaforge_meta` extraction. `layout_metadata` is now node-id-keyed in `DiffEngine` shape. Missing playwright raises `RenderHarnessError` naming the install command (module still imports cleanly).
2. **`core/render_html.py`** — new: `IRDocument` + `VStyle` map → full HTML document with `#figmaforge-root` fixed to the viewport, `data-node-id` attributes, and the inline metadata-extraction script (mirrors `runtime/src/core/render_handler.ts` intent).
3. **`core/render_adapter.py`** — new: `make_render_callable(harness)` produces the `RenderCallable` closure injected via `RepairLoop(render_fn=...)`. Zero changes to `repair_loop.py` internals.
4. **`runtime/src/core/render_handler.ts`** — dead `tryBrowserRender` fixed: the Python bridge script is piped via stdin to `python3 -` and its JSON output parsed into the screenshot path. Extracted `buildBrowserRenderScript` / `parseBrowserRenderOutput` for testability.
5. **Docs** — README + CLAUDE.md setup steps (`pip install playwright && playwright install chromium`), `docs/repair-loop.md` harness section, this log entry.

### Testing
- Mocked-playwright contract tests (`tests/test_render_harness.py`, 12), HTML generation tests (`tests/test_render_html.py`, 7), adapter + repair-loop integration tests (`tests/test_render_adapter.py`, 4), real-browser smoke tests that skip without chromium (`tests/test_render_harness_smoke.py`, 2; `test_harness_determinism` also guarded).
- TS: 5 new bridge tests in the runtime suite.
- Full gate: 266 Python tests OK, 105 runtime tests passing, `claude plugin validate --strict` clean.

### Non-goals (deferred)
Pixel/perceptual diffing (`_diff_raster`), real PNG decode in `screenshot_compare.ts`, Figma baseline download, stub backend implementations.
```

- [ ] **Step 5: Verify**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest discover -s tests
cd /Users/mdshagilnizami/code/projects/FigmaForge
claude plugin validate --strict plugin/figmaforge
```

Expected: `Ran 266 tests ... OK` (with skips when chromium absent) and
`✔ Validation passed`.

- [ ] **Step 6: Commit**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
git add README.md CLAUDE.md docs/repair-loop.md docs/DEVELOPMENT_LOG.md
git commit -m "docs: document Playwright setup and Part 11 render harness"
```

---

## Task 9: Final verification gate + push/PR

**Files:** None (verification + git only).

- [ ] **Step 1: Full Python suite**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge/plugin/figmaforge
python3 -m unittest discover -s tests
```

Expected: `Ran 266 tests in ...s` then `OK` — with `OK (skipped=3)` when
playwright/chromium is not installed (guarded `test_harness_determinism` + 2 smoke
tests), plain `OK` when it is. All 241 pre-existing tests must still pass.

- [ ] **Step 2: Full TS suite**

```bash
cd /Users/mdshagilnizami/code/projects/FigmaForge
npx tsc && node dist/runtime/tests/run_all.js
```

Expected: `105 passing, 0 failing (105 total)` (100 pre-existing + 5 new bridge tests)
and exit code 0.

- [ ] **Step 3: Plugin validation**

```bash
claude plugin validate --strict plugin/figmaforge
```

Expected: `✔ Validation passed`.

- [ ] **Step 4: Confirm only intended files were committed**

```bash
git log --oneline main..HEAD
git diff --cached --name-only
git status --short | grep -vE '^ M|^ D|^\?\?' || echo "no staged/unexpected changes"
```

Expected: six Part 11 commits (Tasks 3–8) on top of `main`, an EMPTY staged list, and no
status lines other than the known pre-existing unstaged `.gitignore` / `.qoder/repowiki`
modifications. The pre-existing unrelated working-tree changes remain uncommitted — leave them.

- [ ] **Step 5: Push and open the Part 11 PR (project convention: branch → PR → merge)**

```bash
git push origin feat/part-11-render-harness
gh pr create --base main --head feat/part-11-render-harness \
  --title "feat: Part 11 — real browser render harness (Playwright)" \
  --body "Real Playwright rendering for the repair loop: harness, HTML util, RenderCallable adapter, TS tryBrowserRender fix, docs. Spec: docs/superpowers/specs/2026-05-13-render-harness-design.md"
```

Expected: PR URL printed. Merge per the established workflow once CI/review passes
(squash or merge commit per repository convention; the branch kept one commit per task,
so either works). If `gh` is unavailable, STOP and report.

---

## Appendix A: Spec coverage check

| Spec point (`2026-05-13-render-harness-design.md`) | Plan task |
|---|---|
| 1. Python harness: real `playwright.sync_api` (launch, new_page, goto file://, networkidle, screenshot full_page, `window.__figmaforge_meta`), unchanged public API, viewport normalization `{w,h}` + `{width,height}` | Task 3 |
| 2. HTML generation util: IRDocument + styles → HTML with `#figmaforge-root` fixed to viewport + `data-node-id` | Task 4 |
| 3. Repair-loop adapter: `RenderCallable` closure via `RepairLoop(render_fn=...)`, zero loop changes | Task 5 |
| 4. TS fix: `tryBrowserRender` pipes script via stdin, parses `RenderOutput` inputs | Task 7 |
| 5. Dependencies & docs: README/CLAUDE.md setup, `docs/repair-loop.md` section, DEVELOPMENT_LOG Part 11 | Task 8 |
| 6. Testing: suites stay green (241 Python / 100 TS baseline), mocked contract tests, viewport normalization tests, skip-guarded real-browser smoke, final gate incl. `claude plugin validate --strict` | Tasks 3–7 (tests), Task 9 (gate) |
| Risk: module imports cleanly without playwright; clear error naming install command | Task 3 (`RenderHarnessError`, `test_missing_playwright_raises_clear_error`) |
| Risk: viewport key inconsistency | Task 3 (`normalize_viewport` + tests) |
| Risk: chromium availability on CI/dev | Task 3 step 5 + Task 6 (`skipUnless`) |
| Merge workflow (branch → PR → merge) | Task 1, Task 9 step 5 |
| Non-goals: `_diff_raster`, `screenshot_compare.ts` decode, Figma baseline download, backend impls, doc sweep | NOT planned — excluded per spec |

## Appendix B: Contract discrepancies found while reading the real code

1. `tests/test_render_pipeline.py::test_harness_determinism` asserts the PLACEHOLDER
   behavior (empty touched PNG); a real implementation cannot keep it passing without a
   browser, so Task 3 converts it to a `skipUnless(chromium)` real-render test. It stays
   counted in the 241 baseline (skips count toward `Ran N tests`), so the "241 OK" gate
   holds.
2. The placeholder returned `layout_metadata = {"viewport": ..., "computed_styles": {}}`;
   the spec requires node-id-keyed metadata for `DiffEngine`. The public API signature is
   unchanged but the metadata SHAPE changes (documented in the module docstring and
   `docs/repair-loop.md`).
3. `cmdRender` (the working TS reference) passes its script via `python3 -c`; the spec
   directs stdin piping, so Task 7 uses `python3 -` with `child.stdin.write(script)`.
4. `LayoutPlan.viewport` is a float width with no height field; the adapter uses a default
   height of 900 (`make_render_callable(harness, default_height=...)`).
5. `DiffEngine._diff_geometry` reports `missing_in_render` for ANY plan node absent from
   `render_meta` — including the top-level screen node. Adapter tests and any real usage
   must ensure the screen/frame-root node is rendered (it is: page children are emitted
   with `data-node-id`).
6. `playwright` is NOT currently installed in the dev environment; all browser-dependent
   paths are skip-guarded, and final-gate expectations include `(skipped=3)` in that case.
7. CLAUDE.md states "standard library only"; Task 8 records the user-approved Playwright
   exception explicitly rather than silently violating the convention.
8. After Task 7 the TS suite is 105 tests, not 100 — gate expectations in Tasks 8/9 use
   the updated counts (266 Python / 105 TS).
