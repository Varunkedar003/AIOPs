from typing import Optional


def normalize_resource_id(resource_id: Optional[str]) -> Optional[str]:
    """Normalize a resource ID to the short topology ID used in topology.json.

    Handles full Azure ARM paths (e.g. /subscriptions/.../managedClusters/aks-prod-cluster-001)
    and returns the short name (e.g. aks-prod-cluster-001).
    """
    if not resource_id:
        return None

    resource_id = resource_id.strip()
    if "/" in resource_id:
        return resource_id.rstrip("/").split("/")[-1]
    return resource_id


def resource_ids_match(id_a: Optional[str], id_b: Optional[str]) -> bool:
    """Check if two resource IDs refer to the same resource."""
    if not id_a or not id_b:
        return False
    return normalize_resource_id(id_a) == normalize_resource_id(id_b)
