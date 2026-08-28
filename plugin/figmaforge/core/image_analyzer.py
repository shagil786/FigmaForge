"""
Image-to-IR analyzer: extract structured layout from any screenshot.

Converts an arbitrary image (Figma export, web page screenshot, mobile app
screenshot, wireframe, mockup) into a Design IR that feeds the same
layout → code pipeline as Figma JSON input.

Two ingestion paths converge on the same IRDocument:

  Figma JSON → ir_builder.py → IRDocument
  Any image  → image_analyzer.py → IRDocument

The analyzer uses a configurable vision model backend to analyze the image
and extract: layout structure, colors, typography, spacing, and component
relationships.

Design goals:
- Standard library only (plus one user-approved dependency: the vision API client).
- Deterministic output for the same input image + model.
- Preserves extracted information in the IR's typed vocabulary.
- Reports confidence levels for each extracted property.
- Falls back gracefully when the model cannot extract certain properties.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .ir_types import (
    IRAnnotations,
    IRBlur,
    IRBorder,
    IRColor,
    IRComponent,
    IRDimensions,
    IRDocument,
    IRFill,
    IRGradientStop,
    IRInstance,
    IRLayout,
    IRLink,
    IRNode,
    IRPosition,
    IRPrototype,
    IResponsive,
    IRShadow,
    IRSource,
    IRSpacing,
    IRStyle,
    IRTextContent,
    IRToken,
    IRTokenRef,
    IRTokens,
    IRTypography,
    IR_VERSION,
    KIND_FRAME,
    KIND_NODE,
    KIND_PAGE,
    KIND_TEXT,
    kind_for,
)

logger = logging.getLogger("figmaforge.image_analyzer")

from .image_analyzer_nlp_parser import parse_natural_language


# ---------------------------------------------------------------------------
# Vision model protocol
# ---------------------------------------------------------------------------

class VisionModel(Protocol):
    """Protocol for vision model backends."""

    def analyze_image(
        self,
        image_path: str,
        prompt: str,
        *,
        max_tokens: int = 4096,
    ) -> str:
        """Analyze an image and return the model's text response.

        Args:
            image_path: Path to the image file.
            prompt: The analysis prompt.
            max_tokens: Maximum tokens in the response.

        Returns:
            The model's text response.
        """
        ...


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ImageAnalyzerConfig:
    """Configuration for the image analyzer."""

    # Vision model to use
    vision_model: Optional[VisionModel] = None

    # Image preprocessing
    max_image_width: int = 1920  # Resize large images to this width
    max_image_height: int = 1080

    # Extraction settings
    extract_colors: bool = True
    extract_typography: bool = True
    extract_spacing: bool = True
    extract_components: bool = True
    extract_responsive: bool = True

    # Confidence threshold (0-1) for including extracted properties
    min_confidence: float = 0.5

    # Source identifier for the IR
    source_file_key: str = "image"

    def __post_init__(self):
        if self.vision_model is None:
            self.vision_model = _create_default_vision_model()


def _create_default_vision_model() -> Optional[VisionModel]:
    """Create a default vision model from environment variables.

    Supports:
    - NVIDIA_API_KEY → Kimi K3 or Llama Vision (via NVIDIA API catalog)
    - ANTHROPIC_API_KEY → Claude Vision
    - OPENAI_API_KEY → GPT-4V
    """
    if os.environ.get("NVIDIA_API_KEY"):
        try:
            return _NvidiaVisionModel()
        except ValueError:
            pass
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _AnthropicVisionModel()
    if os.environ.get("OPENAI_API_KEY"):
        return _OpenAIVisionModel()
    logger.warning(
        "No vision model configured. Set NVIDIA_API_KEY, ANTHROPIC_API_KEY, "
        "or OPENAI_API_KEY to enable image analysis."
    )
    return None


# ---------------------------------------------------------------------------
# Vision model implementations
# ---------------------------------------------------------------------------

class _AnthropicVisionModel:
    """Claude Vision backend via Anthropic API."""

    def analyze_image(
        self,
        image_path: str,
        prompt: str,
        *,
        max_tokens: int = 4096,
    ) -> str:
        import urllib.request

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        # Read and encode the image
        with open(image_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        # Determine media type
        media_type = _media_type_for(image_path)

        payload = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": max_tokens,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }],
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        # Extract text from response
        for block in result.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "")
        return ""


class _OpenAIVisionModel:
    """GPT-4V backend via OpenAI API."""

    def analyze_image(
        self,
        image_path: str,
        prompt: str,
        *,
        max_tokens: int = 4096,
    ) -> str:
        import urllib.request

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")

        # Read and encode the image
        with open(image_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        media_type = _media_type_for(image_path)

        payload = json.dumps({
            "model": "gpt-4o",
            "max_tokens": max_tokens,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{image_data}",
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }],
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        return result.get("choices", [{}])[0].get("message", {}).get("content", "")


class _NvidiaVisionModel:
    """Vision model backend via NVIDIA API catalog.

    Tries Kimi K3 first (best reasoning), falls back to Llama 3.2 Vision.
    """

    MODELS = [
        "moonshotai/kimi-k3",
        "minimaxai/minimax-m3",
        "meta/llama-3.2-11b-vision-instruct",
        "google/gemma-3-27b-it",
    ]

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("NVIDIA_API_KEY", "")
        if not self._api_key:
            raise ValueError("NVIDIA_API_KEY not set")

    def analyze_image(
        self,
        image_path: str,
        prompt: str,
        *,
        max_tokens: int = 4096,
    ) -> str:
        import urllib.request

        # Read and encode the image
        with open(image_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        media_type = _media_type_for(image_path)

        # Try models in order, fall back on timeout/error
        last_error = None
        for model in self.MODELS:
            try:
                payload = json.dumps({
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{image_data}",
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }],
                }).encode("utf-8")

                req = urllib.request.Request(
                    "https://integrate.api.nvidia.com/v1/chat/completions",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self._api_key}",
                    },
                    method="POST",
                )

                with urllib.request.urlopen(req, timeout=90) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    logger.info("Vision model %s responded successfully", model)
                    return content
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_error = e
                logger.warning("Vision model %s failed: %s — trying next", model, e)
                continue

        raise ValueError(
            f"All NVIDIA vision models failed. Last error: {last_error}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _media_type_for(image_path: str) -> str:
    """Determine the media type from file extension."""
    ext = Path(image_path).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "image/png")


def _hex_to_ir_color(hex_str: str) -> Optional[IRColor]:
    """Convert a hex color string (#rrggbb or #rrggbbaa) to IRColor."""
    hex_str = hex_str.strip().lstrip("#")
    if len(hex_str) == 6:
        r = int(hex_str[0:2], 16) / 255.0
        g = int(hex_str[2:4], 16) / 255.0
        b = int(hex_str[4:6], 16) / 255.0
        return IRColor(r=r, g=g, b=b, a=1.0)
    elif len(hex_str) == 8:
        r = int(hex_str[0:2], 16) / 255.0
        g = int(hex_str[2:4], 16) / 255.0
        b = int(hex_str[4:6], 16) / 255.0
        a = int(hex_str[6:8], 16) / 255.0
        return IRColor(r=r, g=g, b=b, a=a)
    return None


def _parse_spacing(value: Any) -> Optional[IRSpacing]:
    """Parse a spacing value (number or object) into IRSpacing."""
    if isinstance(value, (int, float)):
        return IRSpacing(top=value, right=value, bottom=value, left=value)
    if isinstance(value, dict):
        return IRSpacing(
            top=value.get("top"),
            right=value.get("right"),
            bottom=value.get("bottom"),
            left=value.get("left"),
        )
    return None


def _parse_color(value: Any) -> Optional[IRColor]:
    """Parse a color value (hex string or object) into IRColor."""
    if isinstance(value, str):
        return _hex_to_ir_color(value)
    if isinstance(value, dict):
        return IRColor(
            r=float(value.get("r", 0)),
            g=float(value.get("g", 0)),
            b=float(value.get("b", 0)),
            a=float(value.get("a", 1.0)),
        )
    return None


# ---------------------------------------------------------------------------
# Analysis prompt
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = """You are a UI extraction expert. Analyze this screenshot and extract its layout structure as a JSON object.

IMPORTANT: Return ONLY valid JSON, no markdown, no explanation, no code fences.

The JSON must have this exact structure:
{"viewport":{"width":<num>,"height":<num>},"elements":[{"id":"<unique-id>","name":"<descriptive name>","type":"frame|text|shape|image","bounds":{"x":<num>,"y":<num>,"width":<num>,"height":<num>},"style":{"background":"<hex>","border_radius":<num|null>,"border":null,"shadow":null,"opacity":1},"layout":{"display":"flex|grid|block","direction":"row|null","justify":"flex-start|center|space-between|null","align":"flex-start|center|null","padding":<num>,"gap":<num|null>},"typography":{"text":"<if text>","font_family":"<name>","font_size":<num>,"font_weight":<num>,"color":"<hex>","align":"left|center|null","line_height":<num>},"children":[],"confidence":0.9}],"colors":["<hex>"],"fonts":["<name>"]}

Rules:
- Extract ALL visible elements (text, frames, buttons, cards, images)
- Measure bounds in pixels relative to the viewport
- Use descriptive names (header, hero-title, nav-link, card, button, footer)
- Include actual text content in typography.text
- Extract real hex colors from the design
- Preserve the visual hierarchy
- For auto-layout containers, set display=flex and appropriate direction
- Return ONLY the JSON object, nothing else
"""


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------

class ImageAnalyzer:
    """Extract structured IR from any screenshot using a vision model.

    Usage::

        analyzer = ImageAnalyzer(config)
        ir_document = analyzer.analyze("screenshot.png")
        # ir_document is an IRDocument that feeds the same pipeline as Figma JSON
    """

    def __init__(self, config: Optional[ImageAnalyzerConfig] = None):
        self.config = config or ImageAnalyzerConfig()
        if self.config.vision_model is None:
            raise ValueError(
                "No vision model available. Set ANTHROPIC_API_KEY or OPENAI_API_KEY."
            )

    def analyze(self, image_path: str) -> IRDocument:
        """Analyze an image and return a Design IR.

        Args:
            image_path: Path to the image file (PNG, JPG, etc.)

        Returns:
            An IRDocument ready for the layout → code pipeline.
        """
        if not Path(image_path).exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        logger.info("Analyzing image: %s", image_path)

        # 1. Call the vision model
        response = self.config.vision_model.analyze_image(
            image_path,
            ANALYSIS_PROMPT,
            max_tokens=4096,
        )

        # 2. Parse the structured response
        analysis = self._parse_response(response)

        # 3. Build the IR from the extracted structure
        ir_document = self._build_ir(analysis, image_path)

        logger.info(
            "Extracted %d elements, %d colors, %d fonts",
            len(analysis.get("elements", [])),
            len(analysis.get("colors", [])),
            len(analysis.get("fonts", [])),
        )

        return ir_document

    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse the vision model response — try JSON first, then NLP fallback."""
        # Try to extract JSON from the response (may be wrapped in markdown)
        json_match = re.search(r"```json\s*(.*?)\s*```", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find raw JSON
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            json_str = json_match.group(0) if json_match else None

        if json_str:
            try:
                parsed = json.loads(json_str)
                # Validate it has the expected structure
                if "elements" in parsed and parsed["elements"]:
                    return parsed
                logger.warning("JSON parsed but has no elements, trying NLP fallback")
            except json.JSONDecodeError:
                pass

        # Fallback: parse natural language description from the vision model
        logger.info("Using NLP fallback parser for vision model response")
        return parse_natural_language(response)

    def _build_ir(
        self,
        analysis: Dict[str, Any],
        image_path: str,
    ) -> IRDocument:
        """Build an IRDocument from the extracted analysis."""
        viewport = analysis.get("viewport", {"width": 1440, "height": 900})
        elements = analysis.get("elements", [])
        colors = analysis.get("colors", [])
        fonts = analysis.get("fonts", [])

        # Build element lookup
        element_map: Dict[str, Dict[str, Any]] = {}
        for elem in elements:
            eid = elem.get("id", f"elem_{len(element_map)}")
            element_map[eid] = elem

        # Build root frame
        root_node = self._build_root_frame(viewport, elements, element_map)

        # Build IR nodes for each element
        ir_nodes = self._build_ir_nodes(elements, element_map)

        # Attach children to root
        root_node.children = ir_nodes

        # Build document
        doc = IRDocument(
            schema_version=IR_VERSION,
            file_key=self.config.source_file_key,
            name=Path(image_path).stem,
            source=IRSource(
                file_key=self.config.source_file_key,
                node_id="0:0",
                node_type="DOCUMENT",
                path=[],
            ),
            root=root_node,
            pages=[root_node],
            styles=self._build_color_tokens(colors),
            variables=self._build_font_tokens(fonts),
        )

        # Add 'type' field to all nodes for compatibility with source_audit
        # (audit checks 'type' but image analyzer uses 'node_type')
        for node in doc.all_nodes():
            if hasattr(node, 'raw') and isinstance(node.raw, dict):
                node.raw.setdefault('type', node.node_type)
            # Also add fills/styles to raw for audit compatibility
            if node.style and node.style.fills:
                node.raw.setdefault('fills', [{'type': 'SOLID'}])
            if node.text and node.text.characters:
                node.raw.setdefault('characters', node.text.characters)

        return doc

    def _build_root_frame(
        self,
        viewport: Dict[str, Any],
        elements: List[Dict[str, Any]],
        element_map: Dict[str, Dict[str, Any]],
    ) -> IRNode:
        """Build the root frame node."""
        width = float(viewport.get("width", 1440))
        height = float(viewport.get("height", 900))

        return IRNode(
            id="0:1",
            name="Root",
            kind=KIND_FRAME,
            node_type="FRAME",
            source=IRSource(
                file_key=self.config.source_file_key,
                node_id="0:1",
                node_type="FRAME",
                path=[],
            ),
            visible=True,
            opacity=1.0,
            dimensions=IRDimensions(width=width, height=height),
            position=IRPosition(mode="absolute", x=0, y=0, left=0, top=0),
            layout=IRLayout(mode="auto", direction="column"),
            responsive=IResponsive(
                constraints_horizontal="MIN",
                constraints_vertical="MIN",
            ),
        )

    def _build_ir_nodes(
        self,
        elements: List[Dict[str, Any]],
        element_map: Dict[str, Dict[str, Any]],
    ) -> List[IRNode]:
        """Build IR nodes from extracted elements."""
        nodes: List[IRNode] = []
        processed: set = set()

        for elem in elements:
            eid = elem.get("id", f"elem_{len(nodes)}")
            if eid in processed:
                continue
            node = self._build_ir_node(elem, element_map, processed)
            if node is not None:
                nodes.append(node)

        return nodes

    def _build_ir_node(
        self,
        elem: Dict[str, Any],
        element_map: Dict[str, Dict[str, Any]],
        processed: set,
        depth: int = 0,
    ) -> Optional[IRNode]:
        """Build a single IR node from an extracted element."""
        if depth > 20:  # Prevent infinite recursion
            return None

        eid = elem.get("id", f"unknown_{len(processed)}")
        if eid in processed:
            return None
        processed.add(eid)

        # Determine node type
        elem_type = elem.get("type", "frame")
        kind = {
            "frame": KIND_FRAME,
            "text": KIND_TEXT,
            "shape": KIND_FRAME,
            "image": KIND_FRAME,
        }.get(elem_type, KIND_FRAME)

        # Extract bounds
        bounds = elem.get("bounds", {})
        x = float(bounds.get("x", 0))
        y = float(bounds.get("y", 0))
        width = float(bounds.get("width", 100))
        height = float(bounds.get("height", 100))

        # Extract style
        style = self._extract_style(elem)

        # Extract typography
        typography = None
        text_content = None
        if elem_type == "text":
            typo_data = elem.get("typography", {})
            if typo_data:
                typography = IRTypography(
                    font_family=typo_data.get("font_family"),
                    font_size=_as_float(typo_data.get("font_size")),
                    font_weight=_as_float(typo_data.get("font_weight")),
                    line_height=_as_float(typo_data.get("line_height")),
                    text_align=typo_data.get("align"),
                )
                text_content = IRTextContent(
                    characters=typo_data.get("text", "")
                )

        # Extract layout
        layout = self._extract_layout(elem)

        # Build children
        children_ids = elem.get("children", [])
        children = []
        for child_id in children_ids:
            child_elem = element_map.get(child_id)
            if child_elem:
                child_node = self._build_ir_node(
                    child_elem, element_map, processed, depth + 1
                )
                if child_node:
                    children.append(child_node)

        # Build the node with Figma-compatible constraints
        # MIN constraints mean the element is anchored to top-left (default for absolute positioning)
        node = IRNode(
            id=eid,
            name=elem.get("name", eid),
            kind=kind,
            node_type="TEXT" if elem_type == "text" else "FRAME",
            source=IRSource(
                file_key=self.config.source_file_key,
                node_id=eid,
                node_type="TEXT" if elem_type == "text" else "FRAME",
                path=[],
            ),
            visible=True,
            opacity=float(elem.get("style", {}).get("opacity", 1.0)),
            dimensions=IRDimensions(width=width, height=height),
            position=IRPosition(mode="absolute", x=x, y=y, left=x, top=y),
            layout=layout,
            style=style,
            typography=typography,
            text=text_content,
            children=children,
            responsive=IResponsive(
                constraints_horizontal="MIN",
                constraints_vertical="MIN",
            ),
        )

        return node

    def _extract_style(self, elem: Dict[str, Any]) -> Optional[IRStyle]:
        """Extract IRStyle from an element."""
        style_data = elem.get("style", {})
        if not style_data:
            return None

        fills = []
        bg = style_data.get("background")
        if bg and bg != "transparent" and bg != "null":
            color = _parse_color(bg)
            if color:
                fills.append(IRFill(kind="solid", color=color))

        borders = []
        border_data = style_data.get("border")
        if border_data and isinstance(border_data, dict):
            border_color = _parse_color(border_data.get("color"))
            border_width = _as_float(border_data.get("width"))
            if border_color and border_width:
                borders.append(IRBorder(color=border_color, weight=border_width))

        shadows = []
        shadow_data = style_data.get("shadow")
        if shadow_data and isinstance(shadow_data, dict):
            shadow_color = _parse_color(shadow_data.get("color"))
            if shadow_color:
                shadows.append(IRShadow(
                    color=shadow_color,
                    x=float(shadow_data.get("x", 0)),
                    y=float(shadow_data.get("y", 0)),
                    blur=float(shadow_data.get("blur", 0)),
                ))

        return IRStyle(
            fills=fills,
            borders=borders,
            shadows=shadows,
            radius=_as_float(style_data.get("border_radius")),
            opacity=float(style_data.get("opacity", 1.0)),
        )

    def _extract_layout(self, elem: Dict[str, Any]) -> Optional[IRLayout]:
        """Extract IRLayout from an element."""
        layout_data = elem.get("layout", {})
        if not layout_data:
            return None

        display = layout_data.get("display", "block")
        if display == "none":
            return IRLayout(mode="none")

        mode = "auto" if display in ("flex", "grid") else "none"
        direction = layout_data.get("direction")

        padding = _parse_spacing(layout_data.get("padding"))
        gap = _as_float(layout_data.get("gap"))

        return IRLayout(
            mode=mode,
            direction=direction,
            justify=layout_data.get("justify"),
            align=layout_data.get("align"),
            padding=padding,
            gap=gap,
        )

    def _build_color_tokens(
        self,
        colors: List[str],
    ) -> Dict[str, IRToken]:
        """Build IRToken entries for extracted colors."""
        tokens = {}
        for i, color_hex in enumerate(colors):
            color = _parse_color(color_hex)
            if color:
                key = f"color_{i}"
                tokens[key] = IRToken(
                    kind="variable",
                    key=key,
                    name=f"Extracted Color {i}",
                    token_type="COLOR",
                    value=color.to_dict(),
                )
        return tokens

    def _build_font_tokens(
        self,
        fonts: List[str],
    ) -> Dict[str, IRToken]:
        """Build IRToken entries for extracted fonts."""
        tokens = {}
        for i, font_name in enumerate(fonts):
            key = f"font_{i}"
            tokens[key] = IRToken(
                kind="variable",
                key=key,
                name=font_name,
                token_type="FONT",
                value={"family": font_name},
            )
        return tokens


def _as_float(value: Any) -> Optional[float]:
    """Convert a value to float, returning None if not possible."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def analyze_image(
    image_path: str,
    *,
    api_key: Optional[str] = None,
    api_provider: str = "anthropic",
) -> IRDocument:
    """Analyze an image and return a Design IR.

    This is the main entry point for image-to-IR conversion.

    Args:
        image_path: Path to the image file.
        api_key: API key for the vision model (or set via environment).
        api_provider: "anthropic" or "openai".

    Returns:
        An IRDocument ready for the layout → code pipeline.
    """
    if api_key:
        if api_provider == "anthropic":
            os.environ["ANTHROPIC_API_KEY"] = api_key
        elif api_provider == "openai":
            os.environ["OPENAI_API_KEY"] = api_key

    config = ImageAnalyzerConfig()
    analyzer = ImageAnalyzer(config)
    return analyzer.analyze(image_path)
