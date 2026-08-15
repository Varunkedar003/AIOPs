import streamlit as st

from dashboard.metrics import render_metrics_dashboard, render_subscription_health_overview
from dashboard.alerts import render_alerts_dashboard
from dashboard.logs import render_logs_dashboard

# See dashboard/pages/finops.py's identical constant - same reasoning: the subscription-wide
# health/alerts overview makes 2 live Azure calls per sampled resource, and an uncapped sweep
# is what made this page sit on a spinner for minutes with no output for the no-selection view.
_HEALTH_OVERVIEW_SAMPLE_LIMIT = 40


def render_monitoring() -> None:
    st.markdown("## Monitoring & Observability")

    resource_service = st.session_state.resource_service
    selected_resource_id = st.session_state.get("selected_resource_id")

    resource_name = None
    subscription_overview = None
    if selected_resource_id:
        resource_details = resource_service.get_resource_details(selected_resource_id)
        resource_name = resource_details.get("name") if resource_details else selected_resource_id
    else:
        st.caption(
            "No resource selected — showing a subscription-wide health overview. Select a "
            "resource from the sidebar, Infrastructure Explorer, or Resource Workspace for its "
            "full metrics, alerts, and logs."
        )
        with st.spinner("Sampling resource health across the subscription..."):
            subscription_overview = resource_service.get_subscription_health_overview(limit=_HEALTH_OVERVIEW_SAMPLE_LIMIT)

    tab_metrics, tab_alerts, tab_logs = st.tabs(["Metrics", "Alerts", "Logs"])

    with tab_metrics:
        if selected_resource_id:
            render_metrics_dashboard(resource_service, selected_resource_id, resource_name)
        else:
            render_subscription_health_overview(resource_service, overview=subscription_overview)

    with tab_alerts:
        if selected_resource_id:
            render_alerts_dashboard(resource_service, selected_resource_id, resource_name)
        else:
            render_subscription_health_overview(resource_service, overview=subscription_overview)

    with tab_logs:
        if selected_resource_id:
            render_logs_dashboard(resource_service, selected_resource_id, resource_name)
        else:
            st.markdown("*Log tailing is scoped to a single resource — select one to view its logs.*")
