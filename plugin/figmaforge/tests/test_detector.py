#!/usr/bin/env python3
"""
Basic detector tests
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.detector import RepositoryDetector, DETECTION_PATTERNS


def test_detector_init():
    """Test detector initialization."""
    try:
        detector = RepositoryDetector(Path.cwd())
        print("✓ Detector initialized successfully")
        return True
    except Exception as e:
        print(f"✗ Detector initialization failed: {e}")
        return False


def test_detection_patterns_exist():
    """Test that detection patterns are defined."""
    if not DETECTION_PATTERNS:
        print("✗ No detection patterns defined")
        return False

    if "javascript" not in DETECTION_PATTERNS:
        print("✗ JavaScript detection patterns missing")
        return False

    print(f"✓ Detection patterns defined ({len(DETECTION_PATTERNS)} languages)")
    return True


def main():
    """Run all tests."""
    print("Running detector tests...")
    print()

    results = []

    results.append(test_detection_patterns_exist())
    results.append(test_detector_init())

    passed = sum(results)
    total = len(results)

    print()
    print(f"Results: {passed}/{total} tests passed")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
