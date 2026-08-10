"""
Typed Design Intermediate Representation (IR).

The IR is a framework-neutral, normalized view of a Figma file. It is produced
from the ingestion-layer types in ``figma_types`` (see ``ir_builder``) and is
explicitly *not* tied to any code-generation target — React/CSS/whatever may be
derived from it later, but nothing here renders.

Design goals, consistent with FigmaForge conventions:

- Standard library only; no external dependencies.
- Preserve original Figma node ids and parent-child structure.
- Preserve source-location metadata (file key, node id, ancestor path) for
  debugging and, later, for precise code-generation blame.
- Separate *semantic* properties (the normalized fields on each object) from
  *raw* Figma properties (``raw``) and from properties the normalizer did not
  map (``unknown``) — nothing is silently dropped.
- Normalize colors, dimensions, typography, and tokens into consistent internal
  shapes (e.g. colors are always ``IRColor`` with r/g/b/a).
- Every object is JSON-serializable via ``to_dict()``; ``ir_to_json`` renders a
  whole document deterministically (``sort_keys=True``) so snapshots are stable.

The 15 modeled areas map onto the following objects:

1. Documents and pages            ``IRDocument``, ``IRNode`` (kind "document"/"page")
2. Frames and sections            ``IRNode`` (kind "frame"/"group"/"section")
3. Text nodes                     ``IRNode`` (kind "text") + ``IRTextContent``
4. Components / instances         ``IRComponent``, ``IRInstance``
5. Auto-layout                    ``IRLayout`` (mode "auto")
6. Flex/grid/absolute             ``IRLayout`` (wrap/grow/shrink, grid_columns), ``IRPosition``
7. Width/height/min/max           ``IRDimensions``
8. Padding/gaps/alignment/spacing ``IRSpacing`` + ``IRLayout``
9. Fills/borders/shadows/opacity/radius  ``IRStyle`` (+ ``IRFill``/``IRBorder``/``IRShadow``/``IRBlur``)
10. Typography and text styles    ``IRTypography``
11. Variables and design tokens   ``IRToken``, ``IRTokenRef``, ``IRTokens``
12. Assets and image references   ``IRAssetRef`` + ``IRDocument.assets``
13. Responsive constraints        ``IResponsive``
14. Prototype links / interactions ``IRPrototype``, ``IRInteraction``, ``IRLink``
15. Annotations / dev metadata    ``IRAnnotations``

``IR_VERSION`` is bumped when the serialized shape changes incompatibly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Bump when the serialized IR shape changes incompatibly.
IR_VERSION = 1

# ---------------------------------------------------------------------------
# Node kinds (normalized, framework-neutral vocabulary)
# ---------------------------------------------------------------------------

KIND_DOCUMENT = "document"
KIND_PAGE = "page"
KIND_FRAME = "frame"
KIND_GROUP = "group"
KIND_SECTION = "section"
KIND_COMPONENT = "component"
KIND_COMPONENT_SET = "component_set"
KIND_INSTANCE = "instance"
KIND_TEXT = "text"
KIND_VECTOR = "vector"
KIND_SHAPE = "shape"
KIND_NODE = "node"  # generic fallback for unmapped Figma node types

# Figma REST ``type`` -> normalized ``kind``. Types absent from this map fall
# back to ``KIND_NODE`` with their original ``node_type`` preserved.
KIND_BY_TYPE: Dict[str, str] = {
    "DOCUMENT": KIND_DOCUMENT,
    "CANVAS": KIND_PAGE,
    "FRAME": KIND_FRAME,
    "GROUP": KIND_GROUP,
    "SECTION": KIND_SECTION,
    "COMPONENT": KIND_COMPONENT,
    "COMPONENT_SET": KIND_COMPONENT_SET,
    "INSTANCE": KIND_INSTANCE,
    "TEXT": KIND_TEXT,
    "VECTOR": KIND_VECTOR,
    "LINE": KIND_SHAPE,
    "ELLIPSE": KIND_SHAPE,
    "RECTANGLE": KIND_SHAPE,
    "POLYGON": KIND_SHAPE,
    "STAR": KIND_SHAPE,
    "BOOLEAN_OPERATION": KIND_SHAPE,
}


def kind_for(figma_type: str) -> str:
    """Map a raw Figma node ``type`` to a normalized IR ``kind``."""
    return KIND_BY_TYPE.get(figma_type, KIND_NODE)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


def _round(value: Optional[float], places: int = 4) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return None


def _compact(data: Dict[str, Any]) -> Dict[str, Any]:
    """Drop ``None`` values while keeping falsy-but-present ones (0, "", False)."""
    return {k: v for k, v in data.items() if v is not None}


@dataclass
class IRColor:
    """Normalized RGBA color (each channel in 0..1, matching Figma)."""

    r: float = 0.0
    g: float = 0.0
    b: float = 0.0
    a: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "r": _round(self.r) or 0.0,
            "g": _round(self.g) or 0.0,
            "b": _round(self.b) or 0.0,
            "a": _round(self.a) if self.a is not None else 1.0,
        }

    def to_hex(self) -> str:
        """8-digit hex form ``#rrggbbaa`` (with 0x in channel order)."""
        def _byte(value: float) -> int:
            return max(0, min(255, int(round((value if value is not None else 0.0) * 255))))
        return "#{:02x}{:02x}{:02x}{:02x}".format(
            _byte(self.r), _byte(self.g), _byte(self.b), _byte(self.a)
        )


