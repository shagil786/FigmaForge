"""
FigmaForge Backend Adapters.

Each backend converts the framework-neutral Design IR + LayoutPlan into
target-specific generated source code.  Backends declare their capabilities
and report unsupported features explicitly — nothing is silently approximated.

Available backends:

- ``html_css`` — Plain HTML + CSS (fully implemented)
- ``react_tailwind`` — React + Tailwind CSS (stub)
- ``vue`` — Vue single-file components (stub)
- ``svelte`` — Svelte components (stub)
- ``swiftui`` — SwiftUI views (stub)
- ``flutter`` — Flutter widgets (stub)
"""

from .protocol import (
    BackendAdapter,
    BackendCapabilities,
    Feature,
    FidelityLoss,
    GeneratedFile,
    GeneratedOutput,
    WEB_COMMON_FEATURES,
)
from .registry import (
    BackendRegistry,
    get_registry,
    reset_registry,
)

__all__ = [
    "BackendAdapter",
    "BackendCapabilities",
    "BackendRegistry",
    "Feature",
    "FidelityLoss",
    "GeneratedFile",
    "GeneratedOutput",
    "WEB_COMMON_FEATURES",
    "get_registry",
    "reset_registry",
]
