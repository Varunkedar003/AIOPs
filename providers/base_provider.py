from typing import Any, Dict, List, Optional
from abc import ABC


class BaseProvider(ABC):
    """Common list-filtering helpers shared by all provider implementations.

    Phase 1 placeholder: methods below return empty results until Phase 2
    wires each provider up to a live Azure/GitLab client.
    """

    def _find_by_id(self, items: List[Dict[str, Any]], resource_id: str) -> Optional[Dict[str, Any]]:
        """Find an item in a list by its id field"""
        for item in items:
            if item.get('id') == resource_id or item.get('resource_id') == resource_id:
                return item
        return None

    def _find_by_name(self, items: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
        """Find an item in a list by its name field"""
        for item in items:
            if item.get('name') == name:
                return item
        return None

    def _filter_by_field(self, items: List[Dict[str, Any]], field: str, value: Any) -> List[Dict[str, Any]]:
        """Filter items by a specific field value"""
        return [item for item in items if item.get(field) == value]
