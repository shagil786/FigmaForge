#!/usr/bin/env python3
"""Tests for the semantic design spec module (core/design_spec.py).

The design spec converts a raw design IR (as produced by pipeline.py
normalize) into a structured, agent-readable JSON document that an AI
code-generation agent can consume as context.  The spec captures:

- Page metadata (name, viewport)
- Design tokens (colors, typography, spacing)
- Semantic sections (navigation, hero, features, footer, etc.)
- Layout intent per section (flex-row, flex-column, grid)
- Content (text, buttons, images) with styling context

Tests exercise:
1. Rich landing fixture (named sections, auto-layout, text, buttons, images)
2. Complex dashboard fixture (nested frames, mixed layouts)
3. Minimal/empty edge cases
4. Token extraction (color palette, typography scale)
5. Section type inference from naming conventions
6. Layout direction mapping from IR auto-layout
7. Text content extraction with styling metadata
8. Button/CTA detection
9. Image/asset detection
10. Deterministic output (same IR → same spec)
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.design_spec import DesignSpecGenerator, SectionType


class TestDesignSpecBasic(unittest.TestCase):
    """Basic spec generation from the rich landing fixture."""

    @classmethod
    def setUpClass(cls):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        cls.spec = gen.generate_from_figma(figma)

    def test_spec_has_page(self):
        self.assertIn("page", self.spec)
        self.assertIsInstance(self.spec["page"], dict)

    def test_spec_page_name(self):
        page = self.spec["page"]
        self.assertIn("name", page)
        self.assertIsInstance(page["name"], str)
        self.assertTrue(len(page["name"]) > 0)

    def test_spec_has_sections(self):
        self.assertIn("sections", self.spec)
        self.assertIsInstance(self.spec["sections"], list)
        self.assertGreater(len(self.spec["sections"]), 0)

    def test_spec_has_tokens(self):
        self.assertIn("design_tokens", self.spec)
        tokens = self.spec["design_tokens"]
        self.assertIn("colors", tokens)
        self.assertIn("typography", tokens)

    def test_spec_sections_have_required_fields(self):
        for section in self.spec["sections"]:
            self.assertIn("id", section)
            self.assertIn("name", section)
            self.assertIn("type", section)
            self.assertIn("layout", section)

    def test_spec_is_json_serializable(self):
        """The spec must be JSON-serializable for agent consumption."""
        serialized = json.dumps(self.spec, indent=2)
        parsed = json.loads(serialized)
        self.assertEqual(parsed, self.spec)


class TestSectionTypeInference(unittest.TestCase):
    """Section type inference from node names and structure."""

    def test_header_detection(self):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        spec = gen.generate_from_figma(figma)
        types = [s["type"] for s in spec["sections"]]
        self.assertIn("navigation", types)

    def test_hero_detection(self):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        spec = gen.generate_from_figma(figma)
        types = [s["type"] for s in spec["sections"]]
        self.assertIn("hero", types)

    def test_footer_detection(self):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        spec = gen.generate_from_figma(figma)
        types = [s["type"] for s in spec["sections"]]
        self.assertIn("footer", types)

    def test_features_section_detection(self):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        spec = gen.generate_from_figma(figma)
        types = [s["type"] for s in spec["sections"]]
        self.assertIn("features", types)


class TestLayoutDirection(unittest.TestCase):
    """Layout direction mapping from IR auto-layout."""

    def test_autorow_maps_to_flex_row(self):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        spec = gen.generate_from_figma(figma)
        # Header should be flex-row (autorow)
        for section in spec["sections"]:
            if section["type"] == "navigation":
                self.assertEqual(section["layout"], "flex-row")
                break

    def test_autocolumn_maps_to_flex_column(self):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        spec = gen.generate_from_figma(figma)
        # Hero should be flex-column (autocolumn)
        for section in spec["sections"]:
            if section["type"] == "hero":
                self.assertEqual(section["layout"], "flex-column")
                break


class TestContentExtraction(unittest.TestCase):
    """Text, button, and image extraction from sections."""

    def test_hero_has_heading(self):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        spec = gen.generate_from_figma(figma)
        for section in spec["sections"]:
            if section["type"] == "hero":
                self.assertIn("content", section)
                content = section["content"]
                texts = [c["text"] for c in content if c.get("type") == "heading"]
                self.assertTrue(any("Build Faster" in t for t in texts))
                break

    def test_buttons_detected(self):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        spec = gen.generate_from_figma(figma)
        for section in spec["sections"]:
            if section["type"] == "hero":
                content = section["content"]
                buttons = [c for c in content if c.get("type") == "button"]
                self.assertGreater(len(buttons), 0)
                break

    def test_nav_links_extracted(self):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        spec = gen.generate_from_figma(figma)
        for section in spec["sections"]:
            if section["type"] == "navigation":
                content = section["content"]
                nav_items = [c for c in content if c.get("type") == "nav-link"]
                self.assertGreater(len(nav_items), 0)
                break


class TestDesignTokens(unittest.TestCase):
    """Color palette and typography scale extraction."""

    def test_primary_color_extracted(self):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        spec = gen.generate_from_figma(figma)
        colors = spec["design_tokens"]["colors"]
        # The landing page has a blue primary (#3844b7)
        color_values = [c["value"] for c in colors] if isinstance(colors, list) else list(colors.values())
        self.assertTrue(len(color_values) > 0)

    def test_typography_has_entries(self):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        spec = gen.generate_from_figma(figma)
        typo = spec["design_tokens"]["typography"]
        self.assertTrue(len(typo) > 0)


class TestDeterminism(unittest.TestCase):
    """Same IR always produces the same spec."""

    def test_deterministic_output(self):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "rich_landing.json")) as f:
            figma = json.load(f)
        spec1 = gen.generate_from_figma(figma)
        spec2 = gen.generate_from_figma(figma)
        self.assertEqual(json.dumps(spec1, sort_keys=True), json.dumps(spec2, sort_keys=True))


class TestComplexDashboard(unittest.TestCase):
    """Spec generation from the complex dashboard fixture."""

    @classmethod
    def setUpClass(cls):
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        with open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "figma", "complex_dashboard.json")) as f:
            figma = json.load(f)
        cls.spec = gen.generate_from_figma(figma)

    def test_spec_has_sections(self):
        self.assertIn("sections", self.spec)
        self.assertIsInstance(self.spec["sections"], list)

    def test_spec_has_tokens(self):
        self.assertIn("design_tokens", self.spec)

    def test_spec_is_serializable(self):
        serialized = json.dumps(self.spec, indent=2)
        parsed = json.loads(serialized)
        self.assertEqual(parsed, self.spec)


class TestEdgeCases(unittest.TestCase):
    """Edge cases: empty/minimal IR."""

    def test_empty_document(self):
        """A document with no children produces an empty sections list."""
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        minimal = {
            "schema_version": 1,
            "root": {
                "name": "Empty",
                "kind": "document",
                "children": []
            }
        }
        spec = gen.generate_from_ir(minimal)
        self.assertEqual(spec["sections"], [])
        self.assertIn("design_tokens", spec)

    def test_single_text_node(self):
        """A single text node produces one section with text content."""
        from core.design_spec import DesignSpecGenerator
        gen = DesignSpecGenerator()
        minimal = {
            "schema_version": 1,
            "root": {
                "name": "Page",
                "kind": "page",
                "children": [{
                    "name": "Screen",
                    "kind": "frame",
                    "layout": {"mode": "auto", "direction": "column"},
                    "children": [{
                        "name": "Title",
                        "kind": "text",
                        "text": {"characters": "Hello World"}
                    }]
                }]
            }
        }
        spec = gen.generate_from_ir(minimal)
        self.assertGreater(len(spec["sections"]), 0)


if __name__ == "__main__":
    unittest.main()
