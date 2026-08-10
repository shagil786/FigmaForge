#!/usr/bin/env python3
"""
Unit tests for the Figma ingestion layer.

These tests run entirely against fixtures — no Figma credentials, no network.
"""

import sys
import unittest
from pathlib import Path

# Add plugin root to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.figma_fixtures import FixtureLoader
from core.normalizer import Normalizer
from core.figma_types import (
    FigmaFile,
    FigmaNodeResponse,
    ImageSet,
    Node,
    NODE_TYPE_COMPONENT,
    NODE_TYPE_COMPONENT_SET,
    NODE_TYPE_FRAME,
    NODE_TYPE_GROUP,
    NODE_TYPE_INSTANCE,
    NODE_TYPE_PAGE,
    NODE_TYPE_TEXT,
)


class TestFixtureLoader(unittest.TestCase):
    def setUp(self):
        self.loader = FixtureLoader(plugin_root / "fixtures" / "figma")

    def test_load_file_fixture(self):
        raw = self.loader.load_file("file")
        self.assertIsInstance(raw, dict)
        self.assertEqual(raw["name"], "FigmaForge Demo — Button System")
        self.assertIn("document", raw)

    def test_load_missing_fixture_raises(self):
        from core.figma_errors import FigmaResponseError

        with self.assertRaises(FigmaResponseError):
            self.loader.load("does-not-exist")


class TestNormalizer(unittest.TestCase):
    def setUp(self):
        self.loader = FixtureLoader(plugin_root / "fixtures" / "figma")
        self.normalizer = Normalizer()
        self.raw = self.loader.load_file("file")

    def test_normalize_file(self):
        file_data = self.normalizer.normalize_file("abc123", self.raw)
        self.assertIsInstance(file_data, FigmaFile)
        self.assertEqual(file_data.file_key, "abc123")
        self.assertEqual(file_data.name, "FigmaForge Demo — Button System")
        # raw payload retained separately for debugging
        self.assertEqual(file_data.raw, self.raw)

    def test_pages_extracted(self):
        file_data = self.normalizer.normalize_file("abc123", self.raw)
        pages = self.normalizer.pages(file_data)
        self.assertEqual(len(pages), 2)
        self.assertTrue(all(p.type == NODE_TYPE_PAGE for p in pages))
        self.assertEqual(pages[0].name, "Buttons")

    def test_frames_and_groups(self):
        file_data = self.normalizer.normalize_file("abc123", self.raw)
        frames = self.normalizer.frames(file_data)
        types = {n.type for n in frames}
        self.assertIn(NODE_TYPE_FRAME, types)
        self.assertIn(NODE_TYPE_GROUP, types)

    def test_text_nodes_with_style_and_hyperlink(self):
        file_data = self.normalizer.normalize_file("abc123", self.raw)
        texts = self.normalizer.text_nodes(file_data)
        self.assertEqual(len(texts), 2)
        click_me = next(t for t in texts if t.characters == "Click me")
        self.assertEqual(click_me.text_style.font_family, "Inter")
        self.assertEqual(click_me.text_style.font_size, 16)
        self.assertEqual(click_me.link_url, "https://example.com/click")

    def test_components_and_component_sets(self):
        file_data = self.normalizer.normalize_file("abc123", self.raw)
        components = self.normalizer.components(file_data)
        sets = self.normalizer.component_sets(file_data)
        self.assertEqual(len(components), 1)
        self.assertEqual(len(sets), 1)
        self.assertEqual(components[0].type, NODE_TYPE_COMPONENT)
        self.assertEqual(sets[0].type, NODE_TYPE_COMPONENT_SET)

    def test_instances(self):
        file_data = self.normalizer.normalize_file("abc123", self.raw)
        instances = self.normalizer.instances(file_data)
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].type, NODE_TYPE_INSTANCE)
        self.assertEqual(instances[0].component_id, "1:100")

    def test_fills_strokes_effects_opacity(self):
        file_data = self.normalizer.normalize_file("abc123", self.raw)
        card = next(n for n in self.normalizer.all_nodes(file_data) if n.name == "Button Card")
        self.assertEqual(len(card.fills), 1)
        self.assertEqual(card.fills[0].type, "SOLID")
        self.assertAlmostEqual(card.fills[0].color.a, 1.0)
        self.assertEqual(len(card.strokes), 1)
        self.assertEqual(len(card.effects), 1)
        self.assertEqual(card.effects[0].type, "DROP_SHADOW")
        self.assertEqual(card.opacity, 1.0)

    def test_constraints(self):
        file_data = self.normalizer.normalize_file("abc123", self.raw)
        card = next(n for n in self.normalizer.all_nodes(file_data) if n.name == "Button Card")
        self.assertEqual(card.constraints.horizontal, "LEFT")
        self.assertEqual(card.constraints.vertical, "TOP")

    def test_auto_layout(self):
        file_data = self.normalizer.normalize_file("abc123", self.raw)
        card = next(n for n in self.normalizer.all_nodes(file_data) if n.name == "Button Card")
        self.assertEqual(card.auto_layout.layout_mode, "VERTICAL")
        self.assertEqual(card.auto_layout.padding_top, 16)
        self.assertEqual(card.auto_layout.item_spacing, 8)
        self.assertEqual(card.auto_layout.primary_axis_align_items, "CENTER")

    def test_variables_and_styles(self):
        file_data = self.normalizer.normalize_file("abc123", self.raw)
        self.assertEqual(len(file_data.styles), 2)
        self.assertEqual(file_data.styles["S:1"].style_type, "FILL")
        self.assertEqual(len(file_data.variables), 2)
        # node-level bound variables survive
        text = next(n for n in self.normalizer.text_nodes(file_data) if n.characters == "Click me")
        self.assertEqual(text.bound_variables["fontSize"]["id"], "1:40")

    def test_annotations_and_links(self):
        file_data = self.normalizer.normalize_file("abc123", self.raw)
        nodes = self.normalizer.all_nodes(file_data)
        primary_text = next(n for n in nodes if n.name == "Primary Text")
        self.assertEqual(primary_text.annotation, "The default call-to-action label.")
        # component-level url link
        primary = next(n for n in nodes if n.name == "Primary")
        self.assertTrue(primary.link_url.endswith("/design/demo/primary"))
        # component documentation link
        self.assertEqual(file_data.components["1:100"].documentation_links[0].url,
                         "https://example.com/docs/primary-button")

    def test_opacity_instance(self):
        file_data = self.normalizer.normalize_file("abc123", self.raw)
        instance = self.normalizer.instances(file_data)[0]
        self.assertAlmostEqual(instance.opacity, 0.9)


class TestNormalizeNodes(unittest.TestCase):
    def test_nodes_response(self):
        from core.figma_types import Node

        raw = {
            "name": "Subset",
            "nodes": {
                "2:3": {"document": {"id": "2:3", "name": "Button Card", "type": "FRAME"}}
            },
        }
        file_data = Normalizer().normalize_nodes("abc123", raw)
        self.assertIsInstance(file_data, FigmaNodeResponse)
        self.assertIn("2:3", file_data.nodes)
        self.assertEqual(file_data.nodes["2:3"].type, NODE_TYPE_FRAME)


if __name__ == "__main__":
    unittest.main(verbosity=2)