@dataclass
class IRGradientStop:
    position: float = 0.0
    color: Optional[IRColor] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "position": _round(self.position) or 0.0,
            "color": self.color.to_dict() if self.color else None,
        })


@dataclass
class IRFill:
    """A normalized fill/background paint."""

    kind: str = "none"  # "solid" | "gradient" | "image" | "none"
    color: Optional[IRColor] = None
    opacity: float = 1.0
    visible: bool = True
    image_ref: Optional[str] = None
    scale_mode: Optional[str] = None
    blend_mode: Optional[str] = None
    gradient_stops: List[IRGradientStop] = field(default_factory=list)
    token_ref: Optional[str] = None  # bound-variable id, when one is bound

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "kind": self.kind,
            "color": self.color.to_dict() if self.color else None,
            "opacity": self.opacity,
            "visible": self.visible,
            "image_ref": self.image_ref,
            "scale_mode": self.scale_mode,
            "blend_mode": self.blend_mode,
            "gradient_stops": [s.to_dict() for s in self.gradient_stops],
            "token_ref": self.token_ref,
        })


@dataclass
class IRBorder:
    """A normalized stroke/border."""

    color: Optional[IRColor] = None
    weight: Optional[float] = None
    visible: bool = True
    align: Optional[str] = None  # strokeAlign
    style: Optional[str] = None  # stroke style (solid/dashed/...)
    token_ref: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "color": self.color.to_dict() if self.color else None,
            "weight": _round(self.weight),
            "visible": self.visible,
            "align": self.align,
            "style": self.style,
            "token_ref": self.token_ref,
        })


@dataclass
class IRShadow:
    kind: str = "drop"  # "drop" | "inner"
    color: Optional[IRColor] = None
    x: float = 0.0
    y: float = 0.0
    blur: float = 0.0
    spread: float = 0.0
    visible: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "kind": self.kind,
            "color": self.color.to_dict() if self.color else None,
            "x": self.x,
            "y": self.y,
            "blur": self.blur,
            "spread": self.spread,
            "visible": self.visible,
        })


@dataclass
class IRBlur:
    kind: str = "layer"  # "layer" | "background"
    radius: float = 0.0
    visible: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "kind": self.kind,
            "radius": self.radius,
            "visible": self.visible,
        })


@dataclass
class IRStyle:
    """Normalized visual style: fills, borders, shadows, blur, radius, opacity."""

    fills: List[IRFill] = field(default_factory=list)
    borders: List[IRBorder] = field(default_factory=list)
    shadows: List[IRShadow] = field(default_factory=list)
    blurs: List[IRBlur] = field(default_factory=list)
    radius: Optional[float] = None  # cornerRadius
    corner_radii: Optional[List[float]] = None  # rectangleCornerRadii, when given
    opacity: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "fills": [f.to_dict() for f in self.fills],
            "borders": [b.to_dict() for b in self.borders],
            "shadows": [s.to_dict() for s in self.shadows],
            "blurs": [b.to_dict() for b in self.blurs],
            "radius": _round(self.radius),
            "corner_radii": [_round(v) for v in self.corner_radii] if self.corner_radii else None,
            "opacity": self.opacity,
        })


@dataclass
class IRSpacing:
    top: Optional[float] = None
    right: Optional[float] = None
    bottom: Optional[float] = None
    left: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "top": _round(self.top),
            "right": _round(self.right),
            "bottom": _round(self.bottom),
            "left": _round(self.left),
        })


