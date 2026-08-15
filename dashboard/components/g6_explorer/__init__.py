from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).parent

_g6_explorer_component = components.declare_component(
    "g6_explorer",
    path=str(_COMPONENT_DIR),
)


def g6_explorer(
    resources: List[Dict[str, Any]],
    subscription_label: str = "Subscription",
    selected_id: Optional[str] = None,
    key: Optional[str] = None,
    height: int = 760,
    reset_token: int = 0,
) -> Optional[str]:
    """Render the Home Infrastructure Explorer as an AntV G6 hierarchical graph:
    Subscription -> Resource Groups -> Resource Types -> Individual Resources.

    Pure containment tree (no dependency edges) - the component derives the hierarchy
    client-side from each resource's `resource_group`/`type` fields. Resource Type groups
    start collapsed (behind a resource-count badge) so the initial render stays cheap at
    800-1000+ resources; Subscription/Resource Group levels start expanded.

    Args:
        resources: flat list of resource dicts, same shape GraphService/ResourceService
            already produce (id/resource_id, name, type, resource_group, subscription,
            region/location, health_status, monthly_cost, health_score, last_deployment).
        subscription_label: display label for the single root node.
        selected_id: id of the resource to mark selected (e.g. after a rerun).
        height: iframe height in pixels.
        reset_token: bump this (e.g. a counter in session_state) to force the component back to
            its pristine initial view - clears expand/collapse state, manually-dragged positions,
            and re-runs the automatic layout - even though `resources` itself hasn't structurally
            changed (same length/first id) and would otherwise be treated as the same dataset.
            Used by the Refresh button so a refresh also undoes any expand/drag exploration
            instead of silently re-fetching underneath the user's current view.

    Returns:
        The id of the resource the user clicked on, or None if nothing was clicked since
        the last render - same contract as dashboard/components/cytoscape_topology.
    """
    return _g6_explorer_component(
        resources=resources,
        subscription_label=subscription_label,
        selected_id=selected_id,
        height=height,
        reset_token=reset_token,
        key=key,
        default=None,
    )
