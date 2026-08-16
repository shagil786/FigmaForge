"""Deterministic accessibility diagnostics for the normalized design IR.

This module reports findings separately from backend fidelity losses. It does
not invent platform-specific semantics; generated backends can consume the
node-level findings and map them to their own accessibility primitives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .ir_types import IRColor, IRDocument, IRNode


@dataclass(frozen=True)
class AccessibilityFinding:
    node_id: str
    rule: str
    severity: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "node_id": self.node_id,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(frozen=True)
class AccessibilityReport:
    findings: Tuple[AccessibilityFinding, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "finding_count": len(self.findings),
            "findings": [finding.to_dict() for finding in self.findings],
        }


_INTERACTIVE_WORDS = ("button", "link", "input", "checkbox", "radio", "select", "tab")
_GENERIC_NAMES = {"button", "link", "input", "checkbox", "radio", "select", "tab", "frame", "group", "rectangle"}


def _metadata(node: IRNode) -> Dict[str, Any]:
    annotations = node.annotations
    if annotations is None:
        return {}
    return dict(annotations if isinstance(annotations, dict) else annotations.developer_metadata)


def _is_interactive(node: IRNode) -> bool:
    haystack = f"{node.name} {node.node_type}".lower()
    return any(word in haystack for word in _INTERACTIVE_WORDS)


def _has_accessible_name(node: IRNode) -> bool:
    metadata = _metadata(node)
    for key in ("aria-label", "ariaLabel", "accessible_name", "accessibilityLabel", "label"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return node.name.strip().lower() not in _GENERIC_NAMES and bool(node.name.strip())


def _solid_color(node: Optional[IRNode]) -> Optional[IRColor]:
    if node is None or node.style is None:
        return None
    for fill in node.style.fills:
        if fill.visible and fill.kind == "solid" and fill.color is not None:
            return fill.color
    return None


def _luminance(color: IRColor) -> float:
    def channel(value: float) -> float:
        value = max(0.0, min(1.0, float(value)))
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b)


def _contrast_ratio(first: IRColor, second: IRColor) -> float:
    light = max(_luminance(first), _luminance(second))
    dark = min(_luminance(first), _luminance(second))
    return (light + 0.05) / (dark + 0.05)


def analyze_document(document: IRDocument) -> AccessibilityReport:
    findings: List[AccessibilityFinding] = []

    def visit(node: IRNode, parent: Optional[IRNode]) -> None:
        if _is_interactive(node) and not _has_accessible_name(node):
            findings.append(AccessibilityFinding(
                node.id,
                "accessible_name",
                "error",
                "Interactive node has no accessible name; add an aria-label or semantic label.",
            ))

        if node.is_text and parent is not None:
            foreground = _solid_color(node)
            background = _solid_color(parent)
            if foreground is not None and background is not None:
                ratio = _contrast_ratio(foreground, background)
                if ratio < 4.5:
                    findings.append(AccessibilityFinding(
                        node.id,
                        "color_contrast",
                        "error",
                        f"Text contrast ratio {ratio:.2f}:1 is below the WCAG AA 4.5:1 threshold.",
                    ))

        for child in node.children:
            visit(child, node)

    if document.root is not None:
        visit(document.root, None)
    return AccessibilityReport(tuple(findings))
