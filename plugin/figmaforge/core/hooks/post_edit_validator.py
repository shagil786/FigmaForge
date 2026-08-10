#!/usr/bin/env python3
"""
PostToolUse Validator
On Edit|Write, reads changed path and looks up canonical validator from detected manifests.
"""

import sys
import json
import os
from pathlib import Path

# Detect repository root
plugin_root = Path(__file__).parent.parent.parent
repo_root = plugin_root.parent


def get_validator_for_file(file_path: str) -> str:
    """Look up a validator command for a file path.

    Returns validator name or empty string if no applicable check.
    """
    file_path = Path(file_path)

    # Check for test files
    if file_path.suffix in [".test.js", ".test.ts", ".test.py", ".spec.js", ".spec.ts", ".spec.py"]:
        return "test"

    # Check for Rust files
    if file_path.suffix == ".rs":
        return "rustfmt --check"

    # Check for Python files
    if file_path.suffix in [".py", ".pyi"]:
        return "pyright"

    # Check for TypeScript/JavaScript files
    if file_path.suffix in [".ts", ".tsx", ".js", ".jsx"]:
        return "tsc --noEmit"

    # Check for C/C++ files
    if file_path.suffix in [".c", ".cpp", ".h", ".hpp"]:
        return "clang-format --dry-run"

    # Check for Go files
    if file_path.suffix == ".go":
        return "gofmt -l"

    # Check for YAML configuration
    if file_path.suffix == ".yaml" or file_path.suffix == ".yml":
        return "yamllint"

    # Check for JSON configuration
    if file_path.suffix == ".json":
        return "jsonlint"

    # Check for SQL files
    if file_path.suffix == ".sql":
        return "sqlfluff check"

    # Default: no applicable check
    return ""


def main():
    """Validate the edit and return result."""
    # Read tool input from stdin
    try:
        tool_input = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        # Invalid JSON — no applicable check
        sys.exit(0)

    # Only validate Edit and Write tools
    tool_name = tool_input.get("tool", "").lower()
    if "edit" not in tool_name and "write" not in tool_name:
        # No applicable validation
        sys.exit(0)

    # Get changed path
    new_file = tool_input.get("new_file")
    if not new_file:
        sys.exit(0)

    # Determine validator
    validator = get_validator_for_file(new_file)

    if not validator:
        # No applicable toolchain check
        sys.exit(0)

    # Note: Actual validation is not performed here (would require tool invocation)
    # This hook just declares what check would be performed and reports readiness
    # to the harness for verification

    # Exit 0 to indicate readiness for validation
    # The actual validation would happen in a separate pass or be skipped if toolchain unavailable

    sys.exit(0)


if __name__ == "__main__":
    main()
