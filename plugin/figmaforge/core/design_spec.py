"""
Semantic Design Spec generator.

Converts a design IR (as produced by ``pipeline.py normalize``) into a
structured, agent-readable JSON document that an AI code-generation agent
can consume as context.

The spec captures:

- **Page metadata** — name, viewport dimensions
- **Design tokens** — extracted color palette, typography scale, spacing
- **Semantic sections** — navigation, hero, features, CTA, footer, etc.
  Each section carries:
  - ``type`` — inferred from node naming conventions and structure
  - ``layout`` — ``flex-row``, ``flex-column``, ``grid``, or ``stack``
  - ``content`` — child elements with type, text, styling metadata
- **Layout intent** — direction, gap, padding per section

Design goals:

- Pure stdlib, no external deps.
- Deterministic output (same IR → same spec, ``sort_keys=True``).
- Never invents semantics — if a section can't be classified, it's
  ``"content"`` with a descriptive name.
- Tokens are deduplicated; colors are ``#rrggbb`` hex strings.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import json
import re


# ---------------------------------------------------------------------------
# Section type inference
# ---------------------------------------------------------------------------

class SectionType(str, Enum):
    """Inferred semantic section types."""
    NAVIGATION = "navigation"
    HERO = "hero"
    FEATURES = "features"
    CTA = "cta"
    TESTIMONIALS = "testimonials"
    PRICING = "pricing"
    FOOTER = "footer"
    SIDEBAR = "sidebar"
    CONTENT = "content"
    STATS = "stats"
    FAQ = "faq"
    GALLERY = "gallery"
    FORM = "form"


# Name → SectionType mapping (case-insensitive, partial match).
_NAME_PATTERNS: List[Tuple[str, SectionType]] = [
    (r"\bheader\b|\bnav\b|\bnavbar\b|\bmenu\b|\btop.?bar\b", SectionType.NAVIGATION),
    (r"\bhero\b|\bbanner\b|\bjumbotron\b|\babove.?fold\b", SectionType.HERO),
    (r"\bfeature|\bservice\b|\bcapabilit\b|\bbenefit\b", SectionType.FEATURES),
    (r"\bcta\b|\bcall.?to.?action\b|\bsign.?up\b|\bstart\b", SectionType.CTA),
    (r"\btestimonial\b|\breview\b|\bquote\b|\bcase.?study\b", SectionType.TESTIMONIALS),
    (r"\bpricing\b|\bplan\b|\btier\b", SectionType.PRICING),
    (r"\bfooter\b|\bbottom\b|\bcopy\b|\blegal\b", SectionType.FOOTER),
    (r"\bsidebar\b|\bside.?panel\b|\bdrawer\b", SectionType.SIDEBAR),
    (r"\bstat\b|\bmetric\b|\bnumber\b|\bcounter\b", SectionType.STATS),
    (r"\bfaq\b|\bquestion\b|\baccordion\b", SectionType.FAQ),
    (r"\bgallery\b|\bportfolio\b|\bgrid\b|\bshowcase\b", SectionType.GALLERY),
    (r"\bform\b|\binput\b|\blogin\b|\bsignin\b|\bsignup\b", SectionType.FORM),
]


def _infer_section_type(name: str, children: List[Dict[str, Any]]) -> str:
    """Infer the semantic type of a section from its name and children."""
    lower = name.lower()
    for pattern, stype in _NAME_PATTERNS:
        if re.search(pattern, lower):
            return stype.value
    # Fallback: if name contains "section" or "content", it's generic content
    if re.search(r"\bsection\b|\bcontent\b|\bblock\b", lower):
        return SectionType.CONTENT.value
    return SectionType.CONTENT.value


# ---------------------------------------------------------------------------
# Color extraction helpers
# ---------------------------------------------------------------------------

def _color_to_hex(r: float, g: float, b: float, a: float = 1.0) -> str:
    """Convert 0..1 RGBA floats to #rrggbb (or #rrggbbaa if not opaque)."""
    ri = max(0, min(255, round(r * 255)))
    gi = max(0, min(255, round(g * 255)))
    bi = max(0, min(255, round(b * 255)))
    if a < 0.99:
        ai = max(0, min(255, round(a * 255)))
        return f"#{ri:02x}{gi:02x}{bi:02x}{ai:02x}"
    return f"#{ri:02x}{gi:02x}{bi:02x}"


