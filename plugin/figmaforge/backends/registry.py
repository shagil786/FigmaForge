"""
Backend registry — discover, select, and manage code-generation backends.

The registry maps target identifiers (e.g. ``"react_css"``) to backend
adapter instances.  It supports:

- Explicit registration via :func:`register`.
- Auto-discovery of built-in backends via :func:`discover_builtins`.
- Lookup by name, framework, or renderer via :func:`get` / :func:`find`.
- Pre-flight viability checking via :func:`preflight`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from .protocol import BackendAdapter, BackendCapabilities, FidelityLoss


class BackendRegistry:
    """Central registry of available code-generation backends."""

    def __init__(self) -> None:
        self._backends: Dict[str, BackendAdapter] = {}

    # ------------------------------------------------------------------ API

    def register(self, backend: BackendAdapter) -> None:
        """Register a backend adapter.

        Raises ``ValueError`` if a backend with the same name is already
        registered.
        """
        name = backend.name
        if name in self._backends:
            raise ValueError(
                f"Backend {name!r} is already registered. "
                f"Unregister it first or use a different name."
            )
        self._backends[name] = backend

    def unregister(self, name: str) -> None:
        """Remove a backend by name.  No-op if not registered."""
        self._backends.pop(name, None)

    def get(self, name: str) -> Optional[BackendAdapter]:
        """Look up a backend by its unique name."""
        return self._backends.get(name)

    def require(self, name: str) -> BackendAdapter:
        """Look up a backend; raise ``KeyError`` if not found."""
        backend = self._backends.get(name)
        if backend is None:
            available = ", ".join(sorted(self._backends)) or "(none)"
            raise KeyError(
                f"Backend {name!r} not found. Available: {available}"
            )
        return backend

    def find(
        self,
        framework: Optional[str] = None,
        renderer: Optional[str] = None,
        styling_system: Optional[str] = None,
    ) -> List[BackendAdapter]:
        """Find backends matching any of the given criteria."""
        results: List[BackendAdapter] = []
        for backend in self._backends.values():
            caps = backend.capabilities
            if framework and caps.framework != framework:
                continue
            if renderer and caps.renderer != renderer:
                continue
            if styling_system and caps.styling_system != styling_system:
                continue
            results.append(backend)
        return results

    def list(self) -> List[BackendAdapter]:
        """Return all registered backends, sorted by name."""
        return sorted(self._backends.values(), key=lambda b: b.name)

    def names(self) -> List[str]:
        """Return all registered backend names, sorted."""
        return sorted(self._backends.keys())

    def capabilities_report(self) -> Dict[str, Any]:
        """Generate a capabilities report for all registered backends."""
        return {
            name: backend.capabilities.to_dict()
            for name, backend in sorted(self._backends.items())
        }

    def preflight(
        self,
        name: str,
        document: Any,
        layout_plan: Any,
    ) -> List[FidelityLoss]:
        """Run a pre-flight check for a specific backend against a design.

        Returns the list of fidelity losses the backend would incur.
        """
        backend = self.require(name)
        return backend.preflight(document, layout_plan)

    # --------------------------------------------------------- auto-discovery

    def discover_builtins(self) -> None:
        """Register all built-in backends.

        Imports each backend module and registers its adapter.  Backends
        that fail to import (e.g. missing optional dependencies) are
        silently skipped.
        """
        # Import each backend's adapter class and register it
        builtin_modules = [
            ("html_css", "HtmlCssBackend"),
            ("react_tailwind", "ReactTailwindBackend"),
            ("vue", "VueBackend"),
            ("svelte", "SvelteBackend"),
            ("swiftui", "SwiftUIBackend"),
            ("flutter", "FlutterBackend"),
        ]
        for module_name, class_name in builtin_modules:
            try:
                mod = __import__(
                    f"figmaforge.backends.{module_name}",
                    fromlist=[class_name],
                )
                cls: Type[BackendAdapter] = getattr(mod, class_name)
                instance = cls()
                if instance.name not in self._backends:
                    self._backends[instance.name] = instance
            except (ImportError, AttributeError):
                # Backend not available — skip silently
                pass


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_registry: Optional[BackendRegistry] = None


def get_registry() -> BackendRegistry:
    """Return the default global backend registry (created on first call)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = BackendRegistry()
        _default_registry.discover_builtins()
    return _default_registry


def reset_registry() -> None:
    """Reset the global registry (useful for testing)."""
    global _default_registry
    _default_registry = None
