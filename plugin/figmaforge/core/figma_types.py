"""
Typed response models for the Figma ingestion layer.

These dataclasses mirror the subset of the Figma REST API (v1) response
shapes that FigmaForge consumes. Constructors are pure functions over plain
dicts (``from_dict``) so normalization stays deterministic and testable
without a network connection.

All ``from_dict`` methods are defensive: unknown or missing keys degrade to
safe defaults rather than raising, and every value is coerced to the expected
type. This mirrors the evidence-based, non-guessing convention used elsewhere
in FigmaForge.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


# ---------------------------------------------------------------------------
# Primitive value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Color:
    r: float = 0.0
    g: float = 0.0
    b: float = 0.0
    a: float = 1.0

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> Optional["Color"]:
        if not isinstance(data, dict):
            return None
        return Color(
            r=float(data.get("r", 0.0) or 0.0),
            g=float(data.get("g", 0.0) or 0.0),
            b=float(data.get("b", 0.0) or 0.0),
            a=float(data.get("a", 1.0) if data.get("a") is not None else 1.0),
        )


@dataclass(frozen=True)
class BoundingBox:
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> Optional["BoundingBox"]:
        if not isinstance(data, dict):
            return None
        return BoundingBox(
            x=float(data.get("x", 0.0) or 0.0),
            y=float(data.get("y", 0.0) or 0.0),
            width=float(data.get("width", 0.0) or 0.0),
            height=float(data.get("height", 0.0) or 0.0),
        )


@dataclass(frozen=True)
class Constraints:
    horizontal: str = "SCALE"
    vertical: str = "SCALE"

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> Optional["Constraints"]:
        if not isinstance(data, dict):
            return None
        return Constraints(
            horizontal=str(data.get("horizontal", "SCALE") or "SCALE"),
            vertical=str(data.get("vertical", "SCALE") or "SCALE"),
        )


@dataclass(frozen=True)
class GradientStop:
    position: float = 0.0
    color: Optional[Color] = None

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> Optional["GradientStop"]:
        if not isinstance(data, dict):
            return None
        return GradientStop(
            position=float(data.get("position", 0.0) or 0.0),
            color=Color.from_dict(data.get("color")),
        )


@dataclass(frozen=True)
class Paint:
    """A fill or stroke (Figma ``Paint``)."""

    type: str = "SOLID"
    visible: bool = True
    opacity: float = 1.0
    color: Optional[Color] = None
    image_ref: Optional[str] = None  # present for IMAGE/VIDEO paints
    gradient_handles: List[Any] = field(default_factory=list)
    gradient_stops: List[GradientStop] = field(default_factory=list)
    scale_mode: Optional[str] = None
    image_transform: Optional[Dict[str, Any]] = None
    blend_mode: Optional[str] = None

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> Optional["Paint"]:
        if not isinstance(data, dict):
            return None
        return Paint(
            type=str(data.get("type", "SOLID") or "SOLID"),
            visible=bool(data.get("visible", True) is not False),
            opacity=float(data.get("opacity", 1.0) if data.get("opacity") is not None else 1.0),
            color=Color.from_dict(data.get("color")),
            image_ref=data.get("imageRef"),
            gradient_handles=list(data.get("gradientHandlePositions", []) or []),
            gradient_stops=[
                GradientStop.from_dict(s)
                for s in (data.get("gradientStops", []) or [])
                if isinstance(s, dict)
            ],
            scale_mode=data.get("scaleMode"),
            image_transform=data.get("imageTransform"),
            blend_mode=data.get("blendMode"),
        )


@dataclass(frozen=True)
class Effect:
    """A Figma ``Effect`` (shadow / blur)."""

    type: str = "LAYER_BLUR"
    visible: bool = True
    radius: float = 0.0
    color: Optional[Color] = None
    offset: Optional[Any] = None
    spread: float = 0.0
    blend_mode: Optional[str] = None

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> Optional["Effect"]:
        if not isinstance(data, dict):
            return None
        return Effect(
            type=str(data.get("type", "LAYER_BLUR") or "LAYER_BLUR"),
            visible=bool(data.get("visible", True) is not False),
            radius=float(data.get("radius", 0.0) or 0.0),
            color=Color.from_dict(data.get("color")),
            offset=data.get("offset"),
            spread=float(data.get("spread", 0.0) or 0.0),
            blend_mode=data.get("blendMode"),
        )


@dataclass(frozen=True)
class Hyperlink:
    """A text node hyperlink (URL or Figma file link)."""

    type: str = "URL"
    url: Optional[str] = None

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> Optional["Hyperlink"]:
        if not isinstance(data, dict):
            return None
        return Hyperlink(
            type=str(data.get("type", "URL") or "URL"),
            url=data.get("url"),
        )


@dataclass(frozen=True)
class TextStyle:
    """A text node ``style`` object."""

    font_family: Optional[str] = None
    font_post_script_name: Optional[str] = None
    font_weight: Optional[float] = None
    font_size: Optional[float] = None
    line_height_px: Optional[float] = None
    letter_spacing: Optional[float] = None
    text_case: Optional[str] = None
    text_decoration: Optional[str] = None
    hyperlink: Optional[Hyperlink] = None

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> Optional["TextStyle"]:
        if not isinstance(data, dict):
            return None
        return TextStyle(
            font_family=data.get("fontFamily"),
            font_post_script_name=data.get("fontPostScriptName"),
            font_weight=_as_float(data.get("fontWeight")),
            font_size=_as_float(data.get("fontSize")),
            line_height_px=_as_float(data.get("lineHeightPx")),
            letter_spacing=_as_float(data.get("letterSpacing")),
            text_case=data.get("textCase"),
            text_decoration=data.get("textDecoration"),
            hyperlink=Hyperlink.from_dict(data.get("hyperlink")),
        )


@dataclass(frozen=True)
class AutoLayout:
    """Auto-layout / frame properties."""

    layout_mode: str = "NONE"  # NONE | HORIZONTAL | VERTICAL
    primary_axis_align_items: Optional[str] = None
    counter_axis_align_items: Optional[str] = None
    primary_axis_sizing_mode: Optional[str] = None
    counter_axis_sizing_mode: Optional[str] = None
    padding_top: float = 0.0
    padding_right: float = 0.0
    padding_bottom: float = 0.0
    padding_left: float = 0.0
    item_spacing: float = 0.0
    layout_sizing_horizontal: Optional[str] = None
    layout_sizing_vertical: Optional[str] = None

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> Optional["AutoLayout"]:
        if not isinstance(data, dict):
            return None
        return AutoLayout(
            layout_mode=str(data.get("layoutMode", "NONE") or "NONE"),
            primary_axis_align_items=data.get("primaryAxisAlignItems"),
            counter_axis_align_items=data.get("counterAxisAlignItems"),
            primary_axis_sizing_mode=data.get("primaryAxisSizingMode"),
            counter_axis_sizing_mode=data.get("counterAxisSizingMode"),
            padding_top=_as_float(data.get("paddingTop")),
            padding_right=_as_float(data.get("paddingRight")),
            padding_bottom=_as_float(data.get("paddingBottom")),
            padding_left=_as_float(data.get("paddingLeft")),
            item_spacing=_as_float(data.get("itemSpacing")),
            layout_sizing_horizontal=data.get("layoutSizingHorizontal"),
            layout_sizing_vertical=data.get("layoutSizingVertical"),
        )


@dataclass(frozen=True)
class DocumentLink:
    """A documentation link attached to a component."""

    name: Optional[str] = None
    url: Optional[str] = None

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> Optional["DocumentLink"]:
        if not isinstance(data, dict):
            return None
        return DocumentLink(name=data.get("name"), url=data.get("url"))


@dataclass(frozen=True)
class StyleRef:
    """A keyed style from the file's ``styles`` map."""

    key: str
    name: str
    style_type: str  # FILL | TEXT | EFFECT | GRID
    description: str = ""

    @staticmethod
    def from_dict(key: str, data: Optional[Dict[str, Any]]) -> Optional["StyleRef"]:
        if not isinstance(data, dict):
            return None
        return StyleRef(
            key=key,
            name=str(data.get("name", key) or key),
            style_type=str(data.get("styleType", "") or ""),
            description=str(data.get("description", "") or ""),
        )


