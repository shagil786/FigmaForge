"""Completeness checks for raw Figma file payloads.

The visual renderer must never be the first place where an incomplete Figma
source is discovered.  This module audits the structured payload before IR
normalization and reports missing assets, empty frames, unsupported node kinds,
fonts, and adapter warnings in a machine-readable form for an AI controller.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List


SUPPORTED_NODE_TYPES = {
    "DOCUMENT", "CANVAS", "FRAME", "GROUP", "SECTION", "COMPONENT",
    "COMPONENT_SET", "INSTANCE", "TEXT", "RECTANGLE", "ROUNDED_RECTANGLE",
    "ELLIPSE", "VECTOR", "BOOLEAN_OPERATION", "STAR", "LINE", "POLYGON",
    "SLICE", "TABLE", "TABLE_CELL", "STAMP", "WASHI_TAPE", "SHAPE_WITH_TEXT",
}


def _nodes(value: Any) -> Iterable[Dict[str, Any]]:
    if not isinstance(value, dict):
        return
    yield value
    for child in value.get("children") or []:
        yield from _nodes(child)


def audit_source(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deterministic completeness report for a raw Figma payload."""
    document = raw.get("document") or {}
    nodes = list(_nodes(document))
    asset_refs: set[str] = set()
    fonts: set[str] = set()
    types = Counter()
    empty_frames: List[str] = []
    for node in nodes:
        node_type = str(node.get("type") or "UNKNOWN")
        types[node_type] += 1
        if node_type in {"FRAME", "GROUP", "SECTION", "COMPONENT", "INSTANCE"} \
                and not node.get("children") and not node.get("fills") \
                and not node.get("characters"):
            empty_frames.append(str(node.get("id") or "<unknown>"))
        style = node.get("style") or {}
        if style.get("fontFamily"):
            fonts.add(str(style["fontFamily"]))
        for paint in node.get("fills") or []:
            if isinstance(paint, dict) and paint.get("imageRef"):
                asset_refs.add(str(paint["imageRef"]))

    assets = raw.get("assets") or {}
    resolved_assets = sorted(ref for ref in asset_refs if ref in assets)
    missing_assets = sorted(asset_refs - set(assets))
    unsupported_types = sorted(set(types) - SUPPORTED_NODE_TYPES)
    warnings: List[str] = []
    if raw.get("source_adapter") == "figma_mcp":
        warnings.append("source came from MCP adapter; verify recursive completeness")
    if not nodes:
        warnings.append("document contains no nodes")
    if empty_frames:
        warnings.append(f"{len(empty_frames)} empty structural nodes may indicate truncated children")
    if missing_assets:
        warnings.append(f"{len(missing_assets)} image fills have no asset mapping")
    if unsupported_types:
        warnings.append("unsupported node types require an explicit lowering strategy")

    return {
        "schema_version": 1,
        "ready_for_generation": bool(nodes) and not missing_assets and not unsupported_types,
        "node_count": len(nodes),
        "node_types": dict(sorted(types.items())),
        "asset_refs": sorted(asset_refs),
        "assets_expected": len(asset_refs),
        "assets_resolved": len(resolved_assets),
        "missing_assets": missing_assets,
        "fonts": sorted(fonts),
        "empty_structural_nodes": sorted(empty_frames),
        "unsupported_node_types": unsupported_types,
        "warnings": warnings,
    }

