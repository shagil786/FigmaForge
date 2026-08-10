#!/usr/bin/env python3
"""
End-to-end tests for the Part-4 resolution pipeline and its JSON report.
"""

import json
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
from core.resolver import Resolver, report_to_json
from core.ir_validator import validate_ir, load_schema

REPORT_SCHEMA = plugin_root / "schemas" / "resolution-report.schema.json"


def build_report(fixture: str = "variants", file_key: str = "vars123"):
    loader = FixtureLoader(plugin_root / "fixtures" / "figma")
    doc = IRBuilder().build(FigmaFile.from_dict(file_key, loader.load(fixture)))
    return Resolver(doc, LibraryLoader().load()).resolve()


class TestResolver(unittest.TestCase):
    def setUp(self):
        self.report = build_report()

    def test_counts(self):
        counts = self.report.counts
        self.assertEqual(counts["resolved"], 2)
        self.assertEqual(counts["ambiguous"], 1)
        self.assertEqual(counts["missing"], 1)
        self.assertEqual(counts["instances_resolved"], 1)
        self.assertEqual(counts["instances_missing"], 0)
        self.assertEqual(counts["unsupported_tokens"], 1)

    def test_resolved_mappings(self):
        names = {r.figma_name: r.matches for r in self.report.resolved}
        self.assertEqual(names["Button Set"], ["button-set"])
        self.assertEqual(names["Icon Slot"], ["icon-slot"])

    def test_ambiguous_and_missing(self):
        self.assertEqual([m.figma_name for m in self.report.ambiguous], ["Card"])
        self.assertEqual([m.figma_name for m in self.report.missing], ["Navbar"])

    def test_instance_resolution(self):
        inst = self.report.instances[0]
        self.assertEqual(inst["node_id"], "3:6")
        self.assertEqual(inst["status"], "resolved")
        self.assertEqual(inst["resolved_to"], "2:3")
        self.assertEqual(inst["variant_properties"]["Size"], "Large")

    def test_variants_reported(self):
        self.assertEqual(len(self.report.variants), 1)
        variants = self.report.variants[0]
        self.assertEqual(variants["set_name"], "Button Set")
        self.assertEqual(variants["variant_count"], 3)
        self.assertEqual(variants["default_variant"], "2:3")

    def test_token_refs_resolved(self):
        refs = self.report.tokens.node_refs
        self.assertEqual([r for r in refs if r["resolved"]], refs)
        self.assertEqual(len(refs), 3)


class TestResolutionReport(unittest.TestCase):
    def setUp(self):
        self.report = build_report()

    def test_schema_validation_passes(self):
        schema = load_schema(REPORT_SCHEMA)
        self.assertEqual(validate_ir(self.report.to_dict(), schema), [])

    def test_json_serialization_is_deterministic(self):
        self.assertEqual(report_to_json(self.report), report_to_json(build_report()))
        # round-trips through json
        json.loads(report_to_json(self.report))

    def test_report_lists_unresolved_explicitly(self):
        payload = self.report.to_dict()
        self.assertEqual(len(payload["missing"]), 1)
        self.assertEqual(len(payload["ambiguous"]), 1)
        self.assertEqual(payload["tokens"]["unsupported"][0]["token_type"], "STRING")


class TestPart2FixtureIntegration(unittest.TestCase):
    def test_resolver_handles_part2_file_fixture(self):
        # The Part-2 file.json fixture has no variant data; resolution must not
        # crash and must still resolve its instance (1:2 -> component 1:100).
        report = build_report(fixture="file", file_key="abc123")
        counts = report.counts
        self.assertGreaterEqual(counts["resolved"], 1)
        self.assertEqual(counts["instances_resolved"], 1)
        instance = report.instances[0]
        self.assertEqual(instance["resolved_to"], "1:100")
        self.assertEqual(instance["resolved_name"], "Primary Button")


if __name__ == "__main__":
    unittest.main(verbosity=2)