@dataclass
class IRLayout:
    """Normalized layout: auto-layout / flex / grid / none + spacing alignment."""

    mode: str = "none"  # "none" | "auto" | "grid"
    direction: Optional[str] = None  # "row" (HORIZONTAL) | "column" (VERTICAL)
    justify: Optional[str] = None  # main-axis alignment (primaryAxisAlignItems)
    align: Optional[str] = None  # cross-axis alignment (counterAxisAlignItems)
    padding: Optional[IRSpacing] = None
    gap: Optional[float] = None  # itemSpacing
    wrap: Optional[str] = None  # layoutWrap
    grow: Optional[float] = None  # layoutGrow
    shrink: Optional[float] = None  # layoutShrink
    align_self: Optional[str] = None  # layoutAlign
    sizing_primary: Optional[str] = None  # primaryAxisSizingMode (legacy)
    sizing_counter: Optional[str] = None  # counterAxisSizingMode (legacy)
    grid_columns: Optional[Dict[str, Any]] = None  # {count, gutter} for COLUMNS grid

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "mode": self.mode,
            "direction": self.direction,
            "justify": self.justify,
            "align": self.align,
            "padding": self.padding.to_dict() if self.padding else None,
            "gap": _round(self.gap),
            "wrap": self.wrap,
            "grow": _round(self.grow),
            "shrink": _round(self.shrink),
            "align_self": self.align_self,
            "sizing_primary": self.sizing_primary,
            "sizing_counter": self.sizing_counter,
            "grid_columns": self.grid_columns,
        })


@dataclass
class IRPosition:
    """How a node is placed relative to its parent."""

    mode: str = "absolute"  # "auto" (laid out by an auto-layout parent) | "absolute" | "relative"
    x: Optional[float] = None
    y: Optional[float] = None
    left: Optional[float] = None
    right: Optional[float] = None
    top: Optional[float] = None
    bottom: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "mode": self.mode,
            "x": _round(self.x),
            "y": _round(self.y),
            "left": _round(self.left),
            "right": _round(self.right),
            "top": _round(self.top),
            "bottom": _round(self.bottom),
        })


@dataclass
class IRDimensions:
    """Width/height plus min/max constraints and sizing mode."""

    width: Optional[float] = None
    height: Optional[float] = None
    min_width: Optional[float] = None
    max_width: Optional[float] = None
    min_height: Optional[float] = None
    max_height: Optional[float] = None
    sizing_horizontal: Optional[str] = None  # FIXED | AUTO | FILL
    sizing_vertical: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "width": _round(self.width),
            "height": _round(self.height),
            "min_width": _round(self.min_width),
            "max_width": _round(self.max_width),
            "min_height": _round(self.min_height),
            "max_height": _round(self.max_height),
            "sizing_horizontal": self.sizing_horizontal,
            "sizing_vertical": self.sizing_vertical,
        })


@dataclass
class IRLink:
    """A hyperlink / documentation / prototype link target."""

    kind: str = "url"  # "url" | "file" | "node"
    url: Optional[str] = None
    node_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "kind": self.kind,
            "url": self.url,
            "node_id": self.node_id,
        })


@dataclass
class IRTypography:
    """Normalized typography and text-style properties."""

    font_family: Optional[str] = None
    font_postscript_name: Optional[str] = None
    font_weight: Optional[float] = None
    font_size: Optional[float] = None
    line_height: Optional[float] = None  # lineHeightPx
    letter_spacing: Optional[float] = None
    text_case: Optional[str] = None
    text_decoration: Optional[str] = None
    text_align: Optional[str] = None  # textAlignHorizontal
    vertical_align: Optional[str] = None  # textAlignVertical
    auto_resize: Optional[str] = None  # textAutoResize
    token_refs: List[str] = field(default_factory=list)  # bound variable ids

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "font_family": self.font_family,
            "font_postscript_name": self.font_postscript_name,
            "font_weight": _round(self.font_weight),
            "font_size": _round(self.font_size),
            "line_height": _round(self.line_height),
            "letter_spacing": _round(self.letter_spacing),
            "text_case": self.text_case,
            "text_decoration": self.text_decoration,
            "text_align": self.text_align,
            "vertical_align": self.vertical_align,
            "auto_resize": self.auto_resize,
            "token_refs": list(self.token_refs),
        })


@dataclass
class IRTextContent:
    """Normalized text content plus any hyperlink."""

    characters: str = ""
    hyperlink: Optional[IRLink] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "characters": self.characters,
            "hyperlink": self.hyperlink.to_dict() if self.hyperlink else None,
        })


@dataclass
class IRComponent:
    """Normalized component / component-set reference."""

    key: Optional[str] = None  # file-level component key
    node_id: Optional[str] = None
    name: str = ""
    kind: str = "component"  # "component" | "component_set"
    description: str = ""
    documentation_links: List[IRLink] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "key": self.key,
            "node_id": self.node_id,
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "documentation_links": [l.to_dict() for l in self.documentation_links],
        })


