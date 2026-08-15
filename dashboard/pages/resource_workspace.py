import streamlit as st

from dashboard.ai_side_panel import render_ai_side_panel
from dashboard.topology import render_topology
from dashboard.details import render_resource_details
from dashboard.metrics import render_metrics_dashboard
from dashboard.cost import render_cost_dashboard
from dashboard.alerts import render_alerts_dashboard
from dashboard.logs import render_logs_dashboard


def render_resource_workspace() -> None:
    """Focused workspace for a single resource: its depth-1 graph plus detail tabs."""
    st.markdown("## Resource Workspace")

    if not st.session_state.get("selected_resource_id"):
        st.info("No resource selected. Pick one from the Infrastructure Explorer or the sidebar explorer.")

    col_graph, col_tabs = st.columns([3, 2])

    with col_graph:
        render_topology()

    with col_tabs:
        selected_resource_id = st.session_state.get("selected_resource_id")
        if not selected_resource_id:
            return

        resource_service = st.session_state.resource_service
        with st.spinner("Loading resource details..."):
            resource_details = resource_service.get_resource_details(selected_resource_id)
        resource_name = resource_details.get("name") if resource_details else selected_resource_id

        tab_ai, tab_overview, tab_metrics, tab_cost, tab_alerts, tab_logs = st.tabs(
            ["AI Investigation", "Overview", "Metrics", "Cost", "Alerts", "Logs"]
        )

        with tab_ai:
            render_ai_side_panel(resource_service, selected_resource_id, compact=True)

        with tab_overview:
            render_resource_details(resource_details)

        with tab_metrics:
            render_metrics_dashboard(resource_service, selected_resource_id, resource_name)

        with tab_cost:
            render_cost_dashboard(resource_service, selected_resource_id, resource_name)

        with tab_alerts:
            render_alerts_dashboard(resource_service, selected_resource_id, resource_name)

        with tab_logs:
            render_logs_dashboard(resource_service, selected_resource_id, resource_name)
