"""
IR JSON round-trip tests (Part 16, Task 1).

The contract is JSON identity: ``IRDocument.from_dict(x.to_dict()).to_dict()``
must equal ``x.to_dict()`` exactly.  ``to_dict`` rounds floats to 4 decimals
(``_round``), so dataclass equality cannot survive the round-trip — the
artifact-stability guarantee (what the pipeline needs) is the JSON form.

Run:  python3 -m unittest tests.test_ir_roundtrip -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

import sys

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.figma_fixtures import FixtureLoader  # noqa: E402
from core.figma_types import FigmaFile  # noqa: E402
from core.ir_builder import IRBuilder  # noqa: E402
from core.ir_types import (  # noqa: E402
    IRColor,
    IRDocument,
    IRGradientStop,
    IRFill,
    IRInstance,
    IRInteraction,
    IRLink,
    IRLayout,
    IRNode,
    IRPosition,
    IRPrototype,
    IRSource,
    IRStyle,
    IRTextContent,
    IRTokenRef,
    IRTokens,
    IRTypography,
    IResponsive,
    IRSpacing,
    IRAnnotations,
    IRAssetRef,
    IRShadow,
    IRBlur,
    IRBorder,
    IRDimensions,
    KIND_FRAME,
    KIND_PAGE,
    KIND_TEXT,
)

FIXTURES = {
    "layout_desktop": "lay1440",
    "variants": "variants",
    "layout_mobile": "mobile",
    "layout_nested": "nested",
}


def build_doc(fixture: str, file_key: str) -> IRDocument:
    loader = FixtureLoader(plugin_root / "fixtures" / "figma")
    raw = loader.load(fixture)
    return IRBuilder().build(FigmaFile.from_dict(file_key, raw))


def _rich_document() -> IRDocument:
    """A programmatic IR exercising every serialized surface: gradient fills,
    shadows, blur, per-corner radius, borders, full typography, instance,
    tokens, responsive, prototype links/interactions, annotations, assets,
    component maps, and unknown/raw passthrough."""
    page = IRNode(
        id="0:1",
        name="Page",
        kind=KIND_PAGE,
        node_type="CANVAS",
        source=IRSource(file_key="rich", node_id="0:1", node_type="CANVAS", path=["0:1"]),
        visible=True,
        opacity=1.0,
        children=[
            IRNode(
                id="1:1",
                name="Hero",
                kind=KIND_FRAME,
                node_type="FRAME",
                source=IRSource(file_key="rich", node_id="1:1", node_type="FRAME", path=["0:1", "1:1"]),
                visible=True,
                opacity=0.5,
                dimensions=IRDimensions(width=1440.0, height=600.0, sizing_horizontal="FIXED"),
                position=IRPosition(mode="absolute", x=0.0, y=0.0),
                layout=IRLayout(
                    mode="auto",
                    direction="row",
                    justify="center",
                    align="center",
                    padding=IRSpacing(top=24.0, right=24.0, bottom=24.0, left=24.0),
                    gap=12.0,
                    wrap="WRAP",
                    grow=1.0,
                    shrink=0.0,
                    align_self="MIN",
                ),
                style=IRStyle(
                    fills=[
                        IRFill(
                            kind="gradient",
                            opacity=1.0,
                            visible=True,
                            gradient_stops=[
                                IRGradientStop(position=0.0, color=IRColor(0.8431372549, 0.0, 0.0, 1.0)),
                                IRGradientStop(position=1.0, color=IRColor(0.0, 0.0, 1.0, 0.8)),
                            ],
                        ),
                        IRFill(kind="solid", color=IRColor(0.1, 0.2, 0.3, 0.4), opacity=0.9),
                    ],
                    borders=[IRBorder(color=IRColor(0, 0, 0, 1), weight=2.0, align="inside", style="solid")],
                    shadows=[IRShadow(kind="drop", color=IRColor(0, 0, 0, 0.25), x=4.0, y=8.0, blur=12.0, spread=2.0)],
                    blurs=[IRBlur(kind="layer", radius=4.0)],
                    radius=8.0,
                    corner_radii=[8.0, 0.0, 0.0, 8.0],
                    opacity=0.75,
                ),
                children=[
                    IRNode(
                        id="1:2",
                        name="Title",
                        kind=KIND_TEXT,
                        node_type="TEXT",
                        source=IRSource(file_key="rich", node_id="1:2", node_type="TEXT", path=["0:1", "1:1", "1:2"]),
                        visible=True,
                        typography=IRTypography(
                            font_family="Inter",
                            font_postscript_name="Inter-Regular",
                            font_weight=600.0,
                            font_size=32.0,
                            line_height=40.0,
                            letter_spacing=0.5,
                            text_case="UPPER",
                            text_decoration="underline",
                            text_align="left",
                            vertical_align="top",
                            auto_resize="HEIGHT",
                            token_refs=["var:1"],
                        ),
                        text=IRTextContent(
                            characters="Hello",
                            hyperlink=IRLink(kind="url", url="https://example.com"),
                        ),
                        instance=IRInstance(
                            component_id="2:1",
                            component_key="abc",
                            main_component_id="2:1",
                            main_component_key="abc",
                        ),
                        tokens=IRTokens(
                            refs=[IRTokenRef(property_name="fontSize", token_key="var:1", kind="variable")],
                            bound_variables={"fontSize": "var:1"},
                            style_refs={"fills": "style:1"},
                        ),
                        responsive=IResponsive(
                            constraints_horizontal="CENTER",
                            constraints_vertical="MIN",
                            sizing_horizontal="FILL",
                            sizing_vertical="FIXED",
                            min_width=100.0,
                            max_width=400.0,
                        ),
                        prototype=IRPrototype(
                            url="https://example.com/proto",
                            links=[IRLink(kind="node", node_id="1:1")],
                            interactions=[IRInteraction(trigger="ON_CLICK", action="NAVIGATE", destination="1:1")],
                            start_node="1:1",
                        ),
                        annotations=IRAnnotations(annotation="dev note", developer_metadata={"key": "value"}),
                        asset=IRAssetRef(node_id="1:2", url="https://example.com/img.png", image_ref="img:1"),
                        unknown={"custom": 1},
                        raw={"type": "TEXT", "id": "1:2"},
                    ),
                ],
            )
        ],
    )
    return IRDocument(
        schema_version=1,
        file_key="rich",
        name="Rich",
        source=IRSource(file_key="rich", node_id="0:1", node_type="DOCUMENT"),
        root=page,
        pages=[page],
        components={"comp:1": _component("comp:1")},
        component_sets={"set:1": _component("set:1", kind="component_set")},
        styles={"style:1": _token("style:1", kind="style", token_type="FILL")},
        variables={"var:1": _token("var:1", kind="variable", token_type="FLOAT")},
        assets={"1:2": "https://example.com/img.png"},
        prototype_start_node="1:1",
        unknown={"unknown_top": True},
        raw={"name": "Rich", "document": {}},
    )


def _component(key: str, kind: str = "component") -> object:
    from core.ir_types import IRComponent

    return IRComponent(
        key=key,
        node_id=f"{key}:node",
        name=key,
        kind=kind,
        description="desc",
        documentation_links=[IRLink(kind="url", url="https://example.com")],
    )


def _token(key: str, kind: str, token_type: str) -> object:
    from core.ir_types import IRToken

    return IRToken(
        kind=kind,
        key=key,
        name=key,
        token_type=token_type,
        value=1.0 if token_type == "FLOAT" else {"r": 1, "g": 0, "b": 0},
        description="token desc",
        resolved_type="FLOAT" if token_type == "FLOAT" else "FILL",
    )


class TestIRRoundTrip(unittest.TestCase):
    def _assert_roundtrips(self, doc: IRDocument) -> None:
        original = doc.to_dict()
        reloaded = IRDocument.from_dict(original)
        self.assertEqual(reloaded.to_dict(), original)

    def test_ir_roundtrip_desktop(self):
        self._assert_roundtrips(build_doc("layout_desktop", "lay1440"))

    def test_ir_roundtrip_variants(self):
        self._assert_roundtrips(build_doc("variants", "variants"))

    def test_ir_roundtrip_mobile(self):
        self._assert_roundtrips(build_doc("layout_mobile", "mobile"))

    def test_ir_roundtrip_nested(self):
        self._assert_roundtrips(build_doc("layout_nested", "nested"))

    def test_ir_roundtrip_rich(self):
        self._assert_roundtrips(_rich_document())

    def test_ir_roundtrip_empty_document(self):
        self._assert_roundtrips(IRDocument(schema_version=1, file_key="", name=""))


if __name__ == "__main__":
    unittest.main()
