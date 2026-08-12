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

    Renders the children of the first page (``document.pages[0]``, or
    ``document.root`` itself as a fallback) into ``#figmaforge-root``.
    ``viewport_spec`` accepts ``{"w", "h"}`` or ``{"width", "height"}``
    keys.
    """
    viewport = normalize_viewport(viewport_spec)

    page: Optional[IRNode] = None
    if document.pages:
        page = document.pages[0]
    elif document.root is not None:
        page = document.root

    body_html = ""
    if page is not None:
        # Guard against pathological IR trees: convert raw RecursionError
        # into a domain error (simpler than iterative stack-based rendering).
        try:
            body_html = "".join(
                _node_to_html(child, styles) for child in page.children
            )
        except RecursionError as exc:
            raise ValueError("IR tree too deep to render") from exc

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
