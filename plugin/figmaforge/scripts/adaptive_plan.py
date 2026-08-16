#!/usr/bin/env python3
"""
Adaptive preflight plan CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.catalog import Catalog  # noqa: E402
from core.detector import RepositoryDetector  # noqa: E402
from core.router import Router  # noqa: E402


class _CliError(Exception):
    """A user-facing CLI failure with a fixed exit code."""

    def __init__(self, exit_code: int, message: str):
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


class _Parser(argparse.ArgumentParser):
    """Parser that reports usage problems as structured CLI errors."""

    def error(self, message: str) -> None:
        raise _CliError(2, message)


def _emit(payload: Dict[str, Any]) -> None:
    """Print one deterministic JSON line to stdout."""
    print(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _emit_error(message: str) -> None:
    """Print a structured error payload to stderr."""
    print(json.dumps({"error": message}, sort_keys=True, ensure_ascii=False), file=sys.stderr)


def _validate_inputs(root: Path, request: str) -> None:
    """Validate required inputs before running detection."""
    if not str(request).strip():
        raise _CliError(2, "--request must be a non-empty string")
    if not root.exists():
        raise _CliError(2, f"repository root not found: {root}")
    if not root.is_dir():
        raise _CliError(2, f"repository root is not a directory: {root}")


def build_plan(root: Path, request: str, installed_capabilities: list[str]) -> dict:
    """Build the adaptive plan payload."""
    _validate_inputs(root, request)

    detector = RepositoryDetector(root)
    catalog = Catalog()
    router = Router(catalog, detector)
    detection = detector.detect()
    route = router.route(request, installed_capabilities=installed_capabilities)
    return {
        "schema_version": 1,
        "request": request,
        "root": str(root.resolve()),
        "detection": detection,
        "route": asdict(route),
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = _Parser(prog="adaptive_plan.py")
    parser.add_argument("--root", required=True, help="Repository root directory")
    parser.add_argument("--request", required=True, help="User request to plan for")
    parser.add_argument(
        "--installed-capability",
        action="append",
        default=[],
        dest="installed_capabilities",
        help="Installed capability ref (repeatable)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        payload = build_plan(
            Path(args.root),
            args.request,
            list(args.installed_capabilities or []),
        )
        _emit(payload)
        return 0
    except _CliError as exc:
        _emit_error(exc.message)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _emit_error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
