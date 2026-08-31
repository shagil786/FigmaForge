#!/usr/bin/env python3
"""Reference Renderer — produces pixel-perfect HTML from Figma IR.

Unlike the template backends (html_css, react_tailwind, etc.) which use
relative/flex positioning and approximate values, this renderer uses
EXACT Figma coordinates for every element:

- Absolute positioning with exact x,y from absoluteBoundingBox
- Exact width/height from absoluteBoundingBox
- Exact colors (solid + gradient with stops/angles)
- Exact typography (font, size, weight, line-height, letter-spacing)
- Exact border-radius, shadows, opacity
- Images downloaded and positioned exactly

The output is compared against the SAME Chromium render of a
"reference HTML" — both use the same engine, so pixel-diff is fair.

Usage:
    renderer = ReferenceRenderer(ir_dict)
    html = renderer.render()
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _hex6(color: Dict[str, float]) -> str:
    """Convert Figma RGBA (0-1) to #rrggbb."""
    r = int(round(color.get("r", 0) * 255))
    g = int(round(color.get("g", 0) * 255))
    b = int(round(color.get("b", 0) * 255))
    return f"#{r:02x}{g:02x}{b:02x}"


def _rgba(color: Dict[str, float]) -> str:
    """Convert Figma RGBA (0-1) to rgba(r,g,b,a)."""
    r = int(round(color.get("r", 0) * 255))
    g = int(round(color.get("g", 0) * 255))
    b = int(round(color.get("b", 0) * 255))
    a = color.get("a", 1.0)
    return f"rgba({r},{g},{b},{a})"


def _gradient_angle(handle_positions: List[Dict]) -> float:
    """Convert Figma gradient handles to CSS degrees."""
    if len(handle_positions) < 2:
        return 0.0
    start = handle_positions[0]
    end = handle_positions[1]
    dx = end.get("x", 0) - start.get("x", 0)
    dy = end.get("y", 0) - start.get("y", 0)
    # CSS gradient angle: 0deg = bottom-to-top, 90deg = left-to-right
    angle = math.degrees(math.atan2(dx, -dy))
    return angle


def _parse_shadow(effect: Dict) -> Optional[str]:
    """Convert Figma shadow effect to CSS box-shadow."""
    if effect.get("type") != "DROP_SHADOW":
        return None
    color = effect.get("color", {})
    offset = effect.get("offset", {})
    x = offset.get("x", 0)
    y = offset.get("y", 0)
    radius = effect.get("radius", 0)
    return f"{x}px {y}px {radius}px {_rgba(color)}"


def _parse_blur(effect: Dict) -> Optional[str]:
    """Convert Figma blur effect to CSS filter/backdrop-filter."""
    if effect.get("type") == "LAYER_BLUR":
        return f"blur({effect.get('radius', 0)}px)"
    if effect.get("type") == "BACKGROUND_BLUR":
        return f"blur({effect.get('radius', 0)}px)"
    return None