@dataclass
class IRInstance:
    """Normalized component-instance reference."""

    component_id: Optional[str] = None  # the Figma id of the component it instantiates
    component_key: Optional[str] = None
    main_component_id: Optional[str] = None
    main_component_key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "component_id": self.component_id,
            "component_key": self.component_key,
            "main_component_id": self.main_component_id,
            "main_component_key": self.main_component_key,
        })


@dataclass
class IRTokenRef:
    """A single bound token reference on a node."""

    property_name: str = ""  # e.g. "fontSize", "paddingLeft"
    token_key: str = ""  # variable id or style key
    kind: str = "variable"  # "variable" | "style"

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "property_name": self.property_name,
            "token_key": self.token_key,
            "kind": self.kind,
        })


@dataclass
class IRTokens:
    """Bound-variable and style references attached to a node."""

    refs: List[IRTokenRef] = field(default_factory=list)
    bound_variables: Dict[str, str] = field(default_factory=dict)  # property -> variable id
    style_refs: Dict[str, str] = field(default_factory=dict)  # property -> style key

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "refs": [r.to_dict() for r in self.refs],
            "bound_variables": dict(self.bound_variables),
            "style_refs": dict(self.style_refs),
        })


@dataclass
class IResponsive:
    """Responsive/layout constraints that survive scaling."""

    constraints_horizontal: Optional[str] = None  # MIN | CENTER | MAX | STRETCH | SCALE
    constraints_vertical: Optional[str] = None
    sizing_horizontal: Optional[str] = None
    sizing_vertical: Optional[str] = None
    min_width: Optional[float] = None
    max_width: Optional[float] = None
    min_height: Optional[float] = None
    max_height: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "constraints_horizontal": self.constraints_horizontal,
            "constraints_vertical": self.constraints_vertical,
            "sizing_horizontal": self.sizing_horizontal,
            "sizing_vertical": self.sizing_vertical,
            "min_width": _round(self.min_width),
            "max_width": _round(self.max_width),
            "min_height": _round(self.min_height),
            "max_height": _round(self.max_height),
        })


@dataclass
class IRInteraction:
    """A prototype interaction (trigger/action/destination)."""

    trigger: Optional[str] = None
    action: Optional[str] = None
    destination: Optional[str] = None
    transition: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "trigger": self.trigger,
            "action": self.action,
            "destination": self.destination,
            "transition": self.transition,
        })


@dataclass
class IRPrototype:
    """Prototype links and interaction metadata."""

    url: Optional[str] = None
    links: List[IRLink] = field(default_factory=list)
    interactions: List[IRInteraction] = field(default_factory=list)
    start_node: Optional[str] = None  # file-level prototypeStartNode

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "url": self.url,
            "links": [l.to_dict() for l in self.links],
            "interactions": [i.to_dict() for i in self.interactions],
            "start_node": self.start_node,
        })


@dataclass
class IRAnnotations:
    """Annotations and developer metadata."""

    annotation: Optional[str] = None
    developer_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "annotation": self.annotation,
            "developer_metadata": dict(self.developer_metadata),
        })


@dataclass
class IRAssetRef:
    """Reference to a rendered asset (URL from the images endpoint)."""

    node_id: str = ""
    url: Optional[str] = None
    image_ref: Optional[str] = None
    local_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "node_id": self.node_id,
            "url": self.url,
            "image_ref": self.image_ref,
            "local_path": self.local_path,
        })


@dataclass
class IRSource:
    """Source-location metadata for debugging / traceability."""

    file_key: str = ""
    node_id: str = ""
    node_type: str = ""  # original Figma type, e.g. "FRAME"
    path: List[str] = field(default_factory=list)  # ancestor node ids, root first

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "file_key": self.file_key,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "path": list(self.path),
        })


# ---------------------------------------------------------------------------
# Node + document
# ---------------------------------------------------------------------------


