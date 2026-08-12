"""
FigmaForge Core
A technology-agnostic, adaptive, full-lifecycle Claude Code engineering platform.
"""

__version__ = "0.0.1-dev"
__author__ = "Md Shagil Nizami"

# Core modules
from .detector import RepositoryDetector
from .router import Router
from .catalog import Catalog
from .state import StateMachine

__all__ = [
    "RepositoryDetector",
    "Router",
    "Catalog",
    "StateMachine",
]

# NOTE: Backend adapters live in figmaforge.backends, not here.
# Import them via: from figmaforge.backends import get_registry, BackendAdapter
