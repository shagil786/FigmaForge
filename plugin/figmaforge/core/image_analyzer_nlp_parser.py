"""
Fallback parser for natural language descriptions from vision models.

When the vision model returns a text description instead of JSON,
this module extracts layout elements, colors, and typography from it.
"""

from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Common color name -> hex mapping
_COLOR_NAMES = {
    "white": "#ffffff",
    "black": "#000000",
    "red": "#ff0000",
    "blue": "#0000ff",
    "green": "#008000",
    "yellow": "#ffff00",
    "orange": "#ffa500",
    "purple": "#800080",
    "gray": "#808080",
    "grey": "#808080",
    "pink": "#ffc0cb",
    "brown": "#a52a2a",
    "navy": "#000080",
    "teal": "#008080",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "light gray": "#d3d3d3",
    "dark gray": "#a9a9a9",
    "light grey": "#d3d3d3",
    "dark grey": "#a9a9a9",
    "light blue": "#add8e6",
    "dark blue": "#00008b",
    "dark red": "#8b0000",
    "dark green": "#006400",
}

# Color extraction patterns
_COLOR_HEX_PATTERN = re.compile(r"#([0-9a-fA-F]{3,8})")
_COLOR_WORD_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_COLOR_NAMES.keys(), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_COLOR_RGB_PATTERN = re.compile(
    r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)",
    re.IGNORECASE,
)

# Font size patterns
_FONT_SIZE_PATTERN = re.compile(r"(\d+)\s*(?:px|pt|pixels?)", re.IGNORECASE)

# Font weight patterns
_BOLD_PATTERN = re.compile(r"\b(bold|heavy|black|semibold|semi-bold|600|700|800|900)\b", re.IGNORECASE)

# Layout patterns
_CENTERED_PATTERN = re.compile(r"center(?:ed|s?)?", re.IGNORECASE)
_RIGHT_PATTERN = re.compile(r"right[- ]?(?:aligned)?", re.IGNORECASE)

# Element type patterns
_TYPE_PATTERNS = {
    "text": re.compile(r"\b(title|heading|paragraph|text|label|caption|subtitle|name|description)\b", re.IGNORECASE),
    "button": re.compile(r"\b(button|link|cta|submit|action)\b", re.IGNORECASE),
    "image": re.compile(r"\b(image|photo|picture|avatar|icon|logo|illustration)\b", re.IGNORECASE),
    "frame": re.compile(r"\b(card|container|section|panel|box|wrapper|frame|group)\b", re.IGNORECASE),
}


def parse_natural_language(description: str, image_width: int = 1440, image_height: int = 900) -> Dict[str, Any]:
    """Parse a natural language description into structured element data.

    Args:
        description: The text description from the vision model.
        image_width: Estimated image width for bounding boxes.
        image_height: Estimated image height for bounding boxes.

    Returns:
        A dict with 'viewport', 'elements', 'colors', and 'fonts' keys.
    """
    elements: List[Dict[str, Any]] = []
    colors_found: set = set()
    fonts_found: set = set()

    # Split description into paragraphs/bullets
    paragraphs = _split_into_elements(description)

    # Extract viewport if mentioned
    viewport: Dict[str, int] = {"width": image_width, "height": image_height}
    vp_match = re.search(r"(\d{3,4})\s*[x\u00d7]\s*(\d{3,4})", description)
    if vp_match:
        viewport = {"width": int(vp_match.group(1)), "height": int(vp_match.group(2))}

    # Extract all colors from the entire description
    for match in _COLOR_HEX_PATTERN.finditer(description):
        hex_val = match.group(1)
        if len(hex_val) == 3:
            hex_val = "".join(c * 2 for c in hex_val)
        colors_found.add(f"#{hex_val[:6]}")

    for match in _COLOR_WORD_PATTERN.finditer(description):
        word = match.group(1).lower()
        if word in _COLOR_NAMES:
            colors_found.add(_COLOR_NAMES[word])

    for match in _COLOR_RGB_PATTERN.finditer(description):
        r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
        colors_found.add(f"#{r:02x}{g:02x}{b:02x}")

    # Extract fonts
    for font in re.findall(r"(?:font(?:\s+family)?:?\s*)(\w+(?:\s+\w+)*)", description, re.IGNORECASE):
        fonts_found.add(font.strip())

    # Build elements from paragraphs
    y_offset = 20
    for i, para_text in enumerate(paragraphs):
        if not para_text.strip():
            continue

        elem = _element_from_text(para_text, i, y_offset, viewport)
        if elem:
            elements.append(elem)
            # Track colors
            bg = elem.get("style", {}).get("background")
            if bg:
                colors_found.add(bg)
            typo = elem.get("typography", {})
            if typo.get("color"):
                colors_found.add(typo["color"])
            if typo.get("font_family"):
                fonts_found.add(typo["font_family"])

            # Advance y_offset
            y_offset += elem.get("bounds", {}).get("height", 40) + 16

    # If no elements found, create a minimal structure
    if not elements:
        elements = _create_fallback_elements(description, viewport)

    return {
        "viewport": viewport,
        "elements": elements,
        "colors": sorted(colors_found),
        "fonts": sorted(fonts_found) if fonts_found else ["system-ui"],
    }


