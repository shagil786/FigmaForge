"""
Code Generator Protocol and Abstract Syntax Types (Part 6).

This defines the abstraction for code emission. The generator does not emit strings
directly; it emits a semantic 'VNode' (Virtual Node) tree which allows formatting
adapters (React mapping, CSS Modules, Tailwind) to assemble the final files cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class VStyle:
    """An abstract style block attached to a node or breakpoint.

    Dictionaries here map CSS-like keys to string values.
    Adapter modules translate these to inline styles, Tailwind utility classes,
    or CSS Module classes.
    """
    base: Dict[str, Any] = field(default_factory=dict)
    breakpoints: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class VNode:
    """A framework-neutral Virtual DOM node mapping to an HTML tag or Component."""
    node_id: str
    tag: str = "div"  # HTML tag or Component Name
    is_component: bool = False  # True if 'tag' references a React component
    props: Dict[str, Any] = field(default_factory=dict)
    style: VStyle = field(default_factory=VStyle)
    children: List['VNode'] = field(default_factory=list)
    text_content: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for deterministic snapshotting."""
        out = {
            "node_id": self.node_id,
            "tag": self.tag,
        }
        if self.is_component:
            out["is_component"] = True
        if self.props:
            out["props"] = self.props
        if self.style.base or self.style.breakpoints:
            out["style"] = {
                "base": self.style.base,
                "breakpoints": self.style.breakpoints,
            }
        if self.text_content is not None:
            out["text_content"] = self.text_content
        if self.children:
            out["children"] = [c.to_dict() for c in self.children]
        # Keep canonical output clean
        return {k: v for k, v in out.items() if v or isinstance(v, (bool, int, float))}


@dataclass
class GeneratorManifest:
    """Tracks which Figma nodes were emitted to which files."""
    components: Dict[str, str] = field(default_factory=dict)  # node_id -> filepath
    assets: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "components": self.components,
            "assets": self.assets,
        }
