"""Tests for image_analyzer.py — image-to-IR conversion (Part 23)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.image_analyzer import (
    ANALYSIS_PROMPT,
    ImageAnalyzer,
    ImageAnalyzerConfig,
    _AnthropicVisionModel,
    _OpenAIVisionModel,
    _hex_to_ir_color,
    _parse_color,
    _parse_spacing,
    analyze_image,
)
from core.ir_types import IRDocument, IR_VERSION


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


class TestHexToIrColor(unittest.TestCase):
    """Test _hex_to_ir_color conversion."""

    def test_6_digit_hex(self):
        c = _hex_to_ir_color("#ff0000")
        self.assertIsNotNone(c)
        self.assertAlmostEqual(c.r, 1.0)
        self.assertAlmostEqual(c.g, 0.0)
        self.assertAlmostEqual(c.b, 0.0)
        self.assertAlmostEqual(c.a, 1.0)

    def test_8_digit_hex_with_alpha(self):
        c = _hex_to_ir_color("#00ff0080")
        self.assertIsNotNone(c)
        self.assertAlmostEqual(c.a, 128 / 255.0, places=2)

    def test_no_hash_prefix(self):
        c = _hex_to_ir_color("0000ff")
        self.assertIsNotNone(c)
        self.assertAlmostEqual(c.b, 1.0)

    def test_invalid_hex_returns_none(self):
        self.assertIsNone(_hex_to_ir_color("not-a-color"))
        self.assertIsNone(_hex_to_ir_color(""))


class TestParseColor(unittest.TestCase):
    """Test _parse_color for various input types."""

    def test_hex_string(self):
        c = _parse_color("#1a1a1a")
        self.assertIsNotNone(c)
        self.assertAlmostEqual(c.r, 0x1a / 255.0, places=3)

    def test_dict_color(self):
        c = _parse_color({"r": 0.5, "g": 0.5, "b": 0.5, "a": 1.0})
        self.assertIsNotNone(c)
        self.assertAlmostEqual(c.r, 0.5)

    def test_none_returns_none(self):
        self.assertIsNone(_parse_color(None))

    def test_unknown_type_returns_none(self):
        self.assertIsNone(_parse_color(42))


class TestParseSpacing(unittest.TestCase):
    """Test _parse_spacing for various input types."""

    def test_uniform_number(self):
        s = _parse_spacing(16)
        self.assertIsNotNone(s)
        self.assertEqual(s.top, 16)
        self.assertEqual(s.right, 16)
        self.assertEqual(s.bottom, 16)
        self.assertEqual(s.left, 16)

    def test_dict_spacing(self):
        s = _parse_spacing({"top": 8, "right": 12, "bottom": 8, "left": 12})
        self.assertIsNotNone(s)
        self.assertEqual(s.top, 8)
        self.assertEqual(s.right, 12)

    def test_none_returns_none(self):
        self.assertIsNone(_parse_spacing(None))


# ---------------------------------------------------------------------------
# Mock vision model for integration tests
# ---------------------------------------------------------------------------


class _MockVisionModel:
    """A mock vision model that returns a canned analysis."""

    def __init__(self, analysis_data: dict | None = None):
        self._analysis = analysis_data or self._default_analysis()

    @staticmethod
    def _default_analysis() -> dict:
        return {
            "viewport": {"width": 1440, "height": 900},
            "elements": [
                {
                    "id": "header",
                    "name": "Header",
                    "type": "frame",
                    "bounds": {"x": 0, "y": 0, "width": 1440, "height": 64},
                    "style": {
                        "background": "#1a1a2e",
                        "border_radius": 0,
                        "opacity": 1.0,
                    },
                    "layout": {
                        "display": "flex",
                        "direction": "row",
                        "justify": "space-between",
                        "align": "center",
                        "padding": 16,
                        "gap": 16,
                    },
                    "typography": None,
                    "children": ["logo", "nav"],
                    "confidence": 0.95,
                },
                {
                    "id": "logo",
                    "name": "Logo Text",
                    "type": "text",
                    "bounds": {"x": 16, "y": 16, "width": 120, "height": 32},
                    "style": {"background": None, "opacity": 1.0},
                    "layout": {"display": "block"},
                    "typography": {
                        "text": "MyApp",
                        "font_family": "Inter",
                        "font_size": 24,
                        "font_weight": 700,
                        "color": "#ffffff",
                        "align": "left",
                        "line_height": 32,
                    },
                    "children": [],
                    "confidence": 0.98,
                },
                {
                    "id": "nav",
                    "name": "Navigation",
                    "type": "frame",
                    "bounds": {"x": 800, "y": 16, "width": 600, "height": 32},
                    "style": {"background": None, "opacity": 1.0},
                    "layout": {
                        "display": "flex",
                        "direction": "row",
                        "gap": 24,
                    },
                    "children": [],
                    "confidence": 0.90,
                },
                {
                    "id": "hero",
                    "name": "Hero Section",
                    "type": "frame",
                    "bounds": {"x": 0, "y": 64, "width": 1440, "height": 500},
                    "style": {
                        "background": "#f8f9fa",
                        "shadow": {"x": 0, "y": 2, "blur": 8, "color": "#00000020"},
                        "opacity": 1.0,
                    },
                    "layout": {
                        "display": "flex",
                        "direction": "column",
                        "justify": "center",
                        "align": "center",
                        "padding": 48,
                        "gap": 24,
                    },
                    "children": ["hero-title"],
                    "confidence": 0.92,
                },
                {
                    "id": "hero-title",
                    "name": "Hero Title",
                    "type": "text",
                    "bounds": {"x": 320, "y": 160, "width": 800, "height": 60},
                    "style": {"background": None, "opacity": 1.0},
                    "layout": {"display": "block"},
                    "typography": {
                        "text": "Welcome to MyApp",
                        "font_family": "Inter",
                        "font_size": 48,
                        "font_weight": 800,
                        "color": "#1a1a2e",
                        "align": "center",
                        "line_height": 60,
                    },
                    "children": [],
                    "confidence": 0.97,
                },
            ],
            "colors": ["#1a1a2e", "#ffffff", "#f8f9fa"],
            "fonts": ["Inter"],
        }

    def analyze_image(self, image_path: str, prompt: str, *, max_tokens: int = 4096) -> str:
        return json.dumps(self._analysis)


# ---------------------------------------------------------------------------
# Integration tests with mock vision model
# ---------------------------------------------------------------------------


class TestImageAnalyzerInit(unittest.TestCase):
    """Test ImageAnalyzer initialization."""

    def test_raises_without_vision_model(self):
        with self.assertRaises(ValueError):
            ImageAnalyzer(
                ImageAnalyzerConfig(
                    vision_model=None,
                )
            )

    def test_accepts_custom_vision_model(self):
        model = _MockVisionModel()
        analyzer = ImageAnalyzer(
            ImageAnalyzerConfig(vision_model=model)
        )
        self.assertIsNotNone(analyzer.config.vision_model)


class TestImageAnalyzerAnalyze(unittest.TestCase):
    """Test the full analyze() pipeline with a mock vision model."""

    def setUp(self):
        self.model = _MockVisionModel()
        self.config = ImageAnalyzerConfig(
            vision_model=self.model,
            source_file_key="test-image",
        )
        self.analyzer = ImageAnalyzer(self.config)

        # Create a temporary image file
        self.tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        # Write a minimal 1x1 PNG
        import struct
        import zlib

        def minimal_png():
            signature = b"\x89PNG\r\n\x1a\n"
            # IHDR
            ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
            ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
            # IDAT
            raw = b"\x00\xff\x00\x00"  # filter byte + RGB
            compressed = zlib.compress(raw)
            idat_crc = zlib.crc32(b"IDAT" + compressed)
            idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc)
            # IEND
            iend_crc = zlib.crc32(b"IEND")
            iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
            return signature + ihdr + idat + iend

        self.tmp.write(minimal_png())
        self.tmp.close()
        self.image_path = self.tmp.name

    def tearDown(self):
        os.unlink(self.image_path)

    def test_analyze_returns_ir_document(self):
        doc = self.analyzer.analyze(self.image_path)
        self.assertIsInstance(doc, IRDocument)
        self.assertEqual(doc.schema_version, IR_VERSION)
        self.assertEqual(doc.file_key, "test-image")

    def test_analyze_extracts_elements(self):
        doc = self.analyzer.analyze(self.image_path)
        # Root frame should have children
        self.assertIsNotNone(doc.root)
        self.assertIsNotNone(doc.root.children)
        self.assertGreater(len(doc.root.children), 0)

    def test_analyze_extracts_text_content(self):
        doc = self.analyzer.analyze(self.image_path)
        # Find text nodes
        text_nodes = []
        def _collect(node):
            if node.text and node.text.characters:
                text_nodes.append(node)
            for child in (node.children or []):
                _collect(child)
        _collect(doc.root)
        self.assertGreater(len(text_nodes), 0)
        self.assertIn("MyApp", text_nodes[0].text.characters)

    def test_analyze_extracts_typography(self):
        doc = self.analyzer.analyze(self.image_path)
        typo_nodes = []
        def _collect(node):
            if node.typography:
                typo_nodes.append(node)
            for child in (node.children or []):
                _collect(child)
        _collect(doc.root)
        self.assertGreater(len(typo_nodes), 0)
        # Logo should be Inter 700
        logo = typo_nodes[0]
        self.assertEqual(logo.typography.font_family, "Inter")
        self.assertEqual(logo.typography.font_size, 24.0)
        self.assertEqual(logo.typography.font_weight, 700.0)

    def test_analyze_extracts_colors(self):
        doc = self.analyzer.analyze(self.image_path)
        self.assertIsNotNone(doc.styles)
        self.assertGreater(len(doc.styles), 0)

    def test_analyze_extracts_fonts(self):
        doc = self.analyzer.analyze(self.image_path)
        self.assertIsNotNone(doc.variables)
        self.assertGreater(len(doc.variables), 0)
        # Inter font should be extracted
        font_tokens = [v for v in doc.variables.values() if v.token_type == "FONT"]
        self.assertGreater(len(font_tokens), 0)
        self.assertEqual(font_tokens[0].name, "Inter")

    def test_analyze_extracts_root_dimensions(self):
        doc = self.analyzer.analyze(self.image_path)
        self.assertEqual(doc.root.dimensions.width, 1440.0)
        self.assertEqual(doc.root.dimensions.height, 900.0)


class TestImageAnalyzerParsing(unittest.TestCase):
    """Test _parse_response with various model output formats."""

    def setUp(self):
        self.model = _MockVisionModel()
        self.config = ImageAnalyzerConfig(vision_model=self.model)
        self.analyzer = ImageAnalyzer(self.config)

    def test_parse_json_in_markdown_block(self):
        data = _MockVisionModel._default_analysis()
        response = f"Here's the analysis:\n```json\n{json.dumps(data)}\n```\n"
        result = self.analyzer._parse_response(response)
        self.assertIn("elements", result)
        self.assertEqual(len(result["elements"]), 5)

    def test_parse_raw_json(self):
        data = _MockVisionModel._default_analysis()
        response = json.dumps(data)
        result = self.analyzer._parse_response(response)
        self.assertIn("elements", result)

    def test_parse_invalid_json_returns_nlp_elements(self):
        result = self.analyzer._parse_response("not json at all")
        self.assertIn("elements", result)
        # NLP fallback parser returns elements from natural language
        self.assertIsInstance(result["elements"], list)


class TestImageAnalyzerConvenience(unittest.TestCase):
    """Test the analyze_image convenience function."""

    def test_convenience_function(self):
        model = _MockVisionModel()
        with patch("core.image_analyzer._create_default_vision_model", return_value=model):
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            # Minimal PNG
            import struct
            import zlib
            signature = b"\x89PNG\r\n\x1a\n"
            ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
            ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
            raw = b"\x00\xff\x00\x00"
            compressed = zlib.compress(raw)
            idat_crc = zlib.crc32(b"IDAT" + compressed)
            idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc)
            iend_crc = zlib.crc32(b"IEND")
            iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
            tmp.write(signature + ihdr + idat + iend)
            tmp.close()

            try:
                doc = analyze_image(tmp.name)
                self.assertIsInstance(doc, IRDocument)
                self.assertIsNotNone(doc.root)
            finally:
                os.unlink(tmp.name)


class TestImageAnalyzerEdgeCases(unittest.TestCase):
    """Test edge cases and error handling."""

    def test_file_not_found(self):
        model = _MockVisionModel()
        config = ImageAnalyzerConfig(vision_model=model)
        analyzer = ImageAnalyzer(config)
        with self.assertRaises(FileNotFoundError):
            analyzer.analyze("/nonexistent/image.png")

    def test_empty_elements(self):
        model = _MockVisionModel(analysis_data={
            "viewport": {"width": 800, "height": 600},
            "elements": [],
            "colors": [],
            "fonts": [],
        })
        config = ImageAnalyzerConfig(vision_model=model)
        analyzer = ImageAnalyzer(config)

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        import struct, zlib
        signature = b"\x89PNG\r\n\x1a\n"
        ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
        ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
        raw = b"\x00\xff\x00\x00"
        compressed = zlib.compress(raw)
        idat_crc = zlib.crc32(b"IDAT" + compressed)
        idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc)
        iend_crc = zlib.crc32(b"IEND")
        iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
        tmp.write(signature + ihdr + idat + iend)
        tmp.close()

        try:
            doc = analyzer.analyze(tmp.name)
            self.assertIsNotNone(doc)
            # Root frame still exists with default dimensions
            self.assertIsNotNone(doc.root.dimensions)
        finally:
            os.unlink(tmp.name)

    def test_circular_children_reference(self):
        """Elements referencing each other as children should not loop."""
        model = _MockVisionModel(analysis_data={
            "viewport": {"width": 400, "height": 300},
            "elements": [
                {
                    "id": "a", "name": "A", "type": "frame",
                    "bounds": {"x": 0, "y": 0, "width": 400, "height": 300},
                    "style": {}, "layout": {},
                    "children": ["b"], "confidence": 0.9,
                },
                {
                    "id": "b", "name": "B", "type": "frame",
                    "bounds": {"x": 10, "y": 10, "width": 380, "height": 280},
                    "style": {}, "layout": {},
                    "children": ["a"], "confidence": 0.9,  # circular!
                },
            ],
            "colors": [], "fonts": [],
        })
        config = ImageAnalyzerConfig(vision_model=model)
        analyzer = ImageAnalyzer(config)

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        import struct, zlib
        signature = b"\x89PNG\r\n\x1a\n"
        ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
        ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
        raw = b"\x00\xff\x00\x00"
        compressed = zlib.compress(raw)
        idat_crc = zlib.crc32(b"IDAT" + compressed)
        idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc)
        iend_crc = zlib.crc32(b"IEND")
        iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
        tmp.write(signature + ihdr + idat + iend)
        tmp.close()

        try:
            doc = analyzer.analyze(tmp.name)
            self.assertIsNotNone(doc)
            # Should not hang or stack overflow
        finally:
            os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
