#!/usr/bin/env python3
"""
PostToolUse Validator
On Edit|Write, reads changed path and looks up canonical validator from detected manifests.
"""

import sys
import json
import os
import shutil
import subprocess
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

    # Check if the validator binary is available on PATH
    validator_binary = validator.split()[0]
    if not shutil.which(validator_binary):
        # Toolchain not installed — report but don't block
        result = {
            "status": "skipped",
            "validator": validator,
            "file": new_file,
            "reason": f"{validator_binary} not found on PATH",
        }
        print(json.dumps(result))
        sys.exit(0)

    # Execute the validator against the changed file
    try:
        proc = subprocess.run(
            [*validator.split(), new_file],
            capture_output=True,
            text=True,
            timeout=30,
        )
        result = {
            "status": "passed" if proc.returncode == 0 else "failed",
            "validator": validator,
            "file": new_file,
            "exit_code": proc.returncode,
        }
        if proc.stdout:
            result["stdout"] = proc.stdout[:2000]
        if proc.stderr:
            result["stderr"] = proc.stderr[:2000]
        print(json.dumps(result))
        sys.exit(0 if proc.returncode == 0 else 1)
    except subprocess.TimeoutExpired:
        result = {
            "status": "timeout",
            "validator": validator,
            "file": new_file,
            "reason": "validation timed out (30s)",
        }
        print(json.dumps(result))
        sys.exit(0)
    except OSError as exc:
        result = {
            "status": "error",
            "validator": validator,
            "file": new_file,
            "reason": str(exc),
        }
        print(json.dumps(result))
        sys.exit(0)


if __name__ == "__main__":
    main()