@dataclass
class IRNode:
    """A normalized node in the design tree.

    ``raw`` retains the complete original Figma node dict for debugging (it
    includes the child subtree, matching the ingestion-layer ``Node.raw``).
    ``unknown`` holds the subset of raw keys the normalizer did not map, so
    unsupported properties are preserved rather than silently dropped.
    """

    id: str
    name: str
    kind: str
    node_type: str
    source: IRSource
    visible: bool = True
    opacity: float = 1.0
    dimensions: Optional[IRDimensions] = None
    position: Optional[IRPosition] = None
    layout: Optional[IRLayout] = None
    style: Optional[IRStyle] = None
    typography: Optional[IRTypography] = None
    text: Optional[IRTextContent] = None
    component: Optional[IRComponent] = None
    instance: Optional[IRInstance] = None
    tokens: Optional[IRTokens] = None
    responsive: Optional[IResponsive] = None
    prototype: Optional[IRPrototype] = None
    annotations: Optional[IRAnnotations] = None
    asset: Optional[IRAssetRef] = None
    children: List["IRNode"] = field(default_factory=list)
    unknown: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_page(self) -> bool:
        return self.kind == KIND_PAGE

    @property
    def is_frame(self) -> bool:
        return self.kind in (KIND_FRAME, KIND_GROUP, KIND_SECTION)

    @property
    def is_text(self) -> bool:
        return self.kind == KIND_TEXT

    def walk(self) -> Any:
        """Yield this node then all descendants (pre-order)."""
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "node_type": self.node_type,
            "source": self.source.to_dict(),
            "visible": self.visible,
            "opacity": self.opacity,
            "dimensions": self.dimensions.to_dict() if self.dimensions else None,
            "position": self.position.to_dict() if self.position else None,
            "layout": self.layout.to_dict() if self.layout else None,
            "style": self.style.to_dict() if self.style else None,
            "typography": self.typography.to_dict() if self.typography else None,
            "text": self.text.to_dict() if self.text else None,
            "component": self.component.to_dict() if self.component else None,
            "instance": self.instance.to_dict() if self.instance else None,
            "tokens": self.tokens.to_dict() if self.tokens else None,
            "responsive": self.responsive.to_dict() if self.responsive else None,
            "prototype": self.prototype.to_dict() if self.prototype else None,
            "annotations": self.annotations.to_dict() if self.annotations else None,
            "asset": self.asset.to_dict() if self.asset else None,
            "children": [c.to_dict() for c in self.children],
            "unknown": dict(self.unknown),
            "raw": dict(self.raw),
        })


@dataclass
class IRToken:
    """A design token: a variable (from the file's ``variables`` map) or a
    style (from the file's ``styles`` map)."""

    kind: str = "variable"  # "variable" | "style"
    key: str = ""
    name: str = ""
    token_type: str = ""  # FLOAT | COLOR | ... (variables) ; FILL | TEXT | ... (styles)
    value: Any = None
    description: str = ""
    resolved_type: Optional[str] = None  # variable resolvedType

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "kind": self.kind,
            "key": self.key,
            "name": self.name,
            "token_type": self.token_type,
            "value": self.value,
            "description": self.description,
            "resolved_type": self.resolved_type,
        })


@dataclass
class IRDocument:
    """The top-level normalized design IR."""

    schema_version: int = IR_VERSION
    file_key: str = ""
    name: str = ""
    source: Optional[IRSource] = None
    root: Optional[IRNode] = None
    pages: List[IRNode] = field(default_factory=list)
    components: Dict[str, IRComponent] = field(default_factory=dict)
    component_sets: Dict[str, IRComponent] = field(default_factory=dict)
    styles: Dict[str, IRToken] = field(default_factory=dict)
    variables: Dict[str, IRToken] = field(default_factory=dict)
    assets: Dict[str, str] = field(default_factory=dict)  # node_id -> url
    prototype_start_node: Optional[str] = None
    unknown: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def all_nodes(self) -> List[IRNode]:
        if self.root is None:
            return []
        return list(self.root.walk())

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "schema_version": self.schema_version,
            "file_key": self.file_key,
            "name": self.name,
            "source": self.source.to_dict() if self.source else None,
            "root": self.root.to_dict() if self.root else None,
            "pages": [p.to_dict() for p in self.pages],
            "components": {k: v.to_dict() for k, v in self.components.items()},
            "component_sets": {k: v.to_dict() for k, v in self.component_sets.items()},
            "styles": {k: v.to_dict() for k, v in self.styles.items()},
            "variables": {k: v.to_dict() for k, v in self.variables.items()},
            "assets": dict(self.assets),
            "prototype_start_node": self.prototype_start_node,
            "unknown": dict(self.unknown),
            "raw": dict(self.raw),
        })


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def ir_to_dict(document: IRDocument) -> Dict[str, Any]:
    """Serialize an :class:`IRDocument` to a plain JSON-safe dict."""
    return document.to_dict()


def ir_to_json(document: IRDocument, indent: int = 2) -> str:
    """Serialize an :class:`IRDocument` to a deterministic JSON string.

    ``sort_keys=True`` keeps key ordering stable so snapshot tests are
    reproducible across Python versions.
    """
    return json.dumps(document.to_dict(), indent=indent, sort_keys=True)
