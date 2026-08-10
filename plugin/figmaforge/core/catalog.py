"""
Catalog: Load and manage the 100-role catalog from roles.json
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Any


class Catalog:
    """Load and query the 100-role catalog."""

    def __init__(self, catalog_path: str | None = None):
        """Initialize catalog from roles.json file.

        Args:
            catalog_path: Path to roles.json. If None, uses default location.
        """
        if catalog_path is None:
            # Default location relative to plugin root
            plugin_root = Path(__file__).parent.parent
            catalog_path = plugin_root / "catalog" / "roles.json"

        self.catalog_path = Path(catalog_path)
        self._data: Dict[str, Any] = {}
        self._roles: Dict[str, Any] = {}
        self._domains: Dict[str, Any] = {}

        self._load()

    def _load(self) -> None:
        """Load catalog from roles.json."""
        if not self.catalog_path.exists():
            raise FileNotFoundError(f"Catalog not found: {self.catalog_path}")

        with open(self.catalog_path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

        # Extract roles and domains
        self._domains = self._data.get("domains", {})
        self._roles = {}
        for domain_name, domain_data in self._domains.items():
            for role_data in domain_data.get("roles", []):
                self._roles[role_data["id"]] = role_data

    def get_role(self, role_id: str) -> Dict[str, Any] | None:
        """Get a specific role by ID.

        Args:
            role_id: The role ID to look up.

        Returns:
            Role dictionary or None if not found.
        """
        return self._roles.get(role_id)

    def get_roles_by_domain(self, domain_name: str) -> List[Dict[str, Any]]:
        """Get all roles in a domain.

        Args:
            domain_name: Domain name (e.g. "discovery", "architecture").

        Returns:
            List of role dictionaries.
        """
        domain = self._domains.get(domain_name)
        return domain.get("roles", []) if domain else []

    def get_all_roles(self) -> List[Dict[str, Any]]:
        """Get all 100 roles flattened by domain.

        Returns:
            List of all role dictionaries.
        """
        all_roles = []
        for domain_name, domain_data in self._domains.items():
            all_roles.extend(domain_data.get("roles", []))
        return all_roles

    def get_domains(self) -> List[str]:
        """Get list of all domain names.

        Returns:
            List of domain names.
        """
        return list(self._domains.keys())

    def get_trigger_roles(self, trigger: str) -> List[Dict[str, Any]]:
        """Get roles that match a trigger keyword.

        Args:
            trigger: Trigger keyword to match.

        Returns:
            List of matching roles.
        """
        trigger_lower = trigger.lower()
        matching = []
        for role in self._roles.values():
            triggers = [t.lower() for t in role.get("triggers", [])]
            if any(t in trigger_lower for t in triggers):
                matching.append(role)
        return matching

    def get_domain_role_count(self) -> Dict[str, int]:
        """Get role count per domain.

        Returns:
            Dictionary of domain name to role count.
        """
        return {
            domain_name: len(domain.get("roles", []))
            for domain_name, domain in self._domains.items()
        }