def _split_into_elements(text: str) -> List[str]:
    """Split text description into individual element descriptions."""
    elements: List[str] = []

    lines = text.split("\n")
    current: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                elements.append("\n".join(current))
                current = []
            continue
        if re.match(r"^[\*\-\u2022]\s", stripped) or re.match(r"^\d+[\.\)]\s", stripped):
            if current:
                elements.append("\n".join(current))
            current = [stripped]
        else:
            current.append(stripped)

    if current:
        elements.append("\n".join(current))

    return elements


def _element_from_text(
    text: str,
    index: int,
    y_offset: int,
    viewport: Dict[str, int],
) -> Optional[Dict[str, Any]]:
    """Create an element dict from a text description."""
    # Determine element type
    elem_type = "text"
    for etype, pattern in _TYPE_PATTERNS.items():
        if pattern.search(text):
            elem_type = etype
            break

    # Extract text content (look for quoted text first)
    text_content = ""
    quoted = re.findall(r'["\u201c](.+?)["\u201d]', text)
    if quoted:
        text_content = quoted[0]
    else:
        reads_match = re.search(r'reads?\s+["\u201c](.+?)["\u201d]', text, re.IGNORECASE)
        if reads_match:
            text_content = reads_match.group(1)
        else:
            text_content = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
            text_content = re.sub(r"^\s*[\*\-\u2022]\s*", "", text_content)
            text_content = re.sub(r"^\s*\d+[\.\)]\s*", "", text_content)
            text_content = text_content.strip()[:100]

    if not text_content:
        return None

    # Determine if bold
    is_bold = bool(_BOLD_PATTERN.search(text))
    font_weight = 700 if is_bold else 400

    # Determine font size
    font_size = 16
    if elem_type == "text":
        lower_text = text.lower()
        if any(w in lower_text for w in ["title", "heading", "header", "main"]):
            font_size = 32 if "large" in lower_text or "big" in lower_text else 24
        elif any(w in lower_text for w in ["subtitle", "subheading"]):
            font_size = 20
        elif any(w in lower_text for w in ["caption", "small", "label"]):
            font_size = 12
        elif is_bold:
            font_size = 20
    elif elem_type == "button":
        font_size = 14

    size_match = _FONT_SIZE_PATTERN.search(text)
    if size_match:
        font_size = int(size_match.group(1))

    # Determine color
    color = "#000000"
    hex_match = _COLOR_HEX_PATTERN.search(text)
    if hex_match:
        hex_val = hex_match.group(1)
        if len(hex_val) == 3:
            hex_val = "".join(c * 2 for c in hex_val)
        color = f"#{hex_val[:6]}"
    else:
        for word_match in _COLOR_WORD_PATTERN.finditer(text):
            word = word_match.group(1).lower()
            if word in _COLOR_NAMES:
                color = _COLOR_NAMES[word]
                break

    # Determine alignment
    align = "left"
    if _CENTERED_PATTERN.search(text):
        align = "center"
    elif _RIGHT_PATTERN.search(text):
        align = "right"

    # Determine background
    background = None
    bg_match = re.search(
        r"(?:background|bg)(?:-color)?:\s*([#a-z0-9(),.\s]+)",
        text,
        re.IGNORECASE,
    )
    if bg_match:
        bg_str = bg_match.group(1).strip()
        hex_m = _COLOR_HEX_PATTERN.search(bg_str)
        if hex_m:
            hv = hex_m.group(1)
            if len(hv) == 3:
                hv = "".join(c * 2 for c in hv)
            background = f"#{hv[:6]}"
        else:
            word_m = _COLOR_WORD_PATTERN.search(bg_str)
            if word_m and word_m.group(1).lower() in _COLOR_NAMES:
                background = _COLOR_NAMES[word_m.group(1).lower()]
    else:
        lower = text.lower()
        if "background" in lower and "white" in lower:
            background = "#ffffff"
        elif "background" in lower and "black" in lower:
            background = "#000000"

    # Compute bounds
    char_width = font_size * 0.6
    text_width = min(len(text_content) * char_width, viewport["width"] - 80)
    height = font_size * 1.5

    if elem_type == "button":
        text_width = max(text_width + 40, 120)
        height = max(height + 16, 44)

    x = 40
    if align == "center":
        x = (viewport["width"] - text_width) // 2

    elem: Dict[str, Any] = {
        "id": f"elem_{index}",
        "name": text_content[:30].lower().replace(" ", "-"),
        "type": elem_type,
        "bounds": {
            "x": x,
            "y": y_offset,
            "width": text_width,
            "height": height,
        },
        "style": {
            "background": background,
            "border_radius": 8 if elem_type == "button" else None,
            "border": None,
            "shadow": None,
            "opacity": 1,
        },
        "layout": {
            "display": "block",
            "direction": None,
            "justify": "center" if align == "center" else None,
            "align": "center" if elem_type == "button" else None,
            "padding": 12 if elem_type == "button" else 0,
            "gap": None,
        },
        "typography": {
            "text": text_content,
            "font_family": "system-ui",
            "font_size": font_size,
            "font_weight": font_weight,
            "color": color,
            "align": align,
            "line_height": 1.5,
        },
        "children": [],
        "confidence": 0.7,
    }

    return elem


