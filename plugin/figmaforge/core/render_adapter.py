"""
Repair-loop render adapter (Part 11).

Bridges the real :class:`core.render_harness.RenderHarness` into the Part 8
``RepairLoop`` via the existing ``RenderCallable`` dependency-injection point
— zero changes to ``repair_loop.py`` internals.

Standard library only.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Protocol, Tuple

from .generator_types import VStyle
from .ir_types import IRDocument
from .layout_types import LayoutPlan
from .render_harness import RenderResult
from .render_html import generate_render_html

DEFAULT_VIEWPORT_WIDTH = 1440
DEFAULT_VIEWPORT_HEIGHT = 900


class RenderHarnessLike(Protocol):
    """Structural type for anything that can render HTML to metadata."""

    def render(
        self,
        content_html: str,
        viewport_spec: Dict[str, int],
        build_id: str,
        full_page: bool = True,
    ) -> RenderResult: ...


def make_render_callable(
    harness: RenderHarnessLike,
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
        width = int(plan.viewport) if plan.viewport and plan.viewport > 0 else DEFAULT_VIEWPORT_WIDTH
        # Keep Chromium's interactive viewport bounded.  For a full-page
        # Figma baseline, ask Playwright for a full-document screenshot
        # instead of creating a 4600px-tall browser viewport, which can hang
        # or exhaust the renderer on macOS.  Short viewport comparisons retain
        # the fixed-viewport behavior used by the existing tests.
        full_page = int(default_height) > DEFAULT_VIEWPORT_HEIGHT
        browser_height = (
            DEFAULT_VIEWPORT_HEIGHT if full_page else int(default_height)
        )
        viewport = {"width": width, "height": browser_height}
        content_html = generate_render_html(document, styles, viewport)
        try:
            result = harness.render(
                content_html, viewport, build_id=f"repair-iter-{iteration}",
                full_page=full_page,
                tiled=full_page,
            )
        except TypeError as exc:
            # Keep injected/third-party harnesses from older FigmaForge
            # versions usable.  ``tiled`` is an optional capability; only
            # retry when the harness explicitly rejects that keyword, and
            # preserve every other TypeError as a real render failure.
            if "tiled" not in str(exc):
                raise
            result = harness.render(
                content_html, viewport, build_id=f"repair-iter-{iteration}",
                full_page=full_page,
            )
        return result.layout_metadata, str(result.screenshot_path)

    return render_fn