@dataclass(frozen=True)
class ComponentRef:
    """A keyed component or component-set from the file map."""

    key: str
    name: str
    node_id: Optional[str] = None
    type: str = "COMPONENT"
    description: str = ""
    documentation_links: List[DocumentLink] = field(default_factory=list)

    @staticmethod
    def from_dict(key: str, data: Optional[Dict[str, Any]]) -> Optional["ComponentRef"]:
        if not isinstance(data, dict):
            return None
        return ComponentRef(
            key=key,
            name=str(data.get("name", key) or key),
            node_id=data.get("nodeId"),
            type=str(data.get("type", "COMPONENT") or "COMPONENT"),
            description=str(data.get("description", "") or ""),
            documentation_links=[
                DocumentLink.from_dict(d)
                for d in (data.get("documentationLinks", []) or [])
                if isinstance(d, dict)
            ],
        )


# ---------------------------------------------------------------------------
# Node model
# ---------------------------------------------------------------------------


# The subset of Figma node types FigmaForge distinguishes. Other types
# (VECTOR, RECTANGLE, ...) are kept as generic Node instances with their
# declared type string preserved.
NODE_TYPE_PAGE = "CANVAS"
NODE_TYPE_FRAME = "FRAME"
NODE_TYPE_GROUP = "GROUP"
NODE_TYPE_COMPONENT = "COMPONENT"
NODE_TYPE_COMPONENT_SET = "COMPONENT_SET"
NODE_TYPE_INSTANCE = "INSTANCE"
NODE_TYPE_TEXT = "TEXT"


