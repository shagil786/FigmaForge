"""
Semantic token resolution.

Resolves Figma variables and styles from the Design IR into a normalized,
semantic token set with seven categories: color, typography, spacing, radius,
shadow, opacity, and breakpoint.

Two rules drive the output:

- **Prefer existing tokens.** When a Figma variable matches a project-library
  token (by normalized name or by value), the library token wins and
  ``resolved`` is true. Nothing is duplicated.
- **References, not duplicated values.** Node-level bindings (``boundVariables``
  / ``style_refs``) are emitted as token *references* (``token_ref``) pointing
  into the semantic token table — the raw value lives in exactly one place.

Unsupported token types (e.g. STRING variables, GRID styles) are never dropped:
they are reported explicitly under ``unsupported``. All matching is
deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ir_types import IRDocument, IRNode, IRToken, KIND_FRAME, KIND_PAGE
from .library_types import ProjectToken, normalize_name, slugify

# Semantic categories this resolver can emit.
CATEGORY_COLOR = "color"
CATEGORY_TYPOGRAPHY = "typography"
CATEGORY_SPACING = "spacing"
CATEGORY_RADIUS = "radius"
CATEGORY_SHADOW = "shadow"
CATEGORY_OPACITY = "opacity"
CATEGORY_BREAKPOINT = "breakpoint"

# Name fragments used to classify Figma FLOAT variables by purpose.
_FLOAT_CLASSIFIERS = (
    (CATEGORY_RADIUS, ("radius", "corner")),
    (CATEGORY_SPACING, ("space", "padding", "margin", "gap")),
    (CATEGORY_TYPOGRAPHY, ("font", "line", "letter", "type", "typography", "size")),
    (CATEGORY_OPACITY, ("opacity",)),
    (CATEGORY_SHADOW, ("shadow", "elevation")),
)

# Deterministic alias table for matching frames to breakpoint tokens.
DEFAULT_BREAKPOINT_ALIASES: Dict[str, List[str]] = {
    "sm": ["sm", "small", "mobile", "640"],
    "md": ["md", "medium", "tablet", "1024"],
    "lg": ["lg", "large", "desktop", "1440"],
    "xl": ["xl", "wide", "1920"],
}


def _round(value: Any, places: int = 4) -> Any:
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return value


def _color_value(value: Any) -> Dict[str, float]:
    if isinstance(value, dict):
        return {
            "r": _round(value.get("r", 0)),
            "g": _round(value.get("g", 0)),
            "b": _round(value.get("b", 0)),
            "a": _round(value.get("a", 1)),
        }
    return {}


def _same_color(a: Any, b: Any) -> bool:
    return _color_value(a) == _color_value(b)


@dataclass
class SemanticToken:
    """A resolved semantic token."""

    key: str  # e.g. "color/primary"
    category: str
    name: str
    value: Any = None
    source: str = ""  # "library:<name>" | "figma:variable:<id>" | "figma:style:<key>"
    resolved: bool = True
    figma_key: Optional[str] = None  # variable id / style key it came from

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "category": self.category,
            "name": self.name,
            "value": self.value,
            "source": self.source,
            "resolved": self.resolved,
            "figma_key": self.figma_key,
        }


@dataclass
class TokenResolution:
    """The full result of token resolution."""

    semantic: List[SemanticToken] = field(default_factory=list)
    node_refs: List[Dict[str, Any]] = field(default_factory=list)
    breakpoint_matches: List[Dict[str, Any]] = field(default_factory=list)
    breakpoint_unmatched: List[Dict[str, Any]] = field(default_factory=list)
    unsupported: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "semantic": [t.to_dict() for t in self.semantic],
            "node_refs": list(self.node_refs),
            "breakpoint_matches": list(self.breakpoint_matches),
            "breakpoint_unmatched": list(self.breakpoint_unmatched),
            "unsupported": list(self.unsupported),
        }


class TokenResolver:
    """Resolve Figma variables/styles into semantic tokens and references."""

    def __init__(
        self,
        document: IRDocument,
        library_tokens: Optional[List[ProjectToken]] = None,
        breakpoint_aliases: Optional[Dict[str, List[str]]] = None,
    ):
        self._document = document
        self._library = list(library_tokens or [])
        self._aliases = breakpoint_aliases or DEFAULT_BREAKPOINT_ALIASES
        self._var_to_key: Dict[str, str] = {}  # variable id -> semantic key
        self._style_to_key: Dict[str, str] = {}  # style key -> semantic key

    # ------------------------------------------------------------------ API
    def resolve(self) -> TokenResolution:
        result = TokenResolution()
        self._resolve_variables(result)
        self._resolve_styles(result)
        self._resolve_breakpoints(result)
        self._resolve_node_refs(result)
        return result

    # ---------------------------------------------------------- variables
    def _resolve_variables(self, result: TokenResolution) -> None:
        for var in self._document.variables.values():
            category = self._classify_variable(var)
            if category is None:
                result.unsupported.append({
                    "kind": "variable",
                    "key": var.key,
                    "name": var.name,
                    "token_type": var.token_type,
                    "reason": f"token type {var.token_type!r} is not supported",
                })
                continue
            token = self._emit(category, var.name, var.value, var.key, result)
            if token is not None:
                self._var_to_key[var.key] = token.key

    # ------------------------------------------------------------ styles
    def _resolve_styles(self, result: TokenResolution) -> None:
        style_category = {
            "FILL": CATEGORY_COLOR,
            "TEXT": CATEGORY_TYPOGRAPHY,
            "EFFECT": CATEGORY_SHADOW,
        }
        for style in self._document.styles.values():
            category = style_category.get(style.token_type)
            if category is None:
                result.unsupported.append({
                    "kind": "style",
                    "key": style.key,
                    "name": style.name,
                    "token_type": style.token_type,
                    "reason": f"style type {style.token_type!r} is not supported",
                })
                continue
            # Figma styles carry no value in the file response; match by name.
            lib = self._find_library(category, style.name)
            if lib is not None:
                result.semantic.append(SemanticToken(
                    key=f"{category}/{slugify(lib.name)}",
                    category=category,
                    name=lib.name,
                    value=lib.value,
                    source=f"library:{lib.name}",
                    resolved=True,
                    figma_key=style.key,
                ))
                self._style_to_key[style.key] = f"{category}/{slugify(lib.name)}"
            else:
                key = f"{category}/{slugify(style.name)}"
                result.semantic.append(SemanticToken(
                    key=key,
                    category=category,
                    name=style.name,
                    value=None,
                    source=f"figma:style:{style.key}",
                    resolved=False,
                    figma_key=style.key,
                ))
                self._style_to_key[style.key] = key

    # -------------------------------------------------------- breakpoints
    def _resolve_breakpoints(self, result: TokenResolution) -> None:
        # Map alias-size -> emitted semantic token key so breakpoint matches
        # reference the real token (e.g. "breakpoint/breakpoint-lg").
        breakpoint_key_by_size: Dict[str, str] = {}
        for lib in self._library:
            if lib.type != CATEGORY_BREAKPOINT:
                continue
            key = f"{CATEGORY_BREAKPOINT}/{slugify(lib.name)}"
            result.semantic.append(SemanticToken(
                key=key,
                category=CATEGORY_BREAKPOINT,
                name=lib.name,
                value=lib.value,
                source=f"library:{lib.name}",
                resolved=True,
            ))
            name = slugify(lib.name)
            for size in self._aliases:
                if name == f"breakpoint-{size}":
                    breakpoint_key_by_size[size] = key

        # Match frames/pages to breakpoints by name alias (deterministic).
        for node in self._document.all_nodes():
            if node.kind not in (KIND_PAGE, KIND_FRAME):
                continue
            size = self._match_breakpoint(node.name)
            if size is not None:
                result.breakpoint_matches.append({
                    "node_id": node.id,
                    "name": node.name,
                    "breakpoint_token": breakpoint_key_by_size.get(
                        size, f"{CATEGORY_BREAKPOINT}/{size}"),
                })
            else:
                result.breakpoint_unmatched.append({
                    "node_id": node.id,
                    "name": node.name,
                })

    # ------------------------------------------------------- node refs
    def _resolve_node_refs(self, result: TokenResolution) -> None:
        for node in self._document.all_nodes():
            tokens = node.tokens
            if tokens is None:
                continue
            for prop, var_id in tokens.bound_variables.items():
                key = self._var_to_key.get(var_id)
                if key is not None:
                    result.node_refs.append({
                        "node_id": node.id,
                        "property": prop,
                        "figma_variable_id": var_id,
                        "token_ref": key,
                        "resolved": True,
                    })
                else:
                    result.node_refs.append({
                        "node_id": node.id,
                        "property": prop,
                        "figma_variable_id": var_id,
                        "token_ref": None,
                        "resolved": False,
                        "reason": f"variable {var_id!r} is unresolved",
                    })
            for prop, style_key in tokens.style_refs.items():
                key = self._style_to_key.get(style_key)
                result.node_refs.append({
                    "node_id": node.id,
                    "property": prop,
                    "figma_style_key": style_key,
                    "token_ref": key,
                    "resolved": key is not None,
                })

    # ------------------------------------------------------------ helpers
    def _emit(
        self,
        category: str,
        name: str,
        value: Any,
        figma_key: str,
        result: TokenResolution,
    ) -> Optional[SemanticToken]:
        lib = self._find_library(category, name, value)
        if lib is not None:
            token = SemanticToken(
                key=f"{category}/{slugify(lib.name)}",
                category=category,
                name=lib.name,
                value=lib.value,
                source=f"library:{lib.name}",
                resolved=True,
                figma_key=figma_key,
            )
        else:
            token = SemanticToken(
                key=f"{category}/{slugify(name)}",
                category=category,
                name=name,
                value=value,
                source=f"figma:variable:{figma_key}",
                resolved=False,
                figma_key=figma_key,
            )
        result.semantic.append(token)
        return token

    def _find_library(
        self,
        category: str,
        name: str,
        value: Any = None,
    ) -> Optional[ProjectToken]:
        """Prefer an existing library token by name, then by value."""
        slug = slugify(name)
        for lib in self._library:
            if lib.type != category:
                continue
            if slugify(lib.name) == slug:
                return lib
        if value is not None:
            for lib in self._library:
                if lib.type != category:
                    continue
                if category == CATEGORY_COLOR and _same_color(lib.value, value):
                    return lib
                if category in (CATEGORY_SPACING, CATEGORY_RADIUS, CATEGORY_OPACITY) \
                        and isinstance(value, (int, float)) \
                        and _round(lib.value) == _round(value):
                    return lib
        return None

    def _classify_variable(self, var: IRToken) -> Optional[str]:
        """Map a Figma variable to a semantic category, or ``None`` if unsupported."""
        token_type = (var.token_type or var.resolved_type or "").upper()
        if token_type == "COLOR":
            return CATEGORY_COLOR
        if token_type == "FLOAT":
            return self._classify_float(var.name)
        return None

    @staticmethod
    def _classify_float(name: str) -> Optional[str]:
        normalized = normalize_name(name)
        if not normalized:
            return None
        for category, fragments in _FLOAT_CLASSIFIERS:
            if any(fragment in normalized for fragment in fragments):
                return category
        return None

    def _match_breakpoint(self, name: str) -> Optional[str]:
        normalized = normalize_name(name)
        if not normalized:
            return None
        # Longest alias wins to avoid "mobile" matching "sm" and "md" alike.
        best: Optional[str] = None
        best_len = -1
        for size, aliases in self._aliases.items():
            for alias in aliases:
                if alias in normalized and len(alias) > best_len:
                    best = size
                    best_len = len(alias)
        return best
