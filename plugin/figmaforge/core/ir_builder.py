"""
Normalization from the ingestion layer into the Design IR.

Consumes the typed models from ``figma_types`` (``FigmaFile``/``Node``) —
which are themselves built from raw Figma API responses — and produces the
framework-neutral ``IRDocument`` tree from ``ir_types``.

This module is pure and deterministic: no I/O, no network, no credentials.
It reads only the already-normalized ingestion objects plus the ``raw`` dicts
they retain.

Two preservation guarantees are important:

- ``IRNode.raw`` — the complete original Figma node dict (for debugging).
- ``IRNode.unknown`` — the subset of raw keys this normalizer did not map.
  These are surfaced via ``unsupported_properties()`` so nothing is silently
  dropped and callers can report unsupported Figma properties clearly.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .figma_types import (
    FigmaFile,
    Node,
    Paint,
    Effect,
    Color,
    StyleRef,
)
from .ir_types import (
    IRAnnotations,
    IRAssetRef,
    IRBlur,
    IRBorder,
    IRColor,
    IRComponent,
    IRDimensions,
    IRDocument,
    IRFill,
    IRGradientStop,
    IRInstance,
    IRLayout,
    IRLink,
    IRNode,
    IRPosition,
    IRPrototype,
    IResponsive,
    IRShadow,
    IRSource,
    IRSpacing,
    IRStyle,
    IRTextContent,
    IRToken,
    IRTokenRef,
    IRTokens,
    IRTypography,
    IRInteraction,
    kind_for,
)

logger = logging.getLogger("figmaforge.ir_builder")

# Raw node keys that the normalizer maps into typed IR fields. Any key present
# in a raw node dict but absent from this set is preserved under
# ``IRNode.unknown`` and reported by ``unsupported_properties()``.
CONSUMED_NODE_KEYS = frozenset({
    "id", "name", "type", "visible", "opacity", "children",
    "absoluteBoundingBox", "constraints", "fills", "strokes", "effects",
    # auto-layout (also read by figma_types.AutoLayout)
    "layoutMode", "layoutSizingHorizontal", "layoutSizingVertical",
    "primaryAxisAlignItems", "counterAxisAlignItems",
    "primaryAxisSizingMode", "counterAxisSizingMode",
    "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
    "itemSpacing", "layoutWrap", "layoutGrow", "layoutShrink",
    "layoutAlign", "layoutGrids",
    # dimensions / constraints
    "minWidth", "maxWidth", "minHeight", "maxHeight",
    # style
    "cornerRadius", "rectangleCornerRadii", "strokeWeight", "strokeAlign",
    "strokeStyle",
    # text
    "characters", "style", "textAutoResize", "textAlignHorizontal",
    "textAlignVertical",
    # components / instances
    "componentId", "mainComponent",
    # tokens / annotations / links
    "boundVariables", "styles", "annotation", "url", "interactions",
})

# Raw file-level keys mapped into IRDocument fields. Everything else present in
# the raw file dict is preserved under ``IRDocument.unknown``.
CONSUMED_FILE_KEYS = frozenset({
    "name", "role", "editorType", "lastModified", "thumbnailUrl",
    "version", "schemaVersion", "document",
    "components", "componentSets", "styles", "variables",
    "prototypeStartNode", "interactions",
})

# Direction mapping: Figma layout mode -> normalized IR direction.
_DIRECTION_BY_LAYOUT_MODE = {"HORIZONTAL": "row", "VERTICAL": "column"}

# Fill kinds: Figma Paint type -> normalized IR fill kind.
_FILL_KIND_BY_TYPE = {
    "SOLID": "solid",
    "IMAGE": "image",
    "VIDEO": "image",
    "GRADIENT_LINEAR": "gradient",
    "GRADIENT_RADIAL": "gradient",
    "GRADIENT_ANGULAR": "gradient",
    "GRADIENT_DIAMOND": "gradient",
}

# Typography properties that can carry bound variables.
_TYPOGRAPHY_TOKEN_PROPERTIES = (
    "fontSize", "fontFamily", "fontWeight", "lineHeight", "letterSpacing",
)


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ir_color(color: Optional[Color]) -> Optional[IRColor]:
    if color is None:
        return None
    return IRColor(r=color.r, g=color.g, b=color.b, a=color.a)


def _link_from_url(url: Optional[str], kind: str = "url") -> Optional[IRLink]:
    if not url:
        return None
    return IRLink(kind=kind, url=url)


class IRBuilder:
    """Build an :class:`IRDocument` from a :class:`FigmaFile`.

    ``images`` is an optional ``{node_id: url}`` mapping (e.g. from the
    ``/v1/images`` endpoint via ``ImageSet``) used to attach asset references.
    """

    def __init__(self, images: Optional[Dict[str, str]] = None):
        self._images = dict(images or {})
        self._file_key = ""
        self._unknown_props: Dict[str, List[str]] = {}

    # ------------------------------------------------------------------ API
    def build(self, figma_file: FigmaFile) -> IRDocument:
        """Normalize a :class:`FigmaFile` into an :class:`IRDocument`."""
        self._file_key = figma_file.file_key
        self._unknown_props = {}

        root = None
        pages: List[IRNode] = []
        # node_id -> component key, for resolving COMPONENT/COMPONENT_SET nodes
        # to their file-level component-map entries.
        component_lookup: Dict[str, str] = {}
        for key, ref in list(figma_file.components.items()) + list(figma_file.component_sets.items()):
            if ref.node_id:
                component_lookup[ref.node_id] = key

        if figma_file.document is not None:
            root = self._build_node(
                figma_file.document, path=(), in_auto_layout=False,
                styles_map=figma_file.styles,
                component_lookup=component_lookup,
            )
            pages = [child for child in root.children if child.is_page]

        return IRDocument(
            schema_version=1,
            file_key=figma_file.file_key,
            name=figma_file.name,
            source=IRSource(
                file_key=figma_file.file_key,
                node_id=figma_file.document.id if figma_file.document else "0:0",
                node_type="DOCUMENT",
                path=[],
            ),
            root=root,
            pages=pages,
            components={
                key: self._component_from_ref(key, ref)
                for key, ref in figma_file.components.items()
            },
            component_sets={
                key: self._component_from_ref(key, ref)
                for key, ref in figma_file.component_sets.items()
            },
            styles={
                key: self._style_token(key, ref)
                for key, ref in figma_file.styles.items()
            },
            variables=self._variable_tokens(figma_file.variables),
            assets=dict(self._images),
            prototype_start_node=figma_file.raw.get("prototypeStartNode"),
            unknown=self._unknown_keys(figma_file.raw, CONSUMED_FILE_KEYS),
            raw=dict(figma_file.raw),
        )

    def unsupported_properties(self) -> Dict[str, List[str]]:
        """Map of ``node_id -> [unmapped raw property keys, ...]``.

        Keys listed here are properties present in the source Figma payload
        that this normalizer does not map into typed IR fields. They are still
        preserved verbatim under ``IRNode.unknown``.
        """
        return {node_id: list(keys) for node_id, keys in sorted(self._unknown_props.items())}

    # ------------------------------------------------------------- building
    def _build_node(
        self,
        node: Node,
        path: Tuple[str, ...],
        in_auto_layout: bool,
        styles_map: Dict[str, StyleRef],
        component_lookup: Optional[Dict[str, str]] = None,
    ) -> IRNode:
        raw = node.raw if isinstance(node.raw, dict) else {}
        unknown = self._unknown_keys(raw, CONSUMED_NODE_KEYS)
        if unknown:
            self._unknown_props.setdefault(node.id, []).extend(sorted(unknown))

        auto = node.auto_layout
        layout = self._build_layout(node, auto)
        parent_auto_layout = bool(auto and auto.layout_mode != "NONE")

        ir = IRNode(
            id=node.id,
            name=node.name,
            kind=kind_for(node.type),
            node_type=node.type,
            source=IRSource(
                file_key=self._file_key,
                node_id=node.id,
                node_type=node.type,
                path=list(path),
            ),
            visible=node.visible,
            opacity=node.opacity,
            dimensions=self._build_dimensions(node, auto),
            position=self._build_position(node, in_auto_layout),
            layout=layout,
            style=self._build_style(node),
            typography=self._build_typography(node),
            text=self._build_text(node),
            component=self._build_component(node, component_lookup),
            instance=self._build_instance(node),
            tokens=self._build_tokens(node, styles_map),
            responsive=self._build_responsive(node, auto),
            prototype=self._build_prototype(node),
            annotations=self._build_annotations(node),
            asset=self._build_asset(node),
            unknown=dict(unknown),
            raw=dict(raw),
        )
        ir.children = [
            self._build_node(
                child, path + (node.id,), parent_auto_layout,
                styles_map, component_lookup,
            )
            for child in node.children
        ]
        return ir

    # ------------------------------------------------------------- sub-builders
    def _build_layout(self, node: Node, auto: Any) -> Optional[IRLayout]:
        raw = node.raw if isinstance(node.raw, dict) else {}
        if auto is None:
            return None

        is_auto = auto.layout_mode != "NONE"
        mode = "auto" if is_auto else "none"

        grid_columns = None
        grids = raw.get("layoutGrids")
        if isinstance(grids, list):
            for grid in grids:
                if isinstance(grid, dict) and grid.get("pattern") == "COLUMNS":
                    grid_columns = {
                        "count": grid.get("count"),
                        "gutter": _as_float(grid.get("gutterSize")),
                    }
                    if not is_auto:
                        mode = "grid"

        padding = None
        if any((auto.padding_top, auto.padding_right, auto.padding_bottom, auto.padding_left)):
            padding = IRSpacing(
                top=auto.padding_top, right=auto.padding_right,
                bottom=auto.padding_bottom, left=auto.padding_left,
            )

        return IRLayout(
            mode=mode,
            direction=_DIRECTION_BY_LAYOUT_MODE.get(auto.layout_mode),
            justify=auto.primary_axis_align_items,
            align=auto.counter_axis_align_items,
            padding=padding,
            gap=auto.item_spacing if is_auto else None,
            wrap=raw.get("layoutWrap"),
            grow=_as_float(raw.get("layoutGrow")),
            shrink=_as_float(raw.get("layoutShrink")),
            align_self=raw.get("layoutAlign"),
            sizing_primary=raw.get("primaryAxisSizingMode"),
            sizing_counter=raw.get("counterAxisSizingMode"),
            grid_columns=grid_columns,
        )

    def _build_position(self, node: Node, in_auto_layout: bool) -> Optional[IRPosition]:
        box = node.absolute_bounding_box
        mode = "auto" if in_auto_layout else "absolute"
        position = IRPosition(mode=mode)
        if box is not None:
            position.x = box.x
            position.y = box.y
            if mode != "auto":
                position.left = box.x
                position.top = box.y
        return position

    def _build_dimensions(self, node: Node, auto: Any) -> Optional[IRDimensions]:
        raw = node.raw if isinstance(node.raw, dict) else {}
        box = node.absolute_bounding_box
        return IRDimensions(
            width=box.width if box else None,
            height=box.height if box else None,
            min_width=_as_float(raw.get("minWidth")),
            max_width=_as_float(raw.get("maxWidth")),
            min_height=_as_float(raw.get("minHeight")),
            max_height=_as_float(raw.get("maxHeight")),
            sizing_horizontal=auto.layout_sizing_horizontal if auto else None,
            sizing_vertical=auto.layout_sizing_vertical if auto else None,
        )

    def _build_style(self, node: Node) -> Optional[IRStyle]:
        raw = node.raw if isinstance(node.raw, dict) else {}
        style = IRStyle(opacity=node.opacity)
        style.radius = _as_float(raw.get("cornerRadius"))
        radii = raw.get("rectangleCornerRadii")
        if isinstance(radii, list) and len(radii) == 4:
            style.corner_radii = [_as_float(v) for v in radii]
        style.fills = [self._build_fill(p) for p in node.fills]
        style.borders = [self._build_border(p, raw) for p in node.strokes]
        for effect in node.effects:
            shadow = self._effect_to_shadow(effect)
            if shadow is not None:
                style.shadows.append(shadow)
                continue
            blur = self._effect_to_blur(effect)
            if blur is not None:
                style.blurs.append(blur)
        return style

    def _build_fill(self, paint: Paint) -> IRFill:
        kind = _FILL_KIND_BY_TYPE.get(paint.type, "none")
        fill = IRFill(
            kind=kind,
            color=_ir_color(paint.color),
            opacity=paint.opacity,
            visible=paint.visible,
            image_ref=paint.image_ref,
            scale_mode=paint.scale_mode,
            image_transform=paint.image_transform,
            blend_mode=paint.blend_mode,
        )
        if kind == "gradient":
            fill.gradient_stops = [
                IRGradientStop(position=stop.position, color=_ir_color(stop.color))
                for stop in paint.gradient_stops
                if stop.color is not None
            ]
            fill.gradient_handles = [
                {"x": float(handle.get("x", 0.0)), "y": float(handle.get("y", 0.0))}
                for handle in paint.gradient_handles
                if isinstance(handle, dict)
                and isinstance(handle.get("x"), (int, float))
                and isinstance(handle.get("y"), (int, float))
            ]
        return fill

    def _build_border(self, paint: Paint, raw: Dict[str, Any]) -> IRBorder:
        return IRBorder(
            color=_ir_color(paint.color),
            weight=_as_float(raw.get("strokeWeight")),
            visible=paint.visible,
            align=raw.get("strokeAlign"),
            style=raw.get("strokeStyle"),
        )

    @staticmethod
    def _effect_to_shadow(effect: Effect) -> Optional[IRShadow]:
        if effect.type not in ("DROP_SHADOW", "INNER_SHADOW"):
            return None
        kind = "drop" if effect.type == "DROP_SHADOW" else "inner"
        offset = effect.offset if isinstance(effect.offset, dict) else {}
        return IRShadow(
            kind=kind,
            color=_ir_color(effect.color),
            x=_as_float(offset.get("x")) or 0.0,
            y=_as_float(offset.get("y")) or 0.0,
            blur=effect.radius,
            spread=effect.spread,
            visible=effect.visible,
        )

    @staticmethod
    def _effect_to_blur(effect: Effect) -> Optional[IRBlur]:
        kind = {"LAYER_BLUR": "layer", "BACKGROUND_BLUR": "background"}.get(effect.type)
        if kind is None:
            return None
        return IRBlur(kind=kind, radius=effect.radius, visible=effect.visible)

    def _build_typography(self, node: Node) -> Optional[IRTypography]:
        ts = node.text_style
        if ts is None and not node.bound_variables:
            return None
        raw = node.raw if isinstance(node.raw, dict) else {}
        typography = IRTypography(
            font_family=ts.font_family if ts else None,
            font_postscript_name=ts.font_post_script_name if ts else None,
            font_weight=ts.font_weight if ts else None,
            font_size=ts.font_size if ts else None,
            line_height=ts.line_height_px if ts else None,
            letter_spacing=ts.letter_spacing if ts else None,
            text_case=ts.text_case if ts else None,
            text_decoration=ts.text_decoration if ts else None,
            text_align=raw.get("textAlignHorizontal"),
            vertical_align=raw.get("textAlignVertical"),
            auto_resize=raw.get("textAutoResize"),
        )
        for prop in _TYPOGRAPHY_TOKEN_PROPERTIES:
            ref = node.bound_variables.get(prop)
            if isinstance(ref, dict) and ref.get("id"):
                typography.token_refs.append(ref["id"])
        return typography

    def _build_text(self, node: Node) -> Optional[IRTextContent]:
        if node.characters is None:
            return None
        hyperlink = None
        if node.text_style is not None and node.text_style.hyperlink is not None:
            h = node.text_style.hyperlink
            hyperlink = IRLink(
                kind="file" if h.type == "FILE" else "url",
                url=h.url,
            )
        return IRTextContent(characters=node.characters, hyperlink=hyperlink)

    def _build_component(self, node: Node, component_lookup: Optional[Dict[str, str]] = None) -> Optional[IRComponent]:
        if node.type not in ("COMPONENT", "COMPONENT_SET"):
            return None
        key = (component_lookup or {}).get(node.id)
        return IRComponent(
            key=key,
            name=node.name,
            kind="component_set" if node.type == "COMPONENT_SET" else "component",
            node_id=node.id,
        )

    def _build_instance(self, node: Node) -> Optional[IRInstance]:
        if node.type != "INSTANCE":
            return None
        main = node.main_component if isinstance(node.main_component, dict) else {}
        return IRInstance(
            component_id=node.component_id,
            main_component_id=main.get("id"),
            main_component_key=main.get("key"),
        )

    def _build_tokens(self, node: Node, styles_map: Dict[str, StyleRef]) -> Optional[IRTokens]:
        raw = node.raw if isinstance(node.raw, dict) else {}
        refs: List[IRTokenRef] = []
        bound: Dict[str, str] = {}
        for prop, ref in (node.bound_variables or {}).items():
            if isinstance(ref, dict) and ref.get("id"):
                bound[prop] = ref["id"]
                refs.append(IRTokenRef(property_name=prop, token_key=ref["id"], kind="variable"))
        style_refs: Dict[str, str] = {}
        for prop, key in (raw.get("styles") or {}).items():
            if isinstance(key, str):
                style_refs[prop] = key
                refs.append(IRTokenRef(property_name=prop, token_key=key, kind="style"))
        if not refs and not bound and not style_refs:
            return None
        return IRTokens(refs=refs, bound_variables=bound, style_refs=style_refs)

    def _build_responsive(self, node: Node, auto: Any) -> Optional[IResponsive]:
        if node.constraints is None and auto is None:
            return None
        raw = node.raw if isinstance(node.raw, dict) else {}
        return IResponsive(
            constraints_horizontal=node.constraints.horizontal if node.constraints else None,
            constraints_vertical=node.constraints.vertical if node.constraints else None,
            sizing_horizontal=auto.layout_sizing_horizontal if auto else None,
            sizing_vertical=auto.layout_sizing_vertical if auto else None,
            min_width=_as_float(raw.get("minWidth")),
            max_width=_as_float(raw.get("maxWidth")),
            min_height=_as_float(raw.get("minHeight")),
            max_height=_as_float(raw.get("maxHeight")),
        )

    def _build_prototype(self, node: Node) -> Optional[IRPrototype]:
        prototype = IRPrototype(url=node.url)
        if node.text_style is not None and node.text_style.hyperlink is not None:
            h = node.text_style.hyperlink
            prototype.links.append(IRLink(
                kind="file" if h.type == "FILE" else "url", url=h.url,
            ))
        raw = node.raw if isinstance(node.raw, dict) else {}
        interactions = raw.get("interactions")
        if isinstance(interactions, list):
            for interaction in interactions:
                if isinstance(interaction, dict):
                    prototype.interactions.append(self._build_interaction(interaction))
        if not prototype.url and not prototype.links and not prototype.interactions:
            return None
        return prototype

    @staticmethod
    def _build_interaction(data: Dict[str, Any]) -> IRInteraction:
        return IRInteraction(
            trigger=data.get("trigger"),
            action=data.get("action"),
            destination=data.get("destination"),
            transition=data.get("transition"),
        )

    def _build_annotations(self, node: Node) -> Optional[IRAnnotations]:
        raw = node.raw if isinstance(node.raw, dict) else {}
        developer_metadata = {}
        for key in ("devStatus", "devId", "figmaDevMode"):
            if key in raw:
                developer_metadata[key] = raw[key]
        if node.annotation is None and not developer_metadata:
            return None
        return IRAnnotations(annotation=node.annotation, developer_metadata=developer_metadata)

    def _build_asset(self, node: Node) -> Optional[IRAssetRef]:
        image_ref = None
        for paint in node.fills:
            if paint.image_ref:
                image_ref = paint.image_ref
                break
        url = self._images.get(node.id)
        if url is None and image_ref is None:
            return None
        return IRAssetRef(node_id=node.id, url=url, image_ref=image_ref)

    # ------------------------------------------------------- file-level maps
    @staticmethod
    def _component_from_ref(key: str, ref: Any) -> IRComponent:
        return IRComponent(
            key=key,
            node_id=ref.node_id,
            name=ref.name,
            kind="component_set" if ref.type == "COMPONENT_SET" else "component",
            description=ref.description,
            documentation_links=[
                IRLink(kind="url", url=link.url) for link in ref.documentation_links if link.url
            ],
        )

    @staticmethod
    def _style_token(key: str, ref: StyleRef) -> IRToken:
        return IRToken(
            kind="style",
            key=key,
            name=ref.name,
            token_type=ref.style_type,
            description=ref.description,
        )

    @staticmethod
    def _variable_tokens(raw_variables: Any) -> Dict[str, IRToken]:
        tokens: Dict[str, IRToken] = {}
        if not isinstance(raw_variables, dict):
            return tokens
        for key, data in raw_variables.items():
            if not isinstance(data, dict):
                continue
            tokens[key] = IRToken(
                kind="variable",
                key=key,
                name=str(data.get("name", key) or key),
                token_type=str(data.get("resolvedType", "") or ""),
                value=data.get("resolvedValue"),
                resolved_type=data.get("resolvedType"),
            )
        return tokens

    # ------------------------------------------------------------- unknowns
    @staticmethod
    def _unknown_keys(raw: Dict[str, Any], consumed: frozenset) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        return {key: raw[key] for key in sorted(raw.keys()) if key not in consumed}