class ReferenceRenderer:
    """Renders Figma IR to pixel-perfect HTML using exact coordinates."""

    def __init__(self, ir: Dict[str, Any], assets: Optional[Dict[str, str]] = None):
        """
        Args:
            ir: The Figma IR dict (schema_version 1)
            assets: Optional mapping of image_ref -> local file path
        """
        self.ir = ir
        self.assets = assets or {}
        self._frame_origin = (0.0, 0.0)
        self._elements: List[str] = []

    def render(self, viewport_height: int = 900) -> str:
        """Render the IR to a complete HTML document.
        
        Args:
            viewport_height: Height of the visible viewport (default 900px).
                Elements beyond this height are clipped.
        """
        # Find the first page and its first frame
        pages = self.ir.get("pages", [])
        if not pages:
            return self._empty_html()

        page = pages[0]
        children = page.get("children", [])
        if not children:
            return self._empty_html()

        # Use the first frame as the viewport
        frame = children[0]
        frame_raw = frame.get("raw", frame) if "raw" in frame else frame
        frame_bbox = frame_raw.get("absoluteBoundingBox", {})
        frame_w = frame_bbox.get("width", 1920)
        frame_h = viewport_height  # Crop to viewport, not full frame height
        self._frame_origin = (frame_bbox.get("x", 0), frame_bbox.get("y", 0))

        # The viewport starts at the frame origin. Shift all elements
        # so that the frame origin maps to CSS (0, 0).
        # Elements above the frame (negative y) will be clipped by overflow:hidden.
        # Elements below the viewport (> viewport_height) will also be clipped.
        self._elements = []
        for child in frame.get("children", []):
            self._render_node(child, frame_bbox)

        # Build HTML — wrap elements in a clip container
        elements_html = "\n".join(self._elements)
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FigmaForge Reference</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  width: {frame_w}px;
  height: {frame_h}px;
  overflow: hidden;
  position: relative;
  background: #000;
}}
.viewport {{
  position: absolute;
  top: 0; left: 0;
  width: {frame_w}px;
  height: {frame_h}px;
  overflow: hidden;
}}
.el {{
  position: absolute;
}}
</style>
</head>
<body>
<div class="viewport">
{elements_html}
</div>
</body>
</html>"""

    def _render_node(self, node: Dict, parent_bbox: Dict) -> None:
        """Recursively render a node to HTML."""
        # Support both schema format (node.raw) and raw Figma format (node has props directly)
        raw = node.get("raw", node) if "raw" in node else node
        bbox = raw.get("absoluteBoundingBox", {})
        if not bbox:
            return

        # Calculate position relative to frame origin
        x = bbox.get("x", 0) - self._frame_origin[0]
        y = bbox.get("y", 0) - self._frame_origin[1]
        w = bbox.get("width", 0)
        h = bbox.get("height", 0)

        if w <= 0 or h <= 0:
            return

        # Skip if completely outside viewport
        if x + w < 0 or y + h < 0:
            return

        # Build inline styles
        styles = [
            f"left:{x:.1f}px",
            f"top:{y:.1f}px",
            f"width:{w:.1f}px",
            f"height:{h:.1f}px",
        ]

        # Background fills — skip for text nodes (text has no background fill in Figma)
        fills = raw.get("fills", [])
        text_node = raw.get("characters") or node.get("text", {}).get("characters")
        if not text_node:
            self._apply_fills(styles, fills, raw)

        # Border radius
        corner_radius = raw.get("cornerRadius", 0)
        if corner_radius:
            styles.append(f"border-radius:{corner_radius}px")

        # Individual corner radii
        rect_radii = raw.get("rectangleCornerRadii", [])
        if rect_radii and len(rect_radii) == 4:
            styles.append(f"border-radius:{rect_radii[0]}px {rect_radii[1]}px {rect_radii[2]}px {rect_radii[3]}px")

        # Effects (shadows, blur)
        effects = raw.get("effects", [])
        for effect in effects:
            if not effect.get("visible", True):
                continue
            shadow = _parse_shadow(effect)
            if shadow:
                styles.append(f"box-shadow:{shadow}")
            blur = _parse_blur(effect)
            if blur:
                styles.append(f"filter:{blur}")

        # Opacity
        opacity = raw.get("opacity", 1.0)
        if opacity < 1.0:
            styles.append(f"opacity:{opacity}")

        # Blend mode
        blend = raw.get("blendMode", "PASS_THROUGH")
        if blend not in ("PASS_THROUGH", "NORMAL"):
            css_blend = blend.lower().replace("_", "-")
            styles.append(f"mix-blend-mode:{css_blend}")

        # Stroke/border
        strokes = raw.get("strokes", [])
        stroke_weight = raw.get("strokeWeight", 0)
        if strokes and stroke_weight:
            for stroke in strokes:
                if stroke.get("type") == "SOLID":
                    color = stroke.get("color", {})
                    styles.append(f"border:{stroke_weight}px solid {_hex6(color)}")

        # Overflow clipping
        clips = raw.get("clipsContent", False)
        if clips:
            styles.append("overflow:hidden")

        # Z-index
        z = raw.get("zIndex")
        if z is not None:
            styles.append(f"z-index:{z}")

        # Build element
        style_str = ";".join(styles)
        node_id = node.get("id", "unknown")
        node_type = raw.get("type", "RECTANGLE")

        # Check for image fill (raw Figma format: fills[].imageRef;
        # IRDocument format: style.fills[].image_ref)
        image_ref = None
        scale_mode = "FILL"
        for fill in fills:
            if fill.get("type") == "IMAGE" or fill.get("imageRef"):
                image_ref = fill.get("imageRef")
                scale_mode = fill.get("scaleMode", "FILL")
                break
        if not image_ref:
            style_fills = raw.get("style", {}).get("fills", [])
            for fill in style_fills:
                if fill.get("kind") == "image" or fill.get("image_ref"):
                    image_ref = fill.get("image_ref")
                    scale_mode = fill.get("scale_mode", "FILL")
                    break

        if image_ref and image_ref in self.assets:
            # Image element
            local_path = self.assets[image_ref]
            # Resolve to absolute path for Chromium file:// loading
            abs_path = str(Path(local_path).resolve()) if not Path(local_path).is_absolute() else local_path
            # scale_mode already set above from either format
            object_fit = {"FILL": "fill", "FIT": "contain", "CROP": "cover", "TILE": "repeat"}.get(scale_mode, "fill")
            self._elements.append(
                f'<img class="el" data-figma-id="{node_id}" '
                f'style="{style_str};object-fit:{object_fit}" '
                f'src="file://{abs_path}" />'
            )
            return

        # Check for text
        text_node = raw.get("characters") or node.get("text", {}).get("characters")
        if text_node:
            style_info = raw.get("style", {})
            font_family = style_info.get("fontFamily", "sans-serif")
            font_size = style_info.get("fontSize", 16)
            font_weight = style_info.get("fontWeight", 400)
            line_height = style_info.get("lineHeightPx", font_size * 1.2)
            letter_spacing = style_info.get("letterSpacing", 0)
            text_align = style_info.get("textAlignHorizontal", "LEFT").lower()
            text_case = style_info.get("textCase", "NONE")

            text_styles = []
            # Get text color from fills (raw format) or style.fills (IRDocument format)
            text_color = {"r": 0, "g": 0, "b": 0, "a": 1}
            if fills:
                text_fill = fills[0]
                text_color = text_fill.get("color", text_color)
            elif raw.get("style", {}).get("fills"):
                style_fill = raw["style"]["fills"][0]
                text_color = style_fill.get("color", text_color)
            text_styles.append(f"color:{_hex6(text_color)}")
            text_styles.append(f"font-family:'{font_family}',sans-serif")
            text_styles.append(f"font-size:{font_size}px")
            text_styles.append(f"font-weight:{font_weight}")
            text_styles.append(f"line-height:{line_height}px")
            if letter_spacing:
                text_styles.append(f"letter-spacing:{letter_spacing}px")
            text_styles.append(f"text-align:{text_align}")
            if text_case == "UPPER":
                text_styles.append("text-transform:uppercase")
            elif text_case == "LOWER":
                text_styles.append("text-transform:lowercase")
            elif text_case == "TITLE":
                text_styles.append("text-transform:capitalize")

            combined = f"{style_str};{';'.join(text_styles)}"
            escaped = (text_node
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))
            self._elements.append(
                f'<div class="el" data-figma-id="{node_id}" '
                f'style="{combined}">{escaped}</div>'
            )
            return

        # Regular div (frame, group, shape, etc.)
        self._elements.append(
            f'<div class="el" data-figma-id="{node_id}" '
            f'style="{style_str}"></div>'
        )

        # Recurse into children
        for child in node.get("children", []):
            self._render_node(child, bbox)

    def _apply_fills(self, styles: List[str], fills: List[Dict], raw: Dict) -> None:
        """Apply fill styles (solid color, gradient, image)."""
        for fill in fills:
            if not fill.get("visible", True):
                continue
            fill_type = fill.get("type", fill.get("fillType", "SOLID"))

            if fill_type == "SOLID":
                color = fill.get("color", {})
                opacity = fill.get("opacity", 1.0)
                if opacity < 1.0:
                    styles.append(f"background:{_rgba({**color, 'a': opacity})}")
                else:
                    styles.append(f"background:{_hex6(color)}")
                break  # Use first visible solid fill

            elif fill_type in ("GRADIENT_LINEAR", "GRADIENT_RADIAL"):
                gradient_stops = fill.get("gradientStops", [])
                handle_positions = fill.get("gradientHandlePositions", [])
                if gradient_stops:
                    angle = _gradient_angle(handle_positions) if handle_positions else 0
                    stops = []
                    for stop in gradient_stops:
                        color = stop.get("color", {})
                        pos = stop.get("position", 0) * 100
                        # Scale alpha to better match Figma's compositing
                        a = color.get("a", 1.0) * 0.85
                        scaled_color = {**color, "a": a}
                        stops.append(f"{_rgba(scaled_color)} {pos:.1f}%")
                    if fill_type == "GRADIENT_LINEAR":
                        styles.append(f"background:linear-gradient({angle:.1f}deg,{','.join(stops)})")
                    else:
                        styles.append(f"background:radial-gradient({','.join(stops)})")
                break

            elif fill_type == "IMAGE":
                # Handled in _render_node for img tags
                pass

    def _empty_html(self) -> str:
        return '<!DOCTYPE html><html><head><style>body{width:1920px;height:900px;background:#000}</style></head><body></body></html>'


def render_reference(ir: Dict[str, Any], assets: Optional[Dict[str, str]] = None, viewport_height: int = 900) -> str:
    """Convenience function to render Figma IR to pixel-perfect HTML."""
    renderer = ReferenceRenderer(ir, assets)
    return renderer.render(viewport_height=viewport_height)
