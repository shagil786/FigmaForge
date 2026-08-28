import unittest

from scripts.mcp_fallback import build


def _walk(node):
    yield node
    for child in node.get("children", []):
        yield from _walk(child)


class McpFallbackFixtureTests(unittest.TestCase):
    def test_hero_background_is_behind_image_layers(self):
        raw = build()
        frame = next(n for n in _walk(raw["document"]) if n.get("id") == "1:2")
        ids = [child.get("id") for child in frame["children"]]
        self.assertLess(ids.index("11:94-base"), ids.index("11:89"))
        self.assertLess(ids.index("11:89"), ids.index("11:94"))
        self.assertLess(ids.index("11:89"), ids.index("11:90"))
        self.assertLess(ids.index("11:90"), ids.index("11:91"))

    def test_fixture_keeps_node_specific_assets_and_exact_copy(self):
        raw = build()
        nodes = {node.get("id"): node for node in _walk(raw["document"])}
        self.assertIn("1:73", raw["assets"])
        self.assertIn("1:83", raw["assets"])
        self.assertEqual(nodes["1:73"]["fills"][0]["imageRef"], "1:73")
        self.assertEqual(nodes["1:83"]["fills"][0]["imageRef"], "1:83")
        self.assertIn("Be Prepared For The Mountains And Beyond!", nodes["1:13"]["characters"])
        self.assertIn("expert backpacker", nodes["106:134"]["characters"])
        self.assertIn("clothes, which will get heavy", nodes["106:168"]["characters"])

    def test_fixture_preserves_svg_gradient_geometry(self):
        raw = build()
        nodes = {node.get("id"): node for node in _walk(raw["document"])}

        hero_paint = nodes["11:94"]["fills"][0]
        self.assertEqual(hero_paint["gradientStops"][0]["color"]["a"], 0.0)
        self.assertAlmostEqual(hero_paint["gradientHandlePositions"][0]["x"], 1015.5 / 1920)
        self.assertAlmostEqual(hero_paint["gradientHandlePositions"][0]["y"], 937.5 / 1200)
        self.assertAlmostEqual(hero_paint["gradientHandlePositions"][1]["x"], 550.0 / 1920)
        self.assertAlmostEqual(hero_paint["gradientHandlePositions"][1]["y"], -365.0 / 1200)

        content_paint = nodes["1:9-gradient"]["fills"][0]
        self.assertAlmostEqual(content_paint["gradientHandlePositions"][0]["x"], 1060 / 1920)
        self.assertAlmostEqual(content_paint["gradientHandlePositions"][1]["y"], 594.46 / 700)

    def test_fixture_preserves_all_section_geometry_and_image_order(self):
        raw = build()
        nodes = {node.get("id"): node for node in _walk(raw["document"])}
        expected = {
            "106:132": [229, 1440, 1462, 720],
            "106:160": [229, 2360, 1462, 720],
            "106:146": [229, 3280, 1462, 720],
            "106:135": [1125, 1440, 566, 720],
            "106:162": [229, 2360, 566, 720],
            "106:149": [1125, 3280, 566, 720],
        }
        for node_id, box in expected.items():
            actual = nodes[node_id]["absoluteBoundingBox"]
            self.assertEqual(
                [actual["x"], actual["y"], actual["width"], actual["height"]],
                box,
                node_id,
            )
        self.assertEqual(nodes["106:132"]["children"][0]["id"], "106:135")
        self.assertEqual(nodes["106:160"]["children"][0]["id"], "106:162")
        self.assertEqual(nodes["106:146"]["children"][0]["id"], "106:149")


if __name__ == "__main__":
    unittest.main()
