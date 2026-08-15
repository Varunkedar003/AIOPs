import streamlit as st
from typing import Any, Dict, List, Optional
from utils.resource_id import normalize_resource_id


def render_topology_graph(selected_resource_id: Optional[str] = None) -> Optional[str]:
    """Render interactive infrastructure topology graph with context-based navigation
    
    Args:
        selected_resource_id: ID of the currently selected resource
        
    Returns:
        ID of the clicked resource, or None if no click occurred
    """
    st.markdown("### Infrastructure Topology")
    
    try:
        from streamlit_agraph import agraph, Node, Edge, Config
    except ImportError:
        st.error("streamlit-agraph is not installed. Please run: pip install streamlit-agraph")
        st.markdown("*Install the dependency to enable graph visualization.*")
        return None
    
    # Import graph service (assumes path is set by app.py)
    from services.graph_service import GraphService
    
    # Initialize graph service
    graph_service = GraphService()
    
    # Get context-based graph data (depth 1 only)
    if selected_resource_id:
        graph_data = graph_service.build_resource_graph(selected_resource_id)
    else:
        # Return empty graph if no resource selected
        return None
    
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    center_id = graph_data.get("center_id")
    positions = graph_data.get("positions", {})
    topology_selected_id = normalize_resource_id(selected_resource_id)

    if not nodes:
        st.markdown("*No dependencies found for this resource.*")
        return None

    if center_id:
        st.caption(f"Centered on **{center_id}** with {len(nodes) - 1} connected resource(s)")
    
    # Convert to streamlit-agraph format
    agraph_nodes = []
    agraph_edges = []
    
    for node in nodes:
        node_id = node.get("id") or node.get("resource_id", "")
        is_selected = node_id == center_id or node_id == topology_selected_id
        is_center = node_id == center_id

        formatted_node = graph_service.format_node_for_display(node, is_selected=is_selected)
        node_position = positions.get(node_id, (0, 0))

        agraph_node = Node(
            id=formatted_node["id"],
            label=formatted_node["label"],
            size=formatted_node["size"] + (8 if is_center else 0),
            color=formatted_node["color"],
            title=f"{formatted_node['type']}\nStatus: {formatted_node['health_status']}",
            shape="box",
            x=node_position[0],
            y=node_position[1],
            fixed=True,
        )
        agraph_nodes.append(agraph_node)
    
    for edge in edges:
        formatted_edge = graph_service.format_edge_for_display(edge)
        
        # Determine line style
        if formatted_edge["style"] == "dashed":
            style = {"stroke": "dashed"}
        elif formatted_edge["style"] == "dotted":
            style = {"stroke": "dotted"}
        else:
            style = {"stroke": "solid"}
        
        agraph_edge = Edge(
            source=formatted_edge["source"],
            target=formatted_edge["target"],
            label=formatted_edge["label"],
            **style
        )
        agraph_edges.append(agraph_edge)
    
    # Graph configuration for context-based view
    config = Config(
        width=800,
        height=600,
        directed=True,
        physics=False,
        hierarchical=False,
        nodeHighlightBehavior=True,
        highlightColor="#F39C12",
        collapsible=True,
        nodeSpacing=150,
        edgeLength=200,
    )
    
    # Render the graph
    return_value = agraph(
        nodes=agraph_nodes,
        edges=agraph_edges,
        config=config
    )
    
    # Handle node click - streamlit-agraph's selectNode handler calls
    # Streamlit.setComponentValue(e.nodes[0]), so the return value is the
    # clicked node's id itself, not a {"nodes": [...]} dict.
    if return_value:
        return return_value

    return None


def render_graph_controls(selected_resource_id: Optional[str]) -> Optional[str]:
    """Render graph control buttons
    
    Args:
        selected_resource_id: ID of the currently selected resource
        
    Returns:
        Action to perform: 'back', 'forward', 'reset', or None
    """
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("⬅️ Back", key="graph_back"):
            return "back"
    
    with col2:
        if st.button("🔄 Reset View", key="graph_reset"):
            return "reset"
    
    with col3:
        if st.button("➡️ Forward", key="graph_forward"):
            return "forward"
    
    return None


def render_graph_legend() -> None:
    """Render graph legend explaining node types and colors"""
    with st.expander("Graph Legend"):
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Resource Types:**")
            legend_items = [
                ("🏢 Subscription", "#2E86AB"),
                ("📁 Resource Group", "#A23B72"),
                ("☸️ AKS Cluster", "#F18F01"),
                ("🌐 App Service", "#C73E1D"),
                ("🚀 Deployment", "#3B1F2B"),
                ("🗄️ SQL Database", "#06A77D"),
                ("⚡ Redis", "#59C3C3"),
                ("💾 Storage Account", "#6C757D")
            ]
            
            for item, color in legend_items:
                st.markdown(f"<span style='color:{color}'>■</span> {item}", unsafe_allow_html=True)
        
        with col2:
            st.markdown("**More Types:**")
            more_items = [
                ("🔐 Key Vault", "#991B1B"),
                ("📊 Application Insights", "#7B2D8E"),
                ("📋 Log Analytics", "#1F77B4"),
                ("🦊 GitLab Project", "#FC6D26"),
                ("📦 Pod", "#28A745"),
                ("🔗 Service", "#17A2B8"),
                ("🌍 Ingress", "#6610F2")
            ]
            
            for item, color in more_items:
                st.markdown(f"<span style='color:{color}'>■</span> {item}", unsafe_allow_html=True)
        
        st.markdown("**Selection Colors:**")
        st.markdown("<span style='color:#FF6B6B'>■</span> Selected Resource", unsafe_allow_html=True)

        st.markdown("**Health Indicators:**")
        st.markdown("🔴 Critical/Error &nbsp;&nbsp; 🟡 Warning/Degraded &nbsp;&nbsp; (no badge = healthy)", unsafe_allow_html=True)

        st.markdown("**Edge Styles:**")
        st.markdown("──────── Contains/Hosts (Solid)")
        st.markdown("─────── Connects To (Dashed)")
        st.markdown("······· Monitored By (Dotted)")


def render_graph_summary(graph_data: Dict[str, Any]) -> None:
    """Render summary statistics about the graph
    
    Args:
        graph_data: Dictionary containing nodes and edges
    """
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    
    # Count by type
    type_counts = {}
    for node in nodes:
        node_type = node.get("type", "unknown")
        type_counts[node_type] = type_counts.get(node_type, 0) + 1
    
    # Count by health status
    health_counts = {}
    for node in nodes:
        health = node.get("health_status", "Unknown")
        health_counts[health] = health_counts.get(health, 0) + 1
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Total Resources", len(nodes))
    
    with col2:
        st.metric("Connections", len(edges))
    
    with col3:
        unhealthy = health_counts.get("Critical", 0) + health_counts.get("Warning", 0)
        st.metric("Issues", unhealthy)
