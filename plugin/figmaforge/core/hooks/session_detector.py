#!/usr/bin/env python3
"""
SessionStart Detector Hook
Runs the repository detector and injects concise additional context only when actionable evidence exists.
"""

import sys
import json
from pathlib import Path

# Add plugin core to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from detector import RepositoryDetector


def main():
    """Run the detector and output context if applicable."""
    # Detect repository root (parent of plugin/figmaforge)
    plugin_root = Path(__file__).parent.parent.parent
    repo_root = plugin_root.parent

    if not repo_root.exists():
        # Not in a repository context
        sys.exit(0)

    try:
        detector = RepositoryDetector(repo_root)
        detection = detector.detect()

        # Only inject context if there's actionable evidence
        if detection["status"] == "classified" and detection["confidence"] >= 0.3:
            # Inject concise additional context
            context = {
                "language": detection.get("languages", []),
                "framework": detection.get("frameworks", []),
                "package_manager": detection.get("package_managers", []),
                "confidence": detection.get("confidence", 0),
            }

            # Output as JSON on stdout
            print(json.dumps(context, indent=2))

        # Exit 0 for empty repo or nonblocking failures
        sys.exit(0)

    except FileNotFoundError as e:
        # Nonblocking: detector couldn't find repo
        sys.stderr.write(f"Detector error: {e}\n")
        sys.exit(1)

    except Exception as e:
        # Critical error
        sys.stderr.write(f"Fatal detector error: {e}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