@dataclass
class Node:
    """A normalized Figma node.

    ``raw`` retains the original response dict for that node so callers can
    debug against the unmodified payload without re-fetching.
    """

    id: str = ""
    name: str = ""
    type: str = ""
    visible: bool = True
    opacity: float = 1.0
    children: List["Node"] = field(default_factory=list)
    absolute_bounding_box: Optional[BoundingBox] = None
    constraints: Optional[Constraints] = None
    fills: List[Paint] = field(default_factory=list)
    strokes: List[Paint] = field(default_factory=list)
    effects: List[Effect] = field(default_factory=list)
    auto_layout: Optional[AutoLayout] = None
    # text
    characters: Optional[str] = None
    text_style: Optional[TextStyle] = None
    # instances / components
    component_id: Optional[str] = None
    main_component: Optional[Any] = None
    # variables & annotations
    bound_variables: Dict[str, Any] = field(default_factory=dict)
    annotation: Optional[str] = None
    url: Optional[str] = None
    # raw debug payload
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_page(self) -> bool:
        return self.type == NODE_TYPE_PAGE

    @property
    def is_frame(self) -> bool:
        return self.type in (NODE_TYPE_FRAME, NODE_TYPE_GROUP)

    @property
    def is_text(self) -> bool:
        return self.type == NODE_TYPE_TEXT

    @property
    def is_component(self) -> bool:
        return self.type == NODE_TYPE_COMPONENT

    @property
    def is_component_set(self) -> bool:
        return self.type == NODE_TYPE_COMPONENT_SET

    @property
    def is_instance(self) -> bool:
        return self.type == NODE_TYPE_INSTANCE

    @property
    def link_url(self) -> Optional[str]:
        """Best-effort link: node url, text hyperlink, or annotation URL."""
        if self.url:
            return self.url
        if self.text_style and self.text_style.hyperlink:
            return self.text_style.hyperlink.url
        return None

    @staticmethod
    def from_dict(data: Optional[Dict[str, Any]]) -> Optional["Node"]:
        """Build a normalized node from a raw Figma node dict."""
        if not isinstance(data, dict):
            return None
        return Node(
            id=str(data.get("id", "") or ""),
            name=str(data.get("name", "") or ""),
            type=str(data.get("type", "") or ""),
            visible=bool(data.get("visible", True) is not False),
            opacity=_as_float(data.get("opacity"), default=1.0),
            children=[
                Node.from_dict(c)
                for c in (data.get("children", []) or [])
                if isinstance(c, dict)
            ],
            absolute_bounding_box=BoundingBox.from_dict(data.get("absoluteBoundingBox")),
            constraints=Constraints.from_dict(data.get("constraints")),
            fills=[
                Paint.from_dict(p)
                for p in (data.get("fills", []) or [])
                if isinstance(p, dict)
            ],
            strokes=[
                Paint.from_dict(p)
                for p in (data.get("strokes", []) or [])
                if isinstance(p, dict)
            ],
            effects=[
                Effect.from_dict(e)
                for e in (data.get("effects", []) or [])
                if isinstance(e, dict)
            ],
            auto_layout=AutoLayout.from_dict(data),
            characters=data.get("characters"),
            text_style=TextStyle.from_dict(data.get("style")),
            component_id=data.get("componentId"),
            main_component=data.get("mainComponent"),
            bound_variables=dict(data.get("boundVariables", {}) or {}),
            annotation=data.get("annotation"),
            url=data.get("url"),
            raw=data,
        )

    def walk(self):
        """Yield this node then all descendants (pre-order)."""
        yield self
        for child in self.children:
            yield from child.walk()


