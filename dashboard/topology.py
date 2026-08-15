import streamlit as st
from typing import Optional
from utils.resource_id import resource_ids_match, normalize_resource_id
from services.graph_service import GraphService
from dashboard.components.cytoscape_topology import cytoscape_topology
from dashboard.graph_investigation import build_edge_styles, build_node_states, get_events
from .graph_visualization import render_graph_controls


def render_topology() -> Optional[str]:
    """Render the Infrastructure Topology center panel with context-based navigation
    
    Returns:
        ID of the clicked resource from graph, or None
    """
    # Get selected resource from session state
    selected_resource_id = st.session_state.get('selected_resource_id')
    
    # If no resource selected, show placeholder
    if not selected_resource_id:
        st.markdown("### Infrastructure Topology")
        st.markdown("---")
        
        # Center the placeholder message
        st.markdown(
            """
            <div style='text-align: center; padding: 50px;'>
                <h3 style='color: #666;'>No resource selected.</h3>
                <p style='color: #999;'>Select a resource from the Azure Subscription Explorer to view its dependencies.</p>
            </div>
            """,
            unsafe_allow_html=True
        )
        
        st.markdown("---")
        st.markdown("*The graph will display the selected resource and its direct dependencies.*")
        return None
    
    # Render graph controls
    control_action = render_graph_controls(selected_resource_id)
    
    # Handle navigation controls
    if control_action:
        navigation_history = st.session_state.get('navigation_history', [])
        history_index = st.session_state.get('history_index', -1)
        
        if control_action == "back" and history_index > 0:
            history_index -= 1
            selected_resource_id = navigation_history[history_index]
            st.session_state.selected_resource_id = selected_resource_id
            st.session_state.history_index = history_index
            st.rerun()
        
        elif control_action == "forward" and history_index < len(navigation_history) - 1:
            history_index += 1
            selected_resource_id = navigation_history[history_index]
            st.session_state.selected_resource_id = selected_resource_id
            st.session_state.history_index = history_index
            st.rerun()
        
        elif control_action == "reset":
            st.session_state.selected_resource_id = None
            st.session_state.navigation_history = []
            st.session_state.history_index = -1
            st.rerun()
    
    # Render the interactive context-based graph
    clicked_resource_id = _render_resource_graph(selected_resource_id)

    # Handle graph node click
    if clicked_resource_id and not resource_ids_match(clicked_resource_id, selected_resource_id):
        st.session_state.selected_resource_id = clicked_resource_id
        
        # Update navigation history
        navigation_history = st.session_state.get('navigation_history', [])
        history_index = st.session_state.get('history_index', -1)
        
        # If we're not at the end of history, truncate forward history
        if history_index < len(navigation_history) - 1:
            navigation_history = navigation_history[:history_index + 1]
        
        navigation_history.append(clicked_resource_id)
        st.session_state.navigation_history = navigation_history
        st.session_state.history_index = len(navigation_history) - 1
        
        st.rerun()

    return clicked_resource_id


def _render_resource_graph(selected_resource_id: str) -> Optional[str]:
    """Render the resource's depth-1 dependency graph using Cytoscape.js, with AI
    investigation node states/edge styles layered on top (Task 23 §2, §3) — the graph
    data itself still comes from GraphService.build_resource_graph, unchanged.
    """
    graph_service = GraphService(azure_provider=st.session_state.resource_service.azure_provider)
    graph_data = graph_service.build_resource_graph(selected_resource_id)

    nodes = graph_data.get("nodes", [])
    center_id = graph_data.get("center_id")

    if not nodes:
        st.markdown("*No dependencies found for this resource.*")
        return None

    if center_id:
        st.caption(f"Centered on **{center_id}** with {len(nodes) - 1} connected resource(s)")

    edges = graph_data.get("edges", [])
    dependency_ids = graph_service.get_connected_nodes(selected_resource_id)

    return cytoscape_topology(
        nodes=nodes,
        edges=edges,
        selected_id=center_id or normalize_resource_id(selected_resource_id),
        node_states=build_node_states(nodes, dependency_ids=dependency_ids),
        edge_styles=build_edge_styles(edges, dependency_ids=dependency_ids),
        investigation_events=get_events(),
        view_mode="workspace",
        key="resource_workspace_topology",
    )
