#!/usr/bin/env python3
"""
Unit tests for component indexing, variant extraction, instance resolution,
and repository-component matching (Part 4).

These run against the ``variants.json`` fixture — no network or credentials.
"""

import sys
import unittest
from pathlib import Path

# Add plugin root to path so `core.*` packages resolve
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.figma_fixtures import FixtureLoader
from core.figma_types import FigmaFile
from core.ir_builder import IRBuilder
from core.ir_types import IRInstance, IRNode, IRSource
from core.library_types import LibraryLoader, normalize_name
from core.component_index import ComponentIndex
from core.matcher import ComponentMatcher
from core.variant_resolver import VariantResolver


def build_variant_document():
    loader = FixtureLoader(plugin_root / "fixtures" / "figma")
    raw = loader.load("variants")
    return IRBuilder().build(FigmaFile.from_dict("vars123", raw))


class TestComponentIndex(unittest.TestCase):
    def setUp(self):
        self.doc = build_variant_document()
        self.index = ComponentIndex(self.doc)

    def test_indexes_components_and_sets(self):
        ids = {c.node_id for c in self.index.all()}
        self.assertIn("5:8", ids)   # Card (component)
        self.assertIn("5:10", ids)  # Icon Slot (component)
        self.assertIn("1:101", ids)  # Button Set (component_set)
        self.assertIn("2:3", ids)   # Primary / Large (variant component)

    def test_tracks_variants(self):
        set_comp = self.index.get_by_node_id("1:101")
        self.assertTrue(set_comp.is_component_set)
        variants = self.index.variants_of("1:101")
        self.assertEqual(len(variants), 3)
        primary_large = next(v for v in variants if v.node_id == "2:3")
        self.assertTrue(primary_large.is_variant)
        self.assertTrue(primary_large.default)
        self.assertEqual(primary_large.variant_of, "1:101")

    def test_resolve_instance_by_component_id(self):
        instance = next(n for n in self.doc.all_nodes() if n.id == "3:6")
        resolved = self.index.resolve_instance(instance)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.node_id, "2:3")
        self.assertEqual(resolved.name, "Primary / Large")

    def test_resolve_instance_missing_returns_none(self):
        node = IRNode(
            id="9:9", name="Ghost", kind="instance", node_type="INSTANCE",
            source=IRSource(file_key="vars123", node_id="9:9", node_type="INSTANCE"),
            instance=IRInstance(component_id="999:999", main_component_id="999:999"),
        )
        self.assertIsNone(self.index.resolve_instance(node))

    def test_source_metadata_preserved(self):
        icon = self.index.get_by_node_id("5:10")
        self.assertEqual(icon.source.file_key, "vars123")
        self.assertEqual(icon.source.node_id, "5:10")
        self.assertEqual(icon.source.path, ["0:0", "0:1"])


class TestVariantResolver(unittest.TestCase):
    def setUp(self):
        self.doc = build_variant_document()

    def test_instance_properties(self):
        instance = next(n for n in self.doc.all_nodes() if n.id == "3:6")
        self.assertEqual(
            VariantResolver.instance_properties(instance),
            {"Size": "Large", "State": "Default", "Label": "Continue"},
        )

    def test_set_variants_with_default(self):
        set_node = next(n for n in self.doc.all_nodes() if n.id == "1:101")
        variants = VariantResolver.variants(set_node)
        self.assertEqual(len(variants), 3)
        default = next(v for v in variants if v.default)
        self.assertEqual(default.node_id, "2:3")
        self.assertEqual(variants[0].name, "Primary / Large")

    def test_parse_variant_name_kv(self):
        self.assertEqual(VariantResolver._parse_variant_name("Size=Large, State=Default"),
                         {"Size": "Large", "State": "Default"})

    def test_parse_variant_name_fallback(self):
        self.assertEqual(VariantResolver._parse_variant_name("Primary / Large"),
                         {"variant": "Primary / Large"})


class TestMatcher(unittest.TestCase):
    def setUp(self):
        self.doc = build_variant_document()
        self.index = ComponentIndex(self.doc)
        self.matcher = ComponentMatcher(LibraryLoader().load())

    def test_resolved_via_alias(self):
        result = self.matcher.match(self.index.get_by_node_id("5:10"))
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.matches, ["icon-slot"])

    def test_resolved_component_set(self):
        result = self.matcher.match(self.index.get_by_node_id("1:101"))
        self.assertEqual(result.status, "resolved")
        self.assertEqual(result.matches, ["button-set"])

    def test_ambiguous_refuses_to_guess(self):
        result = self.matcher.match(self.index.get_by_node_id("5:8"))
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(set(result.matches), {"card", "card-container"})
        self.assertIn("refusing to guess", result.reason)

    def test_missing_reported_explicitly(self):
        result = self.matcher.match(self.index.get_by_node_id("5:11"))
        self.assertEqual(result.status, "missing")
        self.assertEqual(result.matches, [])

    def test_match_all_skips_variants(self):
        results = self.matcher.match_all(self.index)
        matched_figma = {r.figma_component for r in results}
        self.assertNotIn("2:3", matched_figma)  # variant is resolved via its set
        self.assertIn("1:101", matched_figma)


class TestNameNormalization(unittest.TestCase):
    def test_separators_are_equivalent(self):
        self.assertEqual(normalize_name("icon-slot"), normalize_name("Icon Slot"))
        self.assertEqual(normalize_name("Button Set"), "button set")
        self.assertEqual(normalize_name("Primary Button"), "primary button")


if __name__ == "__main__":
    unittest.main(verbosity=2)