def _create_fallback_elements(description: str, viewport: Dict[str, int]) -> List[Dict[str, Any]]:
    """Create minimal elements when parsing fails completely."""
    texts = re.findall(r'["\u201c](.+?)["\u201d]', description)
    if not texts:
        texts = re.findall(r'reads?\s+["\u201c](.+?)["\u201d]', description, re.IGNORECASE)
    if not texts:
        texts = ["Page content"]

    elements: List[Dict[str, Any]] = []
    y = 20
    for i, text in enumerate(texts[:10]):
        width = min(len(text) * 10, viewport["width"] - 80)
        elements.append({
            "id": f"elem_{i}",
            "name": text[:30].lower().replace(" ", "-"),
            "type": "text",
            "bounds": {"x": 40, "y": y, "width": width, "height": 24},
            "style": {
                "background": None,
                "border_radius": None,
                "border": None,
                "shadow": None,
                "opacity": 1,
            },
            "layout": {
                "display": "block",
                "direction": None,
                "justify": None,
                "align": None,
                "padding": 0,
                "gap": None,
            },
            "typography": {
                "text": text,
                "font_family": "system-ui",
                "font_size": 16,
                "font_weight": 400,
                "color": "#000000",
                "align": "left",
                "line_height": 1.5,
            },
            "children": [],
            "confidence": 0.5,
        })
        y += 40

    return elements
