import streamlit as st

from dashboard.metrics import render_metrics_dashboard, render_subscription_health_overview
from dashboard.cost import render_cost_analysis

# The subscription-wide health/alerts overview shown when no resource is selected makes 2 live
# Azure calls per sampled resource (see ResourceService.get_subscription_health_overview) - an
# uncapped, subscription-size sweep is what made this tab sit on a spinner for minutes with no
# output. Capped to a fast, still-genuinely-useful sample for this automatic/no-selection view;
# the underlying method still supports a full sweep (limit=None) for any caller that wants one.
_HEALTH_OVERVIEW_SAMPLE_LIMIT = 40


def render_finops() -> None:
    st.markdown("## FinOps")

    resource_service = st.session_state.resource_service
    selected_resource_id = st.session_state.get("selected_resource_id")

    resource_name = None
    if selected_resource_id:
        resource_details = resource_service.get_resource_details(selected_resource_id)
        resource_name = resource_details.get("name") if resource_details else selected_resource_id

    tab_cost, tab_utilization = st.tabs(["Cost", "Utilization"])

    with tab_cost:
        # Subscription-wide Cost Analysis (Azure Cost Management) - independent of resource
        # selection and of the Utilization tab below, which is backed by Azure Monitor only.
        render_cost_analysis(resource_service)

    with tab_utilization:
        if selected_resource_id:
            render_metrics_dashboard(resource_service, selected_resource_id, resource_name)
        else:
            with st.spinner("Sampling resource health across the subscription..."):
                overview = resource_service.get_subscription_health_overview(limit=_HEALTH_OVERVIEW_SAMPLE_LIMIT)
            render_subscription_health_overview(resource_service, overview=overview)
