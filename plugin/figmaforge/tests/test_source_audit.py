import unittest

from core.source_audit import audit_source


class SourceAuditTests(unittest.TestCase):
    def test_complete_source_reports_ready(self):
        raw = {
            "assets": {"img:1": "file:///tmp/image.png"},
            "document": {"type": "DOCUMENT", "children": [{
                "id": "page:1", "type": "CANVAS", "children":[{
                    "id": "img:1", "type": "FRAME",
                    "fills": [{"type": "IMAGE", "imageRef": "img:1"}],
                }]
            }]},
        }
        report = audit_source(raw)
        self.assertTrue(report["ready_for_generation"])
        self.assertEqual(report["assets_expected"], 1)
        self.assertEqual(report["assets_resolved"], 1)

    def test_missing_asset_blocks_generation(self):
        raw = {"document": {"type": "DOCUMENT", "children": [{
            "id": "img:1", "type": "RECTANGLE",
            "fills": [{"type": "IMAGE", "imageRef": "missing"}],
        }]}}
        report = audit_source(raw)
        self.assertFalse(report["ready_for_generation"])
        self.assertEqual(report["missing_assets"], ["missing"])

    def test_nested_nodes_and_fonts_are_counted(self):
        raw = {"document": {"type": "DOCUMENT", "children": [{
            "id": "text:1", "type": "TEXT", "style": {"fontFamily": "Gilroy"},
        }]}}
        report = audit_source(raw)
        self.assertEqual(report["node_count"], 2)
        self.assertEqual(report["fonts"], ["Gilroy"])


if __name__ == "__main__":
    unittest.main()
