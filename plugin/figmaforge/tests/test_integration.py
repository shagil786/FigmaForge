#!/usr/bin/env python3
"""
Integration test for detector, catalog, and router.
"""

import sys
from pathlib import Path

# Add plugin core to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / "core"))

from detector import RepositoryDetector
from catalog import Catalog


def test_integration():
    """Test detector, catalog, and router integration."""
    print("Running integration tests...")
    print()

    # Test 1: Detector
    try:
        detector = RepositoryDetector(Path.cwd().parent.parent)
        result = detector.detect()
        print(f"✓ Detector ran successfully")
        print(f"  Status: {result['status']}")
        print(f"  Confidence: {result['confidence']}")
        print(f"  Languages: {result.get('languages', [])}")
        print()
    except Exception as e:
        print(f"✗ Detector failed: {e}")
        return False

    # Test 2: Catalog
    try:
        catalog = Catalog()
        roles = catalog.get_all_roles()
        domains = catalog.get_domains()
        print(f"✓ Catalog loaded successfully")
        print(f"  Total roles: {len(roles)}")
        print(f"  Domains: {len(domains)}")
        print(f"  Domain names: {', '.join(domains[:5])}...")
        print()
    except Exception as e:
        print(f"✗ Catalog failed: {e}")
        return False

    # Test 3: Catalog queries
    try:
        discovery_roles = catalog.get_roles_by_domain("discovery")
        print(f"✓ Catalog queries work")
        print(f"  Discovery domain has {len(discovery_roles)} roles")
        if discovery_roles:
            print(f"  First role: {discovery_roles[0]['title']}")
        print()
    except Exception as e:
        print(f"✗ Catalog query failed: {e}")
        return False

    print("All integration tests passed!")
    return True


if __name__ == "__main__":
    success = test_integration()
    sys.exit(0 if success else 1)
