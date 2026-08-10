"""
Fixture loader for the Figma ingestion layer.

Loads saved Figma API responses from the ``fixtures/figma`` directory so the
normalizer, asset handler, and tests can run without a live Figma token.

Fixtures are plain JSON files named after the endpoint they represent, e.g.
``file.json`` (full-file response) and ``images.json`` (image-url response).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .figma_errors import FigmaResponseError

logger = logging.getLogger("figmaforge.figma_fixtures")

DEFAULT_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "figma"


class FixtureLoader:
    """Load and validate Figma API response fixtures."""

    def __init__(self, fixture_dir: Optional[Path] = None):
        self.fixture_dir = Path(fixture_dir or DEFAULT_FIXTURE_DIR)

    def load(self, name: str) -> Dict[str, Any]:
        """Load a fixture by name (without the ``.json`` suffix)."""
        path = self.fixture_dir / f"{name}.json"
        if not path.exists():
            raise FigmaResponseError(f"Fixture not found: {name!r} ({path})")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise FigmaResponseError(f"Fixture {name!r} is not valid JSON: {exc}")
        if not isinstance(data, dict):
            raise FigmaResponseError(f"Fixture {name!r} must be a JSON object.")
        return data

    def load_file(self, name: str = "file") -> Dict[str, Any]:
        """Load a full-file response fixture."""
        return self.load(name)

    def list_fixtures(self) -> List[str]:
        """List available fixture names (sans ``.json``)."""
        if not self.fixture_dir.exists():
            return []
        return sorted(p.stem for p in self.fixture_dir.glob("*.json") if p.is_file())