# ---------------------------------------------------------------------------
# File / ingestion models
# ---------------------------------------------------------------------------


@dataclass
class FigmaFile:
    """A normalized Figma file.

    ``raw`` holds the complete, untouched file response (document tree plus
    components/componentSets/styles maps) for separate debug storage.
    """

    file_key: str
    name: str
    role: Optional[str] = None
    editor_type: Optional[str] = None
    last_modified: Optional[str] = None
    thumbnail_url: Optional[str] = None
    version: Optional[str] = None
    schema_version: Optional[int] = None
    document: Optional[Node] = None
    components: Dict[str, ComponentRef] = field(default_factory=dict)
    component_sets: Dict[str, ComponentRef] = field(default_factory=dict)
    styles: Dict[str, StyleRef] = field(default_factory=dict)
    variables: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def pages(self) -> List[Node]:
        return [n for n in self._all_nodes() if n.is_page]

    def frames(self) -> List[Node]:
        return [n for n in self._all_nodes() if n.is_frame]

    def text_nodes(self) -> List[Node]:
        return [n for n in self._all_nodes() if n.is_text]

    def components_nodes(self) -> List[Node]:
        return [n for n in self._all_nodes() if n.is_component]

    def component_set_nodes(self) -> List[Node]:
        return [n for n in self._all_nodes() if n.is_component_set]

    def instances(self) -> List[Node]:
        return [n for n in self._all_nodes() if n.is_instance]

    def _all_nodes(self) -> List[Node]:
        if self.document is None:
            return []
        return list(self.document.walk())

    @classmethod
    def from_dict(cls, file_key: str, raw: Dict[str, Any]) -> "FigmaFile":
        """Build a normalized file from a raw ``/v1/files/{key}`` response.

        ``raw`` is retained verbatim for separate debug storage.
        """
        return cls(
            file_key=file_key,
            name=str(raw.get("name", "") or ""),
            role=raw.get("role"),
            editor_type=raw.get("editorType"),
            last_modified=raw.get("lastModified"),
            thumbnail_url=raw.get("thumbnailUrl"),
            version=raw.get("version"),
            schema_version=raw.get("schemaVersion"),
            document=Node.from_dict(raw.get("document")),
            components={
                k: ComponentRef.from_dict(k, v)
                for k, v in (raw.get("components", {}) or {}).items()
                if isinstance(v, dict)
            },
            component_sets={
                k: ComponentRef.from_dict(k, v)
                for k, v in (raw.get("componentSets", {}) or {}).items()
                if isinstance(v, dict)
            },
            styles={
                k: StyleRef.from_dict(k, v)
                for k, v in (raw.get("styles", {}) or {}).items()
                if isinstance(v, dict)
            },
            variables=dict(raw.get("variables", {}) or {}),
            raw=raw,
        )


@dataclass
class FigmaNodeResponse:
    """Result of the ``/v1/files/{key}/nodes`` endpoint."""

    file_key: str
    name: Optional[str] = None
    nodes: Dict[str, Node] = field(default_factory=dict)  # node id -> node
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, file_key: str, raw: Dict[str, Any]) -> "FigmaNodeResponse":
        nodes: Dict[str, Node] = {}
        for node_id, payload in (raw.get("nodes", {}) or {}).items():
            if not isinstance(payload, dict):
                continue
            node = Node.from_dict(payload.get("document"))
            if node is not None:
                nodes[node_id] = node
        return cls(file_key=file_key, name=raw.get("name"), nodes=nodes, raw=raw)


@dataclass
class ImageSet:
    """Result of the ``/v1/images/{key}`` endpoint."""

    file_key: str
    images: Dict[str, str] = field(default_factory=dict)  # node id -> url
    meta: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