def _extract_fill_color(fills: List[Dict[str, Any]]) -> Optional[str]:
    """Extract the first solid fill color as hex."""
    for fill in fills:
        if fill.get("kind") == "solid" and fill.get("color"):
            c = fill["color"]
            return _color_to_hex(c.get("r", 0), c.get("g", 0), c.get("b", 0), c.get("a", 1))
    return None


def _extract_fill_image(fills: List[Dict[str, Any]]) -> Optional[str]:
    """Extract the first image fill reference."""
    for fill in fills:
        if fill.get("kind") == "image" and fill.get("image_ref"):
            return fill["image_ref"]
    return None


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------

def _extract_text_content(node: Dict[str, Any]) -> Optional[str]:
    """Extract text characters from a text node.

    The IR stores text in ``node["text"]["characters"]`` and also in
    ``node["raw"]["characters"]`` (the latter is the raw Figma payload).
    We try both for robustness.
    """
    text = node.get("text")
    if text and isinstance(text, dict):
        chars = text.get("characters", "")
        if chars:
            return chars
    # Fallback: raw Figma payload
    raw = node.get("raw")
    if raw and isinstance(raw, dict):
        chars = raw.get("characters", "")
        if chars:
            return chars
    return None


def _is_button_like(node: Dict[str, Any]) -> bool:
    """Heuristic: is this node a button or CTA?"""
    name = node.get("name", "").lower()
    if re.search(r"\bbutton\b|\bbtn\b|\bcta\b|\baction\b|\bstart\b|\bsign\b", name):
        return True
    # A frame with a single text child and a fill is likely a button
    style = node.get("style", {})
    fills = style.get("fills", []) if style else []
    children = node.get("children", [])
    if fills and len(children) == 1:
        child = children[0]
        if child.get("kind") == "text" and child.get("text"):
            return True
    return False


def _extract_content(node: Dict[str, Any], depth: int = 0) -> List[Dict[str, Any]]:
    """Extract content elements from a node's children."""
    if depth > 5:
        return []

    content = []
    children = node.get("children", [])

    for child in children:
        kind = child.get("kind", "")
        name = child.get("name", "")
        text = _extract_text_content(child)

        if kind == "text" and text:
            # Determine element type from font size in raw.style (IR format)
            raw_style = (child.get("raw") or {}).get("style", {})
            font_size = raw_style.get("fontSize")
            font_weight = raw_style.get("fontWeight")

            elem_type = "paragraph"
            if font_size and isinstance(font_size, (int, float)):
                if font_size >= 32:
                    elem_type = "heading"
                elif font_size >= 20:
                    elem_type = "subheading"
            if font_weight and isinstance(font_weight, (int, float)) and font_weight >= 700:
                if elem_type == "paragraph":
                    elem_type = "label"

            entry: Dict[str, Any] = {"type": elem_type, "text": text}
            if font_size:
                entry["fontSize"] = font_size
            if font_weight:
                entry["fontWeight"] = font_weight
            # Color from fills (IR style.fills or raw.fills)
            style = child.get("style", {})
            fills = (style.get("fills", []) if style else []) or (child.get("raw") or {}).get("fills", [])
            color = _extract_fill_color(fills)
            if color:
                entry["color"] = color
            content.append(entry)

        elif kind == "frame" and _is_button_like(child):
            label = text or name
            for gc in child.get("children", []):
                t = _extract_text_content(gc)
                if t:
                    label = t
                    break
            style = child.get("style", {})
            fills = (style.get("fills", []) if style else []) or (child.get("raw") or {}).get("fills", [])
            bg = _extract_fill_color(fills)
            raw_style = (child.get("raw") or {}).get("style", {})
            border_radius = raw_style.get("borderRadius")
            entry = {"type": "button", "text": label}
            if bg:
                entry["background"] = bg
            if border_radius:
                entry["borderRadius"] = border_radius
            content.append(entry)

        elif kind == "frame":
            # Check if this is a nav-links container
            lower_name = name.lower()
            is_nav_container = re.search(r"\bnav\b|\blink|\bmenu\b", lower_name)

            # Check if this is an image container
            style = child.get("style", {})
            fills = (style.get("fills", []) if style else []) or (child.get("raw") or {}).get("fills", [])
            image_ref = _extract_fill_image(fills)
            if image_ref:
                content.append({"type": "image", "imageRef": image_ref, "name": name})
            elif is_nav_container:
                # Extract nav links from this container
                for nav_child in child.get("children", []):
                    nav_text = _extract_text_content(nav_child)
                    if nav_text:
                        content.append({"type": "nav-link", "text": nav_text})
            else:
                # Check if this is a button-like frame (CTA group, etc.)
                if _is_button_like(child):
                    label = text or name
                    for gc in child.get("children", []):
                        t = _extract_text_content(gc)
                        if t:
                            label = t
                            break
                    content.append({"type": "button", "text": label})
                else:
                    nested = _extract_content(child, depth + 1)
                    if nested:
                        entry = {
                            "type": "group",
                            "name": name,
                            "layout": _layout_from_node(child),
                            "content": nested,
                        }
                        content.append(entry)

    return content


