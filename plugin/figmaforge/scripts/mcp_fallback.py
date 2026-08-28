#!/usr/bin/env python3
"""Create a FigmaForge ingest fixture from Figma MCP design context.

This is deliberately a source adapter, not a second generator.  MCP supplies
the node geometry and exported asset URLs when the Figma REST API is
unavailable; the resulting payload is the same raw-file shape consumed by
``pipeline.py ingest --file``.  Every later FigmaForge stage remains native.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


FILE_KEY = "VI77b2Mlzg2NSruFIZr6HA"
ASSETS = {
    # Node exports are used for the remote fallback.  They preserve the
    # exact crop/compositing of the selected Figma layer instead of relying on
    # positional raw-image ordering from the whole-file download.
    "11:89": "https://www.figma.com/api/mcp/asset/e1c35188-ea8b-43a1-9d37-a5aea77cf8f1.png",
    "11:90": "https://www.figma.com/api/mcp/asset/3dafe2ad-bdd9-4b0e-abdb-debbfbb7e30a.png",
    "11:91": "https://www.figma.com/api/mcp/asset/53a4d989-ce5f-4734-baac-afb8d9555de8.png",
    "106:135": "https://www.figma.com/api/mcp/asset/3452401e-036f-41b4-836c-2aca8b487731.png",
    "106:162": "https://www.figma.com/api/mcp/asset/651f12b6-0f19-456e-b9a0-558699356925.png",
    "106:149": "https://www.figma.com/api/mcp/asset/94afbbf6-ca09-4d89-aa5c-7781e1787d70.png",
    "1:73": "https://www.figma.com/api/mcp/asset/c77caa0f-d8a1-4955-a7a9-576616908be7.svg",
    "1:83": "https://www.figma.com/api/mcp/asset/5049ea80-535d-481d-886c-6e07849965f4.svg",
    "1:14": "https://www.figma.com/api/mcp/asset/5049ea80-535d-481d-886c-6e07849965f4.svg",
}


def color(r: float, g: float, b: float, a: float = 1.0) -> Dict[str, float]:
    return {"r": r, "g": g, "b": b, "a": a}


def solid(c: Dict[str, float], opacity: float = 1.0) -> Dict[str, Any]:
    return {"type": "SOLID", "visible": True, "opacity": opacity, "color": c}


def linear_gradient(
    stops: List[Dict[str, Any]],
    handles: List[Dict[str, float]] | None = None,
) -> Dict[str, Any]:
    """Represent the linear overlays used by the exported MNTN frame."""
    return {
        "type": "GRADIENT_LINEAR", "visible": True, "opacity": 1.0,
        "gradientStops": stops,
        "gradientHandlePositions": handles or [
            {"x": 0.0, "y": 0.0},
            {"x": 0.0, "y": 1.0},
            {"x": 0.0, "y": 0.0},
        ],
    }


def image_fill(
    node_id: str,
    css_background_size: str | None = None,
    css_background_position: str | None = None,
) -> Dict[str, Any]:
    image_transform: Dict[str, Any] = {}
    if css_background_size:
        image_transform["cssBackgroundSize"] = css_background_size
    if css_background_position:
        image_transform["cssBackgroundPosition"] = css_background_position
    return {
        "type": "IMAGE", "visible": True, "opacity": 1.0,
        "imageRef": node_id, "scaleMode": "FILL",
        "imageTransform": image_transform,
    }


def text(node_id: str, name: str, characters: str, box: List[float], size: float = 18,
         family: str = "Gilroy", weight: int = 700, color_value: Dict[str, float] | None = None,
         letter_spacing: float = 0) -> Dict[str, Any]:
    return {
        "id": node_id, "name": name, "type": "TEXT", "visible": True,
        "absoluteBoundingBox": {"x": box[0], "y": box[1], "width": box[2], "height": box[3]},
        "characters": characters,
        "style": {"fontFamily": family, "fontWeight": weight, "fontSize": size,
                  "lineHeightPx": size * 1.25, "letterSpacing": letter_spacing},
        "fills": [solid(color_value or color(1, 1, 1))],
    }


def frame(node_id: str, name: str, box: List[float], children: List[Dict[str, Any]] | None = None,
          fills: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    return {
        "id": node_id, "name": name, "type": "FRAME", "visible": True,
        "absoluteBoundingBox": {"x": box[0], "y": box[1], "width": box[2], "height": box[3]},
        "fills": fills or [], "children": children or [],
    }


def image(
    node_id: str,
    name: str,
    box: List[float],
    css_background_size: str | None = None,
    css_background_position: str | None = None,
) -> Dict[str, Any]:
    return frame(
        node_id,
        name,
        box,
        fills=[image_fill(node_id, css_background_size, css_background_position)],
    )


LOCAL_ASSET_NAMES = {
    "11:89": "raw_image_10.png",
    "11:90": "raw_image_1.png",
    "11:91": "raw_image_3.png",
    "106:135": "raw_image_2.png",
    "106:162": "raw_image_8.jpeg",
    "106:149": "raw_image_12.png",
    "1:73": "instagram.svg",
    "1:83": "twitter.svg",
    "1:14": "arrow.svg",
}


def build(asset_dir: Path | None = None) -> Dict[str, Any]:
    gold = color(0.984, 0.843, 0.518)
    ink = color(0.043, 0.114, 0.149)
    muted = color(0.36, 0.47, 0.51)
    line = color(0.65, 0.72, 0.74)
    hero = [
        # Figma paints the base behind the image layers and then applies a
        # translucent diagonal ink gradient over the hero image.
        frame("11:94-base", "BG Hero base", [0, 0, 1920, 1200], fills=[solid(ink)]),
        image("11:89", "HG", [0, -400, 1920, 1513]),
        frame("11:94", "BG Hero", [0, 0, 1920, 1200], fills=[linear_gradient([
            # Exact SVG source: x1=1115.5,y1=1037.5 -> x2=650,y2=-265
            # in the global 2120x4800 export, normalized to this 1920x1200
            # rect at (100,100).
            {"position": 0.0, "color": color(ink["r"], ink["g"], ink["b"], 0.0)},
            {"position": 1.0, "color": color(ink["r"], ink["g"], ink["b"], 1.0)},
        ], handles=[
            {"x": 1015.5 / 1920, "y": 937.5 / 1200},
            {"x": 550.0 / 1920, "y": -365.0 / 1200},
            {"x": 0.0, "y": 0.0},
        ])]),
        frame("1:11", "Hero rule", [500, 297, 72, 2], fills=[solid(gold)]),
        text("1:12", "A HIKING GUIDE", "A HIKING GUIDE", [604, 287, 212, 22], 18, "Gilroy", 800, gold, 5),
        text("1:13", "Hero title", "Be Prepared For The Mountains And Beyond!", [500, 341, 950, 200], 88, "Chronicle Display", 600),
        text("1:14-label", "Scroll down", "scroll down", [604, 670, 92, 24], 14, "Gilroy", 700),
        image("1:14", "Scroll arrow", [712, 666, 16, 24]),
        image("11:90", "MG", [0, 464, 1920, 1422]),
        image("11:91", "VG", [0, 768, 1920, 926]),
    ]
    header = [
        text("1:67", "Logo M", "M", [80, 58, 32, 38], 28, "Chronicle Display", 600),
        text("1:68", "Logo N", "N", [112, 58, 26, 38], 28, "Chronicle Display", 600),
        text("1:69", "Logo T", "T", [138, 58, 24, 38], 28, "Chronicle Display", 600),
        text("1:70", "Logo N", "N", [162, 58, 26, 38], 28, "Chronicle Display", 600),
        text("1:22", "Equipment", "Equipment", [819, 66, 90, 22], 12, "Gilroy", 700),
        text("1:21", "About us", "About us", [949, 66, 75, 22], 12, "Gilroy", 700),
        text("1:20", "Blog", "Blog", [1064, 66, 38, 22], 12, "Gilroy", 700),
        text("1:49", "Account", "Account", [1774, 67, 66, 21], 12, "Gilroy", 700),
    ]
    social = [
        text("1:85", "Follow us", "Follow us", [80, 359, 22, 77], 12, "Gilroy", 700),
        image("1:73", "Instagram", [80, 460, 24, 24]),
        image("1:83", "Twitter", [80, 508, 24, 24]),
    ]
    slider = [
        text("1:118", "Start", "Start", [1763, 342, 42, 22], 12, "Gilroy", 700),
        text("1:114", "01", "01", [1787, 404, 18, 22], 12, "Gilroy", 700),
        text("1:117", "02", "02", [1784, 466, 21, 22], 12, "Gilroy", 700),
        text("1:116", "03", "03", [1784, 528, 21, 22], 12, "Gilroy", 700),
        frame("1:122", "Slider line", [1837, 326, 3, 240], fills=[solid(line)]),
    ]
    sections = [
        frame("1:9", "BG Content", [0, 1200, 1920, 3400], fills=[solid(ink)]),
        frame("1:9-gradient", "BG Content fade", [0, 1300, 1920, 700], fills=[linear_gradient([
            {"position": 0.0, "color": color(ink["r"], ink["g"], ink["b"], 0.0)},
            {"position": 0.722823, "color": ink},
        ], handles=[
            {"x": 1060 / 1920, "y": 0.0},
            {"x": 1060 / 1920, "y": 594.46 / 700},
            {"x": 0.0, "y": 0.0},
        ])]),
        frame("106:132", "Content 01", [229, 1440, 1462, 720], [
            image("106:135", "Hiker image", [1125, 1440, 566, 720], "100% 117.918%", "center 112.1%"),
            text("106:131", "Section number", "01", [229, 1478, 180, 240], 160, "Gilroy", 700, muted),
            frame("106:130", "Section rule", [389, 1560, 72, 2], fills=[solid(gold)]),
            text("106:130a", "Section label", "GET STARTED", [485, 1551, 130, 22], 12, "Gilroy", 800, gold, 4),
            text("106:133", "Section title", "What level of hiker are you?", [379, 1581, 555, 154], 64, "Chronicle Display", 600),
            text("106:134", "Section body", "Determining what level of hiker you are can be an important tool when planning future hikes. This hiking level guide will help you plan hikes according to different hiking ratings set by various websites like All Trails and Modern Hiker. What type of hiker are you – novice, moderate, advanced moderate, expert, or expert backpacker?", [379, 1762, 632, 160]),
            text("106:136", "Read more", "read more   →", [379, 1960, 127, 22], 12, "Gilroy", 700, gold),
        ]),
        frame("106:160", "Content 02", [229, 2360, 1462, 720], [
            image("106:162", "Hiker image", [229, 2360, 566, 720], "190.788% 100%", "28.3% center"),
            text("106:161", "Section number", "02", [909, 2398, 180, 240], 160, "Gilroy", 700, muted),
            frame("106:166", "Section rule", [1069, 2480, 72, 2], fills=[solid(gold)]),
            text("106:165", "Section label", "HIKING ESSENTIALS", [1165, 2471, 258, 22], 12, "Gilroy", 800, gold, 4),
            text("106:167", "Section title", "Picking the right Hiking Gear!", [1059, 2509, 555, 154], 64, "Chronicle Display", 600),
            text("106:168", "Section body", "The nice thing about beginning hiking is that you don’t really need any special gear, you can probably get away with things you already have. Let’s start with basic hiking. A typical mistake hiking beginners make is wearing jeans and regular clothes, which will get heavy and chafe if they get sweaty or wet.", [1059, 2690, 632, 160]),
            text("106:172", "Read more", "read more   →", [1059, 2880, 127, 22], 12, "Gilroy", 700, gold),
        ]),
        frame("106:146", "Content 03", [229, 3280, 1462, 720], [
            image("106:149", "Compass image", [1125, 3280, 566, 720], "200.248% 100%", "25.0% center"),
            text("106:145", "Section number", "03", [229, 3318, 180, 240], 160, "Gilroy", 700, muted),
            frame("106:144", "Section rule", [389, 3400, 72, 2], fills=[solid(gold)]),
            text("106:143", "Section label", "WHERE YOU GO IS THE KEY", [485, 3391, 275, 22], 12, "Gilroy", 800, gold, 4),
            text("106:147", "Section title", "Understand Your Map & Timing", [379, 3421, 555, 154], 64, "Chronicle Display", 600),
            text("106:148", "Section body", "To start, print out the hiking guide and map. If it’s raining, throw them in a Zip-Lock bag. Read over the guide, study the map, and have a good idea of what to expect. I like to know what my next landmark is as I hike. For example, I’ll read the guide and know that, say, in a mile, I make a right turn at the junction.", [379, 3602, 632, 160]),
            text("106:150", "Read more", "read more   →", [379, 3790, 127, 22], 12, "Gilroy", 700, gold),
        ]),
        frame("11:135", "Footer", [229, 4200, 1462, 280], [
            text("11:116", "Footer logo", "MNTN", [229, 4200, 108, 38], 28, "Chronicle Display", 600),
            text("11:133", "Footer description", "Get out there & discover your next slope, mountain & destination!", [229, 4262, 303, 64], 14, "Gilroy", 700),
            text("11:121", "Blog links heading", "More on The Blog", [1125, 4200, 193, 32], 14, "Gilroy", 800, gold),
            text("11:122", "Blog link", "About MNTN", [1125, 4256, 104, 32], 12, "Gilroy", 700),
            text("11:123", "Blog link", "Contributors & Writers", [1125, 4304, 183, 32], 12, "Gilroy", 700),
            text("11:124", "Blog link", "Write For Us", [1125, 4352, 99, 32], 12, "Gilroy", 700),
            text("11:125", "Blog link", "Contact Us", [1125, 4400, 94, 32], 12, "Gilroy", 700),
            text("11:126", "Blog link", "Privacy Policy", [1125, 4448, 113, 32], 12, "Gilroy", 700),
            text("11:127", "MNTN links heading", "More on MNTN", [1530, 4200, 161, 32], 14, "Gilroy", 800, gold),
            text("11:128", "MNTN link", "The Team", [1530, 4256, 81, 32], 12, "Gilroy", 700),
            text("11:129", "MNTN link", "Jobs", [1530, 4304, 41, 32], 12, "Gilroy", 700),
            text("11:130", "MNTN link", "Press", [1530, 4352, 42, 32], 12, "Gilroy", 700),
            text("11:134", "Copyright", "Copyright 2023 MNTN, Inc. Terms & Privacy", [235, 4448, 350, 32], 12, "Gilroy", 700, muted),
        ]),
    ]
    document_children = [frame("1:2", "MNTN", [0, 0, 1920, 4600], hero + header + social + slider + sections)]
    asset_urls = dict(ASSETS)
    if asset_dir:
        for node_id, filename in LOCAL_ASSET_NAMES.items():
            path = (asset_dir / filename).resolve()
            if path.is_file():
                asset_urls[node_id] = path.as_uri()
    return {
        "name": "MNTN — MCP fallback fixture",
        "role": "owner", "editorType": "figma", "schemaVersion": 0,
        "file_key": FILE_KEY, "source_adapter": "figma_mcp",
        "document": {"id": "0:0", "name": "Document", "type": "DOCUMENT", "children": [
            {"id": "0:1", "name": "MNTN", "type": "CANVAS", "visible": True, "children": document_children}
        ]},
        "assets": asset_urls,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--asset-dir", type=Path, help="MCP-downloaded asset directory")
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(build(args.asset_dir), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "kind": "mcp_fallback", "file": str(args.out), "file_key": FILE_KEY}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
