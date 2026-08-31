#!/usr/bin/env python3
"""
Semantic comparator — compares generated HTML structure against Figma IR.

This module provides a viewport-scoped semantic comparison that checks:
- Color palette (exact hex + close match)
- Text content (word-level fuzzy matching)
- Image presence and unique references
- Gradients, shadows, opacity
- Typography (font sizes, weights, spacing)
- Layout type (flex/grid/absolute)
- Section keywords
- Vertical layout ordering
- Overall content completeness

Target: 95%+ semantic similarity for pixel-perfect output.
"""
import math
import re
from typing import Any, Dict, List, Set, Tuple

# Figma imageRef → content_hash mapping (from assets/manifest.json)
IMAGE_REF_MAP = {
    "e35dee6deae63c1dba994d043f4de7eaf83ad2f4": "d27b0382c0ff2f141951e1c43a7ba35790003350eb7f59343dc2f19ef6a93de2",
    "9c12887c275a251d0966ae77b7e58d007b190a88": "8602468a31138c6deae81d713872a59929ef763ac6059ab8746d3c99d5bf5aab",
    "d13ca5ea79047419a4bc27738c3cc555a99dcd2c": "7c8f4fef3195fdf944f4c6ee2105603f69586cfcf0397a033889d33081d9b912",
    "327a204ed9928b5174b5259d5829eb2610909585": "50510acf8eaf183c4dfc28e293660f35a6ba86fe63e34a493ff86530e94cea59",
    "e003595138e50dbf32c78423a47442b3c281fc96": "58ef3615d722e34b27854ce3ab3b73bc075899d6c71de9457e5d408001edd906",
}

# All viewport-visible section keywords to detect
SECTION_KEYWORDS = [
    "header", "hero", "content", "footer", "nav", "social",
    "slider", "logo", "mountain",
]

# Font weight thresholds
FONT_WEIGHT_THRESHOLDS = [
    (800, "w800"),
    (700, "w700"),
    (600, "w600"),
]

# Feature weights for overall score
FEATURE_WEIGHTS = {
    "colors": 0.12,
    "text": 0.15,
    "elements": 0.06,
    "images": 0.08,
    "gradients": 0.04,
    "typography": 0.07,
    "layout_type": 0.04,
    "background": 0.08,
    "dimensions": 0.04,
    "border_radius": 0.03,
    "shadows": 0.03,
    "opacity": 0.02,
    "text_style": 0.06,
    "sections": 0.04,
    "vertical_layout": 0.03,
    "image_refs": 0.04,
    "completeness": 0.07,
}


