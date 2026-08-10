#!/usr/bin/env python3
"""
Unit tests for semantic token resolution (Part 4).

Covers the seven semantic categories, preferring existing project tokens,
token references instead of duplicated values, and explicit unsupported-token
reporting. Runs against the ``variants.json`` fixture.
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
from core.library_types import LibraryLoader
from core.token_resolver import TokenResolver, CATEGORY_BREAKPOINT


def build_token_resolution():
    loader = FixtureLoader(plugin_root / "fixtures" / "figma")
    raw = loader.load("variants")
    doc = IRBuilder().build(FigmaFile.from_dict("vars123", raw))
    library = LibraryLoader().load()
    return TokenResolver(doc, library_tokens=library.tokens).resolve()


class TestTokenCategories(unittest.TestCase):
    def setUp(self):
        self.result = build_token_resolution()
        self.categories = {t.category for t in self.result.semantic}

    def test_all_seven_categories_present(self):
        expected = {"color", "typography", "spacing", "radius", "shadow", "opacity", CATEGORY_BREAKPOINT}
        self.assertTrue(expected.issubset(self.categories))

    def test_prefers_existing_library_tokens(self):
        color = next(t for t in self.result.semantic if t.key == "color/color-primary")
        self.assertTrue(color.resolved)
        self.assertEqual(color.source, "library:color-primary")
        self.assertEqual(color.value, {"r": 0.08, "g": 0.12, "b": 0.24, "a": 1.0})

    def test_value_match_prefers_library_spacing(self):
        # "Space / 4" (16) has no name match ("space-4" != "spacing-4") but the
        # value 16 matches the existing spacing-4 token — library wins.
        spacing = next(t for t in self.result.semantic if t.key == "spacing/spacing-4")
        self.assertTrue(spacing.resolved)
        self.assertEqual(spacing.source, "library:spacing-4")

    def test_unmatched_style_is_explicit_not_dropped(self):
        fill = next(t for t in self.result.semantic if t.key == "color/primary-fill")
        self.assertFalse(fill.resolved)
        self.assertEqual(fill.source, "figma:style:S:1")

    def test_breakpoint_tokens_and_matching(self):
        keys = {t.key for t in self.result.semantic if t.category == CATEGORY_BREAKPOINT}
        self.assertIn("breakpoint/breakpoint-sm", keys)
        self.assertIn("breakpoint/breakpoint-lg", keys)
        self.assertEqual(self.result.breakpoint_matches[0]["node_id"], "6:12")
        self.assertEqual(self.result.breakpoint_matches[0]["breakpoint_token"], "breakpoint/breakpoint-lg")


class TestTokenRefs(unittest.TestCase):
    def setUp(self):
        self.result = build_token_resolution()

    def test_node_refs_reference_tokens_not_values(self):
        ref = next(r for r in self.result.node_refs
                   if r["node_id"] == "3:6" and r["property"] == "fontSize")
        self.assertEqual(ref["token_ref"], "typography/typography-button")
        self.assertTrue(ref["resolved"])
        # The reference must not carry a duplicated raw value.
        self.assertNotIn("value", ref)

    def test_all_fixture_bindings_resolve(self):
        self.assertEqual(self.result.node_refs, [r for r in self.result.node_refs if r["resolved"]])
        self.assertEqual(len(self.result.node_refs), 3)

    def test_unresolved_binding_is_explicit(self):
        # Hand-build a document with an unresolved binding to verify reporting.
        from core.ir_types import IRDocument, IRNode, IRSource, IRToken, IRTokens
        doc = IRDocument(file_key="k")
        doc.variables = {"1:99": IRToken(kind="variable", key="1:99", name="Ghost / X", token_type="FLOAT", value=1.0)}
        doc.root = IRNode(
            id="n:1", name="Ghost", kind="text", node_type="TEXT",
            source=IRSource(file_key="k", node_id="n:1", node_type="TEXT"),
            tokens=IRTokens(bound_variables={"fontSize": "1:99"}),
        )
        result = TokenResolver(doc, library_tokens=[]).resolve()
        ref = next(r for r in result.node_refs if r["node_id"] == "n:1")
        self.assertFalse(ref["resolved"])
        self.assertIn("unresolved", ref.get("reason", ""))


class TestUnsupportedTokens(unittest.TestCase):
    def setUp(self):
        self.result = build_token_resolution()

    def test_unsupported_token_type_reported(self):
        self.assertEqual(len(self.result.unsupported), 1)
        entry = self.result.unsupported[0]
        self.assertEqual(entry["key"], "1:44")
        self.assertEqual(entry["token_type"], "STRING")
        self.assertIn("not supported", entry["reason"])

    def test_unsupported_token_is_not_silently_dropped(self):
        keys = {t.key for t in self.result.semantic}
        self.assertNotIn("motion/duration", keys)  # not emitted as a semantic token
        self.assertTrue(any(u["name"] == "Motion / Duration" for u in self.result.unsupported))


if __name__ == "__main__":
    unittest.main(verbosity=2)