# ---------------------------------------------------------------------------
# Layout direction mapping
# ---------------------------------------------------------------------------

def _layout_from_node(node: Dict[str, Any]) -> str:
    """Map IR layout mode/direction to a semantic layout string."""
    layout = node.get("layout")
    if not layout:
        return "stack"

    mode = layout.get("mode", "")
    direction = layout.get("direction", "")

    if mode == "auto":
        if direction == "row":
            return "flex-row"
        elif direction == "column":
            return "flex-column"
        return "flex-row"  # default for auto-layout
    elif mode == "grid":
        return "grid"
    return "stack"


def _extract_gap(node: Dict[str, Any]) -> Optional[float]:
    """Extract the gap value from a node's layout."""
    layout = node.get("layout")
    if layout and layout.get("gap") is not None:
        return layout["gap"]
    return None


def _extract_padding(node: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Extract padding from a node's layout."""
    layout = node.get("layout")
    if layout and layout.get("padding"):
        p = layout["padding"]
        result = {}
        for key in ("top", "right", "bottom", "left"):
            val = p.get(key)
            if val is not None and val > 0:
                result[key] = val
        return result if result else None
    return None


# ---------------------------------------------------------------------------
# Design token extraction
# ---------------------------------------------------------------------------

def _collect_colors(node: Dict[str, Any], colors: Dict[str, int]) -> None:
    """Recursively collect all unique colors from the IR tree."""
    style = node.get("style", {})
    fills = style.get("fills", []) if style else []
    color = _extract_fill_color(fills)
    if color:
        colors[color] = colors.get(color, 0) + 1

    for child in node.get("children", []):
        _collect_colors(child, colors)


def _collect_typography(node: Dict[str, Any], typo_map: Dict[str, Dict[str, Any]]) -> None:
    """Recursively collect unique typography styles.

    Typography lives in ``raw.style`` for text nodes in the IR.
    """
    kind = node.get("kind", "")
    if kind == "text":
        # Try raw.style first (the IR's canonical typography source)
        raw_style = (node.get("raw") or {}).get("style", {})
        # Also try style.typography for compatibility
        style = node.get("style", {})
        typo = style.get("typography", {}) if style else {}
        if not typo and raw_style:
            typo = raw_style
        if typo:
            key_parts = []
            for k in ("fontFamily", "font_family", "fontSize", "font_size",
                       "fontWeight", "font_weight", "lineHeight", "line_height"):
                v = typo.get(k)
                if v is not None:
                    key_parts.append(f"{k}={v}")
            key = "|".join(key_parts) if key_parts else "default"
            if key not in typo_map:
                entry: Dict[str, Any] = {}
                family = typo.get("fontFamily") or typo.get("font_family")
                size = typo.get("fontSize") or typo.get("font_size")
                weight = typo.get("fontWeight") or typo.get("font_weight")
                lh = typo.get("lineHeight") or typo.get("line_height")
                if family:
                    entry["fontFamily"] = family
                if size:
                    entry["fontSize"] = size
                if weight:
                    entry["fontWeight"] = weight
                if lh:
                    entry["lineHeight"] = lh
                typo_map[key] = entry

    for child in node.get("children", []):
        _collect_typography(child, typo_map)


def _build_design_tokens(root: Dict[str, Any]) -> Dict[str, Any]:
    """Extract design tokens from the IR tree."""
    # Collect all colors
    color_counts: Dict[str, int] = {}
    _collect_colors(root, color_counts)
    # Sort by frequency (most used first), limit to 20
    sorted_colors = sorted(color_counts.items(), key=lambda x: -x[1])[:20]
    colors = [{"value": c, "count": n} for c, n in sorted_colors]

    # Collect typography
    typo_map: Dict[str, Dict[str, Any]] = {}
    _collect_typography(root, typo_map)
    typography = list(typo_map.values())

    return {
        "colors": colors,
        "typography": typography,
    }


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

class DesignSpecGenerator:
    """Generate a semantic design spec from a design IR."""

    def generate_from_figma(self, figma_file: Dict[str, Any], file_key: str = "fixture") -> Dict[str, Any]:
        """Generate spec from a raw Figma file JSON (ingest output)."""
        # If it has schema_version and root, it's already an IR
        if "schema_version" in figma_file and "root" in figma_file:
            return self.generate_from_ir(figma_file)

        # Otherwise, run through the normalize pipeline (same as pipeline.py normalize)
        from core.ir_builder import IRBuilder
        from core.figma_types import FigmaFile
        figma = FigmaFile.from_dict(file_key, figma_file)
        builder = IRBuilder(images=figma_file.get("assets") or {})
        ir = builder.build(figma)
        ir_dict = ir.to_dict()
        return self.generate_from_ir(ir_dict)

    def generate_from_ir(self, ir_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Generate spec from an IR dict (as produced by pipeline.py normalize)."""
        root = ir_dict.get("root", {})

        # Extract page-level info
        page_name = root.get("name", "Untitled")

        # The actual screen is usually the first child of the page
        screen = root
        children = root.get("children", [])
        if len(children) == 1 and children[0].get("kind") in ("frame", "page"):
            if children[0].get("kind") == "page":
                page_children = children[0].get("children", [])
                if page_children:
                    screen = page_children[0]
                    page_name = children[0].get("name", page_name)
                else:
                    screen = children[0]
            else:
                screen = children[0]
                page_name = screen.get("name", page_name)

        # Extract sections from screen's direct children
        sections = self._extract_sections(screen)

        # Extract design tokens from the full tree
        tokens = _build_design_tokens(root)

        return {
            "page": {
                "name": page_name,
            },
            "design_tokens": tokens,
            "sections": sections,
        }

    def _extract_sections(self, screen: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract semantic sections from a screen's children."""
        sections = []
        for i, child in enumerate(screen.get("children", [])):
            kind = child.get("kind", "")
            name = child.get("name", f"Section {i + 1}")

            if kind not in ("frame", "group", "section", "component", "instance"):
                # Standalone text or shape at screen level — wrap in a content section
                text = _extract_text_content(child)
                if text:
                    sections.append({
                        "id": _slugify(name),
                        "name": name,
                        "type": SectionType.CONTENT.value,
                        "layout": "stack",
                        "content": [{"type": "paragraph", "text": text}],
                    })
                continue

            section_type = _infer_section_type(name, child.get("children", []))
            layout = _layout_from_node(child)
            gap = _extract_gap(child)
            padding = _extract_padding(child)

            # Extract background color
            style = child.get("style", {})
            fills = style.get("fills", []) if style else []
            background = _extract_fill_color(fills)
            bg_image = _extract_fill_image(fills)

            # Extract content
            content = _extract_content(child)

            # Extract dimensions
            dims = child.get("dimensions", {})
            width = dims.get("width") if dims else None
            height = dims.get("height") if dims else None

            section: Dict[str, Any] = {
                "id": _slugify(name),
                "name": name,
                "type": section_type,
                "layout": layout,
                "content": content,
            }
            if gap is not None:
                section["gap"] = gap
            if padding:
                section["padding"] = padding
            if background:
                section["background"] = background
            if bg_image:
                section["backgroundImage"] = bg_image
            if width:
                section["width"] = width
            if height:
                section["height"] = height

            sections.append(section)

        return sections


def _slugify(name: str) -> str:
    """Convert a node name to a URL-friendly slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "section"
