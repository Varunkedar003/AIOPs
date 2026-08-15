from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).parent

_cytoscape_topology_component = components.declare_component(
    "cytoscape_topology",
    path=str(_COMPONENT_DIR),
)


def cytoscape_topology(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    selected_id: Optional[str] = None,
    key: Optional[str] = None,
    node_states: Optional[Dict[str, str]] = None,
    edge_styles: Optional[Dict[str, str]] = None,
    investigation_events: Optional[List[Dict[str, Any]]] = None,
    height: int = 680,
    view_mode: str = "explorer",
) -> Optional[str]:
    """Render the topology/resource graph as an interactive, AI-investigation-aware
    Cytoscape.js graph (Task 23).

    Args:
        nodes: same shape as before (id/name/type/health_status/...), optionally carrying
            enrichment fields the side panel/tooltips can show when known: resource_group,
            region, subscription, health_score, alert_count, monthly_cost, last_deployment.
        edges: same shape as before (source/target/relationship).
        selected_id: id of the node to mark selected.
        node_states: {node_id: one of "healthy"|"investigating"|"warning"|"root_cause"|
            "dependency"|"not_involved"|"offline"} - see dashboard/graph_investigation.py.
            Nodes not present default to a health-derived state.
        edge_styles: {"source->target": one of "normal"|"active_investigation"|
            "root_cause_path"|"impact_path"}. Edges not present default to "normal".
        investigation_events: [{timestamp, node_id, stage, label}, ...] for the playback
            control, oldest first (see dashboard/graph_investigation.record_event).
        height: iframe height in pixels.
        view_mode: "explorer" for the full subscription topology (hierarchical layout,
            lazy-loaded resource groups) or "workspace" for the depth-1 Resource Workspace
            graph (radial layout, kept centered on `selected_id`).

    Returns:
        The id of the node the user clicked on, or None if nothing was clicked since the
        last render (unchanged contract from before this task).
    """
    return _cytoscape_topology_component(
        nodes=nodes,
        edges=edges,
        selected_id=selected_id,
        node_states=node_states or {},
        edge_styles=edge_styles or {},
        investigation_events=investigation_events or [],
        height=height,
        view_mode=view_mode,
        key=key,
        default=None,
    )
