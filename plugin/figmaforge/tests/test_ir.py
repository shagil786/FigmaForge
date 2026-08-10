#!/usr/bin/env python3
"""
Unit tests for the Design Intermediate Representation (IR).

These tests build an ``IRDocument`` from the Part-2 Figma fixtures (``file.json``,
``images.json``) and exercise every modeled area plus schema validation. No
network or credentials required.
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
from core.ir_types import (
    IRDocument,
    ir_to_json,
    KIND_DOCUMENT,
    KIND_PAGE,
    KIND_FRAME,
    KIND_GROUP,
    KIND_COMPONENT,
    KIND_COMPONENT_SET,
    KIND_INSTANCE,
    KIND_TEXT,
    KIND_VECTOR,
)
from core.ir_validator import IRValidationError, ensure_valid, load_schema, validate_ir


def build_fixture_document(images: bool = True) -> IRDocument:
    """Normalize the ``file.json`` fixture into an IR document."""
    loader = FixtureLoader(plugin_root / "fixtures" / "figma")
    raw = loader.load_file("file")
    figma_file = FigmaFile.from_dict("abc123", raw)
    img = loader.load("images")["images"] if images else None
    return IRBuilder(images=img).build(figma_file)


class TestIRStructure(unittest.TestCase):
    def setUp(self):
        self.doc = build_fixture_document()
        self.nodes = self.doc.all_nodes()

    def node(self, node_id):
        return next(n for n in self.nodes if n.id == node_id)

    # 1. Documents and pages
    def test_document_and_pages(self):
        self.assertEqual(self.doc.file_key, "abc123")
        self.assertEqual(self.doc.name, "FigmaForge Demo — Button System")
        self.assertEqual(self.doc.root.kind, KIND_DOCUMENT)
        self.assertEqual([p.name for p in self.doc.pages], ["Buttons", "Fundamentals"])
        self.assertTrue(all(p.kind == KIND_PAGE for p in self.doc.pages))

    # 2. Frames and sections / groups
    def test_frames_groups_vectors(self):
        kinds = {n.kind for n in self.nodes}
        self.assertIn(KIND_FRAME, kinds)
        self.assertIn(KIND_GROUP, kinds)
        self.assertIn(KIND_VECTOR, kinds)
        self.assertEqual(self.node("2:3").node_type, "FRAME")

    # 3. Text nodes
    def test_text_nodes(self):
        click = self.node("3:4")
        self.assertEqual(click.kind, KIND_TEXT)
        self.assertEqual(click.text.characters, "Click me")
        self.assertEqual(click.typography.font_family, "Inter")
        self.assertEqual(click.typography.font_size, 16.0)

    # 4. Components and component instances
    def test_components_and_component_sets(self):
        self.assertEqual(self.node("7:9").kind, KIND_COMPONENT)
        self.assertEqual(self.node("7:9").component.name, "Primary")
        self.assertEqual(self.node("6:8").kind, KIND_COMPONENT_SET)
        self.assertEqual(self.node("6:8").component.kind, "component_set")
        # file-level maps carry keys + docs links
        self.assertIn("1:100", self.doc.components)
        self.assertEqual(
            self.doc.components["1:100"].documentation_links[0].url,
            "https://example.com/docs/primary-button",
        )

    def test_instances(self):
        inst = self.node("1:2")
        self.assertEqual(inst.kind, KIND_INSTANCE)
        self.assertEqual(inst.instance.component_id, "1:100")
        self.assertEqual(inst.instance.main_component_id, "1:100")

    # 5/6. Auto-layout + positioning
    def test_auto_layout(self):
        card = self.node("2:3")
        self.assertEqual(card.layout.mode, "auto")
        self.assertEqual(card.layout.direction, "column")
        self.assertEqual(card.layout.gap, 8.0)
        self.assertEqual(card.layout.padding.to_dict(),
                         {"top": 16.0, "right": 16.0, "bottom": 16.0, "left": 16.0})
        self.assertEqual(card.layout.justify, "CENTER")
        self.assertEqual(card.layout.align, "CENTER")

    def test_positioning_absolute_and_auto(self):
        inst = self.node("1:2")  # absolutely positioned under a page
        self.assertEqual(inst.position.mode, "absolute")
        self.assertEqual(inst.position.x, 0.0)
        click = self.node("3:4")  # inside auto-layout frame
        self.assertEqual(click.position.mode, "auto")

    # 7. Dimensions + min/max
    def test_dimensions(self):
        inst = self.node("1:2")
        self.assertEqual(inst.dimensions.width, 120.0)
        self.assertEqual(inst.dimensions.height, 40.0)
        card = self.node("2:3")
        self.assertEqual(card.dimensions.sizing_horizontal, "FIXED")
        self.assertEqual(card.dimensions.sizing_vertical, "AUTO")

    # 9. Fills, borders, shadows, opacity, radius
    def test_fills_borders_shadows(self):
        card = self.node("2:3")
        self.assertEqual(len(card.style.fills), 1)
        self.assertEqual(card.style.fills[0].kind, "solid")
        self.assertEqual(card.style.fills[0].color.to_dict(),
                         {"r": 0.08, "g": 0.12, "b": 0.24, "a": 1.0})
        self.assertEqual(len(card.style.borders), 1)
        self.assertEqual(card.style.borders[0].color.to_dict(),
                         {"r": 0.9, "g": 0.92, "b": 0.95, "a": 1.0})
        self.assertEqual(len(card.style.shadows), 1)
        shadow = card.style.shadows[0]
        self.assertEqual(shadow.kind, "drop")
        self.assertEqual((shadow.x, shadow.y, shadow.blur, shadow.spread),
                         (0.0, 4.0, 8.0, 0.0))
        self.assertEqual(card.style.opacity, 1.0)

    def test_instance_opacity(self):
        self.assertAlmostEqual(self.node("1:2").opacity, 0.9)

    # 10. Typography and text styles
    def test_typography_and_text_styles(self):
        click = self.node("3:4")
        self.assertEqual(click.typography.font_weight, 400.0)
        self.assertEqual(click.typography.line_height, 24.0)
        self.assertEqual(click.typography.text_case, "ORIGINAL")
        self.assertEqual(click.typography.text_decoration, "NONE")
        primary = self.node("8:10")
        self.assertEqual(primary.typography.text_case, "UPPER")

    # 11. Variables and design tokens
    def test_variables_and_tokens(self):
        self.assertIn("1:33", self.doc.variables)
        self.assertIn("1:40", self.doc.variables)
        self.assertEqual(self.doc.variables["1:40"].token_type, "FLOAT")
        self.assertEqual(self.doc.variables["1:40"].value, 16.0)
        # styles map
        self.assertIn("S:1", self.doc.styles)
        self.assertEqual(self.doc.styles["S:1"].token_type, "FILL")
        # node-level bound variables
        click = self.node("3:4")
        self.assertEqual(click.tokens.bound_variables, {"fontSize": "1:40"})
        card = self.node("2:3")
        self.assertEqual(card.tokens.bound_variables, {"paddingLeft": "1:33"})

    # 12. Assets and image references
    def test_assets_and_image_refs(self):
        self.assertEqual(
            self.doc.assets,
            {
                "3:4": "https://s3-alpha.figma.com/assets/click.png",
                "2:3": "https://s3-alpha.figma.com/assets/card.svg",
            },
        )
        self.assertEqual(self.node("3:4").asset.url,
                         "https://s3-alpha.figma.com/assets/click.png")
        self.assertEqual(self.node("3:4").asset.node_id, "3:4")

    def test_no_assets_when_images_not_provided(self):
        doc = build_fixture_document(images=False)
        self.assertEqual(doc.assets, {})
        click = next(n for n in doc.all_nodes() if n.id == "3:4")
        self.assertIsNone(click.asset)

    # 13. Responsive constraints
    def test_responsive_constraints(self):
        inst = self.node("1:2")
        self.assertEqual(inst.responsive.constraints_vertical, "CENTER")
        self.assertEqual(inst.responsive.constraints_horizontal, "SCALE")
        card = self.node("2:3")
        self.assertEqual(card.responsive.constraints_vertical, "TOP")
        self.assertEqual(card.responsive.constraints_horizontal, "LEFT")

    # 14. Prototype links and interactions
    def test_prototype_links(self):
        click = self.node("3:4")
        self.assertEqual(click.prototype.links[0].url, "https://example.com/click")
        self.assertEqual(click.prototype.links[0].kind, "url")
        primary = self.node("7:9")
        self.assertEqual(primary.prototype.url,
                         "https://www.figma.com/design/demo/primary")

    # 15. Annotations and developer metadata
    def test_annotations(self):
        primary_text = self.node("8:10")
        self.assertEqual(primary_text.annotations.annotation,
                         "The default call-to-action label.")
        self.assertEqual(primary_text.annotations.developer_metadata, {})

    # Cross-cutting requirements
    def test_source_metadata_preserved(self):
        card = self.node("2:3")
        self.assertEqual(card.source.file_key, "abc123")
        self.assertEqual(card.source.node_id, "2:3")
        self.assertEqual(card.source.node_type, "FRAME")
        self.assertEqual(card.source.path, ["0:0", "0:1"])

    def test_parent_child_relationships(self):
        page = next(p for p in self.doc.pages if p.name == "Buttons")
        self.assertEqual([c.id for c in page.children], ["1:2", "2:3"])
        self.assertEqual(self.node("2:3").children[0].id, "3:4")

    def test_unknown_properties_preserved(self):
        card = self.node("2:3")
        self.assertIn("backgroundColor", card.unknown)
        self.assertEqual(card.unknown["backgroundColor"],
                         {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0})

    def test_unsupported_properties_report(self):
        loader = FixtureLoader(plugin_root / "fixtures" / "figma")
        raw = loader.load_file("file")
        figma_file = FigmaFile.from_dict("abc123", raw)
        builder = IRBuilder()
        builder.build(figma_file)
        report = builder.unsupported_properties()
        self.assertEqual(report, {"2:3": ["backgroundColor"]})

    def test_raw_preserved_for_debugging(self):
        card = self.node("2:3")
        self.assertEqual(card.raw["type"], "FRAME")
        self.assertEqual(card.raw["name"], "Button Card")


class TestIRSerialization(unittest.TestCase):
    def setUp(self):
        self.doc = build_fixture_document()

    def test_to_dict_is_json_safe(self):
        import json
        payload = self.doc.to_dict()
        # round-trips through json without error
        json.dumps(payload)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["root"]["id"], "0:0")

    def test_ir_to_json_is_deterministic(self):
        first = ir_to_json(self.doc)
        second = ir_to_json(build_fixture_document())
        self.assertEqual(first, second)


class TestIRValidation(unittest.TestCase):
    def setUp(self):
        self.doc = build_fixture_document()
        self.schema = load_schema()

    def test_schema_file_loads(self):
        self.assertEqual(self.schema["title"], "FigmaForge Design IR")
        self.assertEqual(self.schema["$schema"], "http://json-schema.org/draft-07/schema#")

    def test_valid_ir_passes_validation(self):
        self.assertEqual(validate_ir(self.doc.to_dict(), self.schema), [])
        ensure_valid(self.doc.to_dict(), self.schema)  # should not raise

    def test_invalid_ir_fails_validation(self):
        payload = self.doc.to_dict()
        payload["schema_version"] = "nope"
        self.assertNotEqual(validate_ir(payload, self.schema), [])
        with self.assertRaises(IRValidationError):
            ensure_valid(payload, self.schema)

    def test_missing_required_key_fails(self):
        payload = self.doc.to_dict()
        del payload["file_key"]
        self.assertTrue(any("file_key" in e for e in validate_ir(payload, self.schema)))

    def test_bad_kind_fails(self):
        payload = self.doc.to_dict()
        payload["root"]["kind"] = "banana"
        self.assertTrue(any("kind" in e for e in validate_ir(payload, self.schema)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
