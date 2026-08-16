"""Tests for deterministic, backend-neutral accessibility diagnostics."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.accessibility import analyze_document
from core.ir_types import (
    IRColor,
    IRDocument,
    IRFill,
    IRNode,
    IRSource,
    IRStyle,
)


def node(node_id, name, kind="frame", style=None, annotations=None, children=None):
    return IRNode(
        id=node_id,
        name=name,
        kind=kind,
        node_type=kind.upper(),
        source=IRSource(file_key="test", node_id=node_id),
        style=style,
        annotations=annotations,
        children=children or [],
    )


class TestAccessibility(unittest.TestCase):
    def test_interactive_nodes_require_an_accessible_name(self):
        root = node("root", "Page", children=[node("button", "Button")])

        report = analyze_document(IRDocument(file_key="test", root=root))

        finding = next(f for f in report.findings if f.node_id == "button")
        self.assertEqual(finding.rule, "accessible_name")
        self.assertEqual(finding.severity, "error")

    def test_named_interactive_nodes_and_good_contrast_are_clean(self):
        root = node(
            "root", "Page",
            style=IRStyle(fills=[IRFill(kind="solid", color=IRColor(1, 1, 1))]),
            children=[node(
                "button", "Submit button",
                annotations={"aria-label": "Submit"},
                style=IRStyle(fills=[IRFill(kind="solid", color=IRColor(0, 0, 0))]),
            )],
        )

        report = analyze_document(IRDocument(file_key="test", root=root))

        self.assertFalse([f for f in report.findings if f.rule == "accessible_name"])
        self.assertFalse([f for f in report.findings if f.rule == "color_contrast"])

    def test_low_contrast_text_is_reported_deterministically(self):
        root = node(
            "root", "Page",
            style=IRStyle(fills=[IRFill(kind="solid", color=IRColor(1, 1, 1))]),
            children=[node(
                "label", "Label", kind="text",
                style=IRStyle(fills=[IRFill(kind="solid", color=IRColor(0.8, 0.8, 0.8))]),
            )],
        )

        report = analyze_document(IRDocument(file_key="test", root=root))

        finding = next(f for f in report.findings if f.rule == "color_contrast")
        self.assertEqual(finding.node_id, "label")
        self.assertEqual(finding.severity, "error")
        self.assertIn("contrast ratio", finding.message)
        self.assertEqual(report.to_dict(), analyze_document(IRDocument(file_key="test", root=root)).to_dict())


if __name__ == "__main__":
    unittest.main()