class SemanticComparator:
    """Compare HTML structure against Figma IR with viewport-scoped scoring."""
    
    def __init__(self, ir: Dict[str, Any], viewport_height: int = 900):
        """
        Initialize comparator with Figma IR.
        
        Args:
            ir: Figma IR as parsed JSON dict
            viewport_height: Height of the viewport in pixels (default 900)
        """
        self.ir = ir
        self.viewport_height = viewport_height
        self.ir_features = self._extract_ir_features()
    
    def compare(self, html: str) -> Dict[str, Any]:
        """
        Compare HTML against the IR.
        
        Args:
            html: Generated HTML string
            
        Returns:
            Dict with 'overall' (0.0-1.0), 'scores' (per-feature), and
            'guidance' (human-readable fix instructions)
        """
        html_features = self._extract_html_features(html)
        
        scores = {}
        
        scores["colors"] = self._score_colors(
            self.ir_features["color_palette"],
            html_features["color_palette"]
        )
        
        scores["text"] = self._score_text(
            self.ir_features["key_texts"],
            html_features["texts"]
        )
        
        scores["elements"] = self._score_elements(
            self.ir_features["top_level_count"],
            html_features["element_count"]
        )
        
        scores["images"] = self._score_images(
            self.ir_features["unique_image_count"],
            html_features["image_count"]
        )
        
        scores["gradients"] = 1.0 if (
            self.ir_features["has_gradient"] == html_features["has_gradient"]
        ) else 0.0
        
        scores["typography"] = self._score_typography(
            self.ir_features["font_sizes"],
            html_features["font_sizes"]
        )
        
        scores["layout_type"] = self._score_layout_type(
            self.ir_features["layout_types"],
            html_features["layout_types"]
        )
        
        scores["background"] = 1.0 if (
            self.ir_features["bg_color"] == html_features["bg_color"]
        ) else 0.0
        
        scores["dimensions"] = self._score_dimensions(
            self.ir_features["viewport"],
            html_features["viewport"]
        )
        
        scores["border_radius"] = self._score_border_radius(
            self.ir_features["has_border_radius"],
            html_features["has_border_radius"]
        )
        
        scores["shadows"] = self._score_shadows(
            self.ir_features["shadow_count"],
            html_features["has_shadow"]
        )
        
        scores["opacity"] = self._score_opacity(
            self.ir_features["has_opacity"],
            html_features["has_opacity"]
        )
        
        scores["text_style"] = self._score_text_style(
            self.ir_features["text_styles"],
            html_features["text_styles"]
        )
        
        scores["sections"] = self._score_sections(
            self.ir_features["section_keywords"],
            html_features["section_keywords"]
        )
        
        scores["vertical_layout"] = self._score_vertical_layout(
            self.ir_features["vertical_positions"],
            html_features["vertical_positions"]
        )
        
        scores["image_refs"] = self._score_image_refs(
            self.ir_features["image_refs"],
            html_features["image_refs"]
        )
        
        scores["completeness"] = self._score_completeness(
            self.ir_features,
            html_features
        )
        
        overall = sum(scores[k] * FEATURE_WEIGHTS[k] for k in FEATURE_WEIGHTS)
        
        guidance = self._generate_guidance(scores, self.ir_features, html_features)
        
        return {
            "overall": overall,
            "scores": scores,
            "guidance": guidance,
        }
    
    def _extract_ir_features(self) -> Dict[str, Any]:
        """Extract features from Figma IR, scoped to viewport."""
        color_palette: Set[str] = set()
        key_texts: List[str] = []
        top_level_count = 0
        unique_image_count = 0
        has_gradient = False
        font_sizes: Set[float] = set()
        layout_types: Set[str] = set()
        bg_color = None
        has_border_radius = False
        shadow_count = 0
        has_opacity = False
        text_styles: Set[str] = set()
        section_keywords: Set[str] = set()
        vertical_positions: List[float] = []
        image_refs: Set[str] = set()
        
        pages = self.ir.get("pages", [])
        if not pages or not pages[0].get("children"):
            return self._empty_features()
        
        frame = pages[0]["children"][0]
        frame_bbox = frame.get("raw", {}).get("absoluteBoundingBox", {})
        frame_y = frame_bbox.get("y", 0)
        frame_bottom = frame_y + self.viewport_height
        
        seen_text: Set[str] = set()
        seen_images: Set[str] = set()
        
        def walk(node: Dict[str, Any], depth: int = 0) -> None:
            nonlocal top_level_count, unique_image_count, has_gradient, bg_color
            nonlocal has_border_radius, shadow_count, has_opacity
            
            raw = node.get("raw", {})
            name = node.get("name", "")
            bbox = raw.get("absoluteBoundingBox", {})
            y = bbox.get("y", 0)
            w = bbox.get("width", 0)
            h = bbox.get("height", 0)
            
            in_viewport = (y < frame_bottom) and (y + h > frame_y)
            is_significant = w > 100 and h > 100
            
            # Colors
            fills = raw.get("fills", [])
            for fill in fills:
                fill_type = fill.get("type", "")
                if fill_type == "SOLID" and fill.get("color"):
                    c = fill["color"]
                    r = int(c.get("r", 0) * 255)
                    g = int(c.get("g", 0) * 255)
                    b = int(c.get("b", 0) * 255)
                    alpha = c.get("a", 1.0)
                    hex_c = f"#{r:02x}{g:02x}{b:02x}"
                    if in_viewport:
                        color_palette.add(hex_c)
                        if depth <= 1 and bg_color is None and alpha > 0.5:
                            bg_color = hex_c
                elif fill_type in ("GRADIENT_LINEAR", "GRADIENT_RADIAL"):
                    has_gradient = True
                    for stop in fill.get("gradientStops", []):
                        sc = stop.get("color", {})
                        sr = int(sc.get("r", 0) * 255)
                        sg = int(sc.get("g", 0) * 255)
                        sb = int(sc.get("b", 0) * 255)
                        stop_hex = f"#{sr:02x}{sg:02x}{sb:02x}"
                        if in_viewport:
                            color_palette.add(stop_hex)
                elif fill_type == "IMAGE" or fill.get("imageRef"):
                    img_ref = fill.get("imageRef", "")
                    if (img_ref and in_viewport and is_significant
                            and img_ref not in seen_images):
                        seen_images.add(img_ref)
                        unique_image_count += 1
                        image_refs.add(img_ref)
            
            # Border radius
            if raw.get("cornerRadius", 0) > 0:
                has_border_radius = True
            if raw.get("rectangleCornerRadii"):
                has_border_radius = True
            
            # Effects (shadows)
            effects = raw.get("effects", [])
            for effect in effects:
                if effect.get("type") in ("DROP_SHADOW", "INNER_SHADOW"):
                    if effect.get("visible", True) and in_viewport:
                        shadow_count += 1
            
            # Opacity
            opacity = raw.get("opacity", 1.0)
            if opacity < 1.0 and in_viewport:
                has_opacity = True
            
            # Text
            chars = raw.get("characters") or node.get("text", {}).get("characters")
            if chars and len(chars.strip()) > 0 and in_viewport:
                text_clean = chars.strip()
                if text_clean not in seen_text:
                    seen_text.add(text_clean)
                    key_texts.append(text_clean)
                    style = raw.get("style", {})
                    if style.get("fontSize"):
                        font_sizes.add(style["fontSize"])
                    if style.get("fontWeight"):
                        fw = int(style["fontWeight"])
                        text_styles.add(f"w{fw}")
                    if style.get("letterSpacing", 0) > 0:
                        text_styles.add("ls")
                    if style.get("textCase") == "UPPER":
                        text_styles.add("upper")
            
            # Layout
            layout_mode = raw.get("layoutMode", "")
            if layout_mode in ("HORIZONTAL", "VERTICAL"):
                layout_types.add("flex")
            
            # Section keywords — only from viewport-visible elements
            if in_viewport:
                name_lower = name.lower()
                for kw in SECTION_KEYWORDS:
                    if kw in name_lower:
                        section_keywords.add(kw)
            
            # Top-level elements
            if depth == 1 and bbox:
                top_level_count += 1
                vertical_positions.append(bbox.get("y", 0))
            
            for child in node.get("children", []):
                walk(child, depth + 1)
        
        # Only process the first frame (main design), not showcase/OG/favicon frames
        if pages and pages[0].get("children"):
            walk(pages[0]["children"][0])
        
        viewport = (int(frame_bbox.get("width", 1920)), self.viewport_height)
        
        return {
            "color_palette": color_palette,
            "key_texts": key_texts,
            "top_level_count": top_level_count,
            "unique_image_count": unique_image_count,
            "has_gradient": has_gradient,
            "font_sizes": font_sizes,
            "layout_types": layout_types,
            "bg_color": bg_color,
            "has_border_radius": has_border_radius,
            "shadow_count": shadow_count,
            "has_opacity": has_opacity,
            "text_styles": text_styles,
            "section_keywords": section_keywords,
            "vertical_positions": sorted(vertical_positions),
            "image_refs": image_refs,
            "viewport": viewport,
        }
    
    def _empty_features(self) -> Dict[str, Any]:
        """Return empty feature dict."""
        return {
            "color_palette": set(),
            "key_texts": [],
            "top_level_count": 0,
            "unique_image_count": 0,
            "has_gradient": False,
            "font_sizes": set(),
            "layout_types": set(),
            "bg_color": None,
            "has_border_radius": False,
            "shadow_count": 0,
            "has_opacity": False,
            "text_styles": set(),
            "section_keywords": set(),
            "vertical_positions": [],
            "image_refs": set(),
            "viewport": (1920, self.viewport_height),
        }
    
    def _extract_html_features(self, html: str) -> Dict[str, Any]:
        """Extract features from HTML."""
        color_palette: Set[str] = set()
        texts: List[str] = []
        element_count = 0
        image_count = 0
        has_gradient = False
        font_sizes: Set[float] = set()
        layout_types: Set[str] = set()
        bg_color = None
        has_border_radius = False
        has_shadow = False
        has_opacity = False
        text_styles: Set[str] = set()
        section_keywords: Set[str] = set()
        vertical_positions: List[float] = []
        image_refs: List[str] = []
        viewport = (1920, self.viewport_height)
        
        # Colors — hex
        for match in re.finditer(r'#([0-9a-fA-F]{6})', html):
            color_palette.add(f"#{match.group(1).lower()}")
        
        # Colors — rgba
        for match in re.finditer(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', html):
            r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
            color_palette.add(f"#{r:02x}{g:02x}{b:02x}")
        
        # Background color
        bg_match = re.search(r'body\s*\{[^}]*background:\s*(#[0-9a-fA-F]{6})', html)
        if bg_match:
            bg_color = bg_match.group(1).lower()
        
        # Text content
        for match in re.finditer(r'>([^<]{1,})<', html):
            text = match.group(1).strip()
            if (text and len(text) > 0
                    and not text.startswith('{')
                    and not text.startswith('/*')
                    and not text.startswith('}')
                    and not text.startswith('*/')
                    and not re.match(r'^[\s\n\r]+$', text)
                    and not text.startswith('var ')
                    and not text.startswith('//')):
                texts.append(text)
        
        # Elements
        element_count = len(re.findall(
            r'<(?:div|img|header|nav|main|section|footer|p|h[1-6]|span|a|button|ul|li|article|aside)\b',
            html
        ))
        
        # Images
        image_count = len(re.findall(r'<img\b', html))
        bg_images = len(re.findall(r'background-image:\s*url', html))
        image_count = max(image_count, bg_images)
        
        # Gradients
        has_gradient = bool(re.search(r'linear-gradient|radial-gradient', html))
        
        # Font sizes
        for match in re.finditer(r'font-size:\s*(\d+\.?\d*)px', html):
            font_sizes.add(float(match.group(1)))
        
        # Layout types
        if 'display: flex' in html or 'display:flex' in html:
            layout_types.add("flex")
        if 'display: grid' in html or 'display:grid' in html:
            layout_types.add("grid")
        if 'position: absolute' in html or 'position:absolute' in html:
            layout_types.add("absolute")
        
        # Border radius
        has_border_radius = bool(re.search(r'border-radius:\s*\d+', html))
        
        # Shadows
        has_shadow = (bool(re.search(r'box-shadow:', html))
                      or bool(re.search(r'text-shadow:', html)))
        
        # Opacity
        has_opacity = bool(re.search(r'opacity:\s*0\.\d', html))
        
        # Text styles
        fw_matches = re.findall(r'font-weight:\s*(\d+)', html)
        for fw in fw_matches:
            fw_int = int(fw)
            for threshold, style_name in FONT_WEIGHT_THRESHOLDS:
                if fw_int >= threshold:
                    text_styles.add(style_name)
        if re.search(r'letter-spacing:', html):
            text_styles.add("ls")
        if re.search(r'text-transform:\s*uppercase', html):
            text_styles.add("upper")
        
        # Section keywords
        html_lower = html.lower()
        for kw in SECTION_KEYWORDS:
            if kw in html_lower:
                section_keywords.add(kw)
        
        # Vertical positions
        for match in re.finditer(r'top:\s*(-?\d+\.?\d*)px', html):
            vertical_positions.append(float(match.group(1)))
        
        # Image refs
        for match in re.finditer(r'src="([^"]+)"', html):
            src = match.group(1)
            is_asset = bool(re.search(r'[0-9a-f]{40,}', src))
            is_image = any(ext in src.lower()
                           for ext in ['.jpg', '.jpeg', '.png', '.svg', '.webp'])
            is_figmaforge = 'assets/' in src
            if is_asset or is_image or is_figmaforge:
                image_refs.append(src)
        
        # Viewport
        w_match = re.search(r'width:\s*(\d+)px', html)
        h_match = re.search(r'height:\s*(\d+)px', html)
        if w_match and h_match:
            viewport = (int(w_match.group(1)), int(h_match.group(1)))
        
        return {
            "color_palette": color_palette,
            "texts": texts,
            "element_count": element_count,
            "image_count": image_count,
            "has_gradient": has_gradient,
            "font_sizes": font_sizes,
            "layout_types": layout_types,
            "bg_color": bg_color,
            "has_border_radius": has_border_radius,
            "has_shadow": has_shadow,
            "has_opacity": has_opacity,
            "text_styles": text_styles,
            "section_keywords": section_keywords,
            "vertical_positions": sorted(vertical_positions),
            "image_refs": image_refs,
            "viewport": viewport,
        }
    
    def _score_colors(self, ir_colors: Set[str], html_colors: Set[str]) -> float:
        if not ir_colors:
            return 1.0
        exact = sum(1 for c in ir_colors if c in html_colors)
        approx = 0
        for ic in ir_colors:
            if ic in html_colors:
                continue
            ir_rgb = self._hex_to_rgb(ic)
            best_dist = float('inf')
            for hc in html_colors:
                html_rgb = self._hex_to_rgb(hc)
                dist = math.sqrt(sum((a - b) ** 2
                                     for a, b in zip(ir_rgb, html_rgb)))
                best_dist = min(best_dist, dist)
            if best_dist < 30:
                approx += 1
        return min((exact + approx * 0.5) / len(ir_colors), 1.0)
    
    @staticmethod
    def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    
    def _score_text(self, ir_texts: List[str], html_texts: List[str]) -> float:
        if not ir_texts:
            return 1.0
        html_combined = " ".join(html_texts).lower()
        html_words = set(html_combined.split())
        matched = 0.0
        for t in ir_texts:
            t_lower = t.lower().strip()
            if not t_lower:
                continue
            if t_lower in html_combined:
                matched += 1.0
                continue
            ir_words = t_lower.split()
            if len(ir_words) >= 2:
                words_found = sum(1 for w in ir_words if w in html_words)
                ratio = words_found / len(ir_words)
                if ratio >= 0.8:
                    matched += ratio
                elif ratio >= 0.5:
                    matched += ratio * 0.7
                else:
                    matched += ratio * 0.3
            else:
                for hw in html_words:
                    if t_lower in hw or hw in t_lower:
                        matched += 0.5
                        break
        return min(matched / len(ir_texts), 1.0)
    
    def _score_elements(self, ir_count: int, html_count: int) -> float:
        if ir_count == 0:
            return 1.0
        ratio = min(html_count, ir_count * 3) / ir_count
        return min(ratio, 1.0)
    
    def _score_images(self, ir_count: int, html_count: int) -> float:
        if ir_count == 0 and html_count == 0:
            return 1.0
        if ir_count == 0:
            return 0.8
        if html_count == 0:
            return 0.0
        return min(html_count / ir_count, 1.0)
    
    def _score_image_refs(self, ir_refs: Set[str],
                          html_refs: List[str]) -> float:
        if not ir_refs:
            return 1.0
        if not html_refs:
            return 0.0
        ir_content_hashes = set()
        for ref in ir_refs:
            if ref in IMAGE_REF_MAP:
                ir_content_hashes.add(IMAGE_REF_MAP[ref])
            else:
                ir_content_hashes.add(ref)
        html_ref_str = " ".join(html_refs).lower()
        matched = 0
        for ch in ir_content_hashes:
            ch_lower = ch.lower()
            if ch_lower in html_ref_str:
                matched += 1
            elif ch_lower[:16] in html_ref_str:
                matched += 0.5
        return min(matched / len(ir_content_hashes), 1.0) if ir_content_hashes else 0.5
    
    def _score_typography(self, ir_sizes: Set[float],
                          html_sizes: Set[float]) -> float:
        if not ir_sizes:
            return 1.0
        matched = sum(1 for s in ir_sizes if s in html_sizes)
        partial = 0
        for s in ir_sizes:
            if s not in html_sizes:
                for hs in html_sizes:
                    if abs(s - hs) < 2:
                        partial += 0.5
                        break
        return min((matched + partial) / len(ir_sizes), 1.0)
    
    def _score_layout_type(self, ir_types: Set[str],
                           html_types: Set[str]) -> float:
        if not ir_types:
            return 1.0
        matched = sum(1 for t in ir_types if t in html_types)
        return matched / len(ir_types) if ir_types else 0
    
    def _score_dimensions(self, ir_vp: Tuple[int, int],
                          html_vp: Tuple[int, int]) -> float:
        if ir_vp == html_vp:
            return 1.0
        w_match = (1.0 if ir_vp[0] == html_vp[0]
                   else max(0, 1 - abs(ir_vp[0] - html_vp[0]) / ir_vp[0]))
        h_match = (1.0 if ir_vp[1] == html_vp[1]
                   else max(0, 1 - abs(ir_vp[1] - html_vp[1]) / ir_vp[1]))
        return (w_match + h_match) / 2
    
    @staticmethod
    def _score_border_radius(ir_has: bool, html_has: bool) -> float:
        return 1.0 if ir_has == html_has else (0.5 if not ir_has else 0.0)
    
    @staticmethod
    def _score_shadows(ir_shadow_count: int, html_has_shadow: bool) -> float:
        if ir_shadow_count == 0:
            return 1.0
        return 1.0 if html_has_shadow else 0.0
    
    @staticmethod
    def _score_opacity(ir_has: bool, html_has: bool) -> float:
        if not ir_has:
            return 1.0
        return 1.0 if html_has else 0.7
    
    def _score_text_style(self, ir_styles: Set[str],
                          html_styles: Set[str]) -> float:
        if not ir_styles:
            return 1.0
        matched = sum(1 for s in ir_styles if s in html_styles)
        return matched / len(ir_styles) if ir_styles else 0
    
    def _score_sections(self, ir_keywords: Set[str],
                        html_keywords: Set[str]) -> float:
        if not ir_keywords:
            return 1.0
        matched = sum(1 for k in ir_keywords if k in html_keywords)
        return matched / len(ir_keywords) if ir_keywords else 0
    
    def _score_vertical_layout(self, ir_positions: List[float],
                               html_positions: List[float]) -> float:
        if not ir_positions or not html_positions:
            return 0.5
        ir_increasing = sum(
            1 for i in range(1, len(ir_positions))
            if ir_positions[i] > ir_positions[i - 1]
        )
        html_increasing = sum(
            1 for i in range(1, len(html_positions))
            if html_positions[i] > html_positions[i - 1]
        )
        ir_ratio = ir_increasing / max(len(ir_positions) - 1, 1)
        html_ratio = html_increasing / max(len(html_positions) - 1, 1)
        order_score = 1.0 - abs(ir_ratio - html_ratio)
        # If ordering matches perfectly, give full credit regardless of count
        # (HTML may not include elements beyond the viewport)
        if order_score >= 0.99:
            return 1.0
        # Otherwise use soft count normalization
        count_ratio = min(len(html_positions), len(ir_positions)) / max(
            len(ir_positions), 1
        )
        soft_count = math.sqrt(count_ratio)
        return order_score * 0.5 + soft_count * 0.5
    
    def _score_completeness(self, ir_features: Dict[str, Any],
                            html_features: Dict[str, Any]) -> float:
        checks = []
        if ir_features["bg_color"]:
            checks.append(
                1.0 if html_features["bg_color"] == ir_features["bg_color"]
                else 0.0
            )
        checks.append(
            1.0 if ir_features["has_gradient"] == html_features["has_gradient"]
            else 0.0
        )
        if ir_features["unique_image_count"] > 0:
            checks.append(
                1.0 if html_features["image_count"] > 0 else 0.0
            )
        if ir_features["key_texts"]:
            text_overlap = sum(
                1 for t in ir_features["key_texts"]
                if any(t.lower() in ht.lower()
                       for ht in html_features["texts"])
            )
            checks.append(
                min(text_overlap / len(ir_features["key_texts"]), 1.0)
            )
        if ir_features["shadow_count"] > 0:
            checks.append(
                1.0 if html_features["has_shadow"] else 0.0
            )
        return sum(checks) / len(checks) if checks else 0.5
    
    def _generate_guidance(self, scores: Dict[str, float],
                           ir_features: Dict[str, Any],
                           html_features: Dict[str, Any]) -> str:
        """Generate human-readable guidance for fixing mismatches."""
        issues = []
        
        if scores["colors"] < 1.0:
            missing = ir_features["color_palette"] - html_features["color_palette"]
            if missing:
                issues.append(f"Missing colors: {', '.join(sorted(missing)[:3])}")
        
        if scores["text"] < 0.9:
            issues.append("Text content doesn't match — check headings and body text")
        
        if scores["images"] < 1.0:
            issues.append(
                f"Image count mismatch: IR has {ir_features['unique_image_count']}, "
                f"HTML has {html_features['image_count']}"
            )
        
        if scores["shadows"] < 1.0 and ir_features["shadow_count"] > 0:
            issues.append("Missing box-shadow or text-shadow effects")
        
        if scores["typography"] < 1.0:
            missing_sizes = ir_features["font_sizes"] - html_features["font_sizes"]
            if missing_sizes:
                issues.append(f"Missing font sizes: {sorted(missing_sizes)[:3]}")
        
        if scores["background"] < 1.0:
            issues.append(
                f"Background color mismatch: expected {ir_features['bg_color']}, "
                f"got {html_features['bg_color']}"
            )
        
        if scores["sections"] < 1.0:
            missing = ir_features["section_keywords"] - html_features["section_keywords"]
            if missing:
                issues.append(f"Missing section keywords: {', '.join(sorted(missing))}")
        
        if not issues:
            return "All features match the Figma design"
        
        return "Issues found:\n" + "\n".join(f"  - {i}" for i in issues)


def compare_html_against_ir(ir: Dict[str, Any], html: str,
                            viewport_height: int = 900) -> Dict[str, Any]:
    """
    Convenience function to compare HTML against Figma IR.
    
    Args:
        ir: Figma IR as parsed JSON dict
        html: Generated HTML string
        viewport_height: Height of the viewport (default 900)
        
    Returns:
        Dict with 'overall', 'scores', 'guidance'
    """
    comparator = SemanticComparator(ir, viewport_height)
    return comparator.compare(html)
