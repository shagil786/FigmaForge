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
