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

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict


class RenderHarnessError(RuntimeError):
    """Raised when the harness cannot produce a browser render."""


PLAYWRIGHT_INSTALL_HINT = (
    "playwright is required for browser rendering. "
    "Install it with: pip install playwright && playwright install chromium"
)

BUILD_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]+")


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
        width = int(width)
        height = int(height)
    except (TypeError, ValueError):
        raise ValueError(
            f"viewport dimensions must be numeric, got {viewport_spec!r}"
        ) from None
    if width <= 0 or height <= 0:
        raise ValueError(
            f"viewport dimensions must be positive, got {width}x{height}"
        )
    return {"width": width, "height": height}


@dataclass
class RenderResult:
    screenshot_path: Path
    layout_metadata: Dict[str, Any]
    accessibility_findings: list[Dict[str, Any]] = field(default_factory=list)


def _browser_accessibility_script() -> str:
    """Return a dependency-free browser accessibility smoke audit.

    This is intentionally a small deterministic guardrail, not a replacement
    for axe-core. It catches common generated-markup regressions while keeping
    the render harness usable without adding a browser-side dependency.
    """
    return """() => {
      const findings = [];
      const name = (el) => (el.getAttribute('aria-label') ||
        el.getAttribute('title') || el.textContent || '').trim();
      if (!document.documentElement.getAttribute('lang')) {
        findings.push({rule: 'document_lang', severity: 'error',
          message: 'Document has no lang attribute'});
      }
      document.querySelectorAll('img').forEach((el) => {
        if (!el.hasAttribute('alt')) findings.push({rule: 'image_alt', severity: 'error',
          message: 'Image has no alt attribute'});
      });
      document.querySelectorAll('button, [role="button"], a[href]').forEach((el) => {
        if (!name(el)) findings.push({rule: 'interactive_name', severity: 'error',
          message: 'Interactive element has no accessible name'});
      });
      document.querySelectorAll('input, select, textarea').forEach((el) => {
        const id = el.getAttribute('id');
        const labelled = el.getAttribute('aria-label') ||
          (id && document.querySelector(`label[for="${CSS.escape(id)}"]`));
        if (!labelled) findings.push({rule: 'form_label', severity: 'error',
          message: 'Form control has no accessible label'});
      });
      return findings;
    }"""


class RenderHarness:
    """Wrapper to interact with a Playwright rendering context."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render(
        self,
        content_html: str,
        viewport_spec: Dict[str, int],
        build_id: str,
        full_page: bool = True,
    ) -> RenderResult:
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
        if not BUILD_ID_PATTERN.fullmatch(build_id):
            raise ValueError(
                f"build_id must match ^[A-Za-z0-9._-]+$, got {build_id!r}"
            )

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
                launch_args = (["--no-sandbox"]
                               if os.environ.get("FIGMAFORGE_PLAYWRIGHT_NO_SANDBOX") == "1"
                               else [])
                browser = p.chromium.launch(**({"args": launch_args} if launch_args else {}))
                try:
                    page = browser.new_page(
                        viewport=viewport, device_scale_factor=1
                    )
                    page.goto(html_path.as_uri(), timeout=15_000)
                    page.wait_for_load_state("networkidle", timeout=15_000)
                    # Deterministic capture: wait for fonts before shooting.
                    page.evaluate("document.fonts.ready")
                    accessibility_findings = page.evaluate(_browser_accessibility_script())
                    page.screenshot(
                        path=str(screenshot_path), full_page=full_page
                    )
                    meta = page.evaluate("window.__figmaforge_meta || {}")
                finally:
                    try:
                        browser.close()
                    except Exception:
                        pass
        except Exception as exc:
            raise RenderHarnessError(
                f"browser rendering failed: {exc} — if chromium is not "
                "installed, run: playwright install chromium"
            ) from exc

        if not isinstance(meta, dict):
            meta = {}
        if not isinstance(accessibility_findings, list):
            accessibility_findings = []

        return RenderResult(
            screenshot_path=screenshot_path,
            layout_metadata=meta,
            accessibility_findings=accessibility_findings,
        )
