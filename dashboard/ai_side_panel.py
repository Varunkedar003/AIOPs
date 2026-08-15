"""AI Side Panel (Task 23 §4).

Pure UI: every field here comes from calls the rest of the app already makes
(ResourceService / the shared LangGraph+CrewAI+Claude session in dashboard.chat) - nothing new
is fetched or computed outside of what Tasks 1-22 already built.
"""
from typing import Any

import streamlit as st

from dashboard.chat import render_progress_strip
from dashboard.graph_investigation import compute_health_score, investigate_node
from utils.resource_id import resource_ids_match
from workflow.router import ALL_STAGES


_TYPE_ICONS = {
    "app_service": "🌐", "app_service_plan": "📐", "aks_cluster": "☸️", "resource_group": "📁",
    "sql_database": "🗄️", "storage_account": "💾", "key_vault": "🔐", "redis": "⚡",
    "application_insights": "📊", "log_analytics": "📋", "gitlab_project": "🦊",
    "vnet": "🕸️", "nsg": "🛡️", "load_balancer": "⚖️", "public_ip": "📡",
    "container_registry": "🏗️", "virtual_machine": "🖥️",
}


def _status_badge_color(status: str) -> str:
    status_lower = (status or "").lower()
    if status_lower in ("healthy", "running", "active", "online", "success", "available", "succeeded"):
        return "green"
    if status_lower in ("warning", "degraded", "unhealthy"):
        return "orange"
    if status_lower in ("critical", "error", "failed", "stopped", "offline", "unavailable"):
        return "red"
    return "blue"


def render_ai_side_panel(resource_service: Any, resource_id: str, compact: bool = False) -> None:
    """Render the AI side panel for `resource_id`: identity fields, derived health/cost/alert
    metrics, related resources, and the live AI investigation (progress, summary, evidence),
    with an "Open Investigation" button that runs the shared LangGraph/CrewAI/Claude pipeline.

    This is the single clean, right-hand-side description of whatever node was last clicked on
    the graph - there is no separate popup near the node itself.
    """
    with st.spinner("Loading resource details..."):
        resource = resource_service.get_resource_details(resource_id)

    if not resource:
        st.info("No resource details available for this node.")
        return

    resource_name = resource.get("name", resource_id)
    resource_type = resource.get("resource_type", resource.get("type", "Unknown"))
    status = resource.get("state", resource.get("health_status", "Unknown"))
    icon = _TYPE_ICONS.get((resource_type or "").lower(), "🧭")

    with st.container(border=True):
        header_col, badge_col = st.columns([3, 1])
        with header_col:
            st.markdown(f"#### {icon} {resource_name}")
            st.caption(resource_type)
        with badge_col:
            st.markdown(f":{_status_badge_color(status)}[**{status}**]")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Resource Group**  \n{resource.get('resource_group', 'Unknown')}")
            st.markdown(f"**Subscription**  \n{resource.get('subscription', resource.get('subscription_id', 'Unknown'))}")
        with col2:
            st.markdown(f"**Region**  \n{resource.get('region', resource.get('location', 'Unknown'))}")

    alerts = resource_service.get_resource_alerts(resource_id) or []
    active_alerts = [a for a in alerts if (a.get("status") or "").lower() == "active"]
    health = resource_service.get_resource_health(resource_id) or {}
    health_score = compute_health_score(resource, health, len(active_alerts))

    cost = resource_service.get_resource_cost(resource_id)
    monthly_cost_text = "N/A"
    if cost and cost.get("monthly_cost") is not None:
        monthly_cost_text = f"${cost['monthly_cost']:,.2f} {cost.get('currency', '')}".strip()

    last_deployment = (
        resource.get("last_deployed_at")
        or resource.get("last_activity_at")
        or resource.get("updated_at")
        or resource.get("last_modified")
        or "Unknown"
    )

    with st.container(border=True):
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Health Score", f"{health_score}/100")
        m2.metric("Alert Count", len(active_alerts))
        m3.metric("Monthly Cost", monthly_cost_text)
        m4.metric("Active Incidents", len(active_alerts))
        st.caption(f"Last Deployment: {last_deployment}")

    related = resource.get("connected_resources") or []
    with st.expander(f"Related Resources ({len(related)})"):
        if related:
            for related_resource in related[:15]:
                st.markdown(
                    f"- **{related_resource.get('name', 'Unknown')}** "
                    f"({related_resource.get('resource_type', related_resource.get('type', 'Unknown'))})"
                )
        else:
            st.markdown("_No related resources recorded._")

    st.markdown("#### 🤖 AI Investigation")

    agent_state = st.session_state.get("agent_state") or {}
    investigation = agent_state.get("investigation") or {}
    investigated_id = agent_state.get("selected_resource_id")
    is_current = bool(investigated_id) and resource_ids_match(investigated_id, resource_id)
    stage = investigation.get("stage", "idle") if is_current else "idle"

    progress_placeholder = st.empty()
    if stage in ALL_STAGES:
        render_progress_strip(progress_placeholder, stage)
    else:
        progress_placeholder.caption("⬜ No investigation run yet for this resource.")

    button_label = "🔍 Re-run Investigation" if is_current and stage == "complete" else "🔍 Open Investigation"
    if st.button(button_label, key=f"open_investigation_{resource_id}", use_container_width=True):
        for step_state in investigate_node(resource_id, resource_name=resource_name):
            live_stage = (step_state.get("investigation") or {}).get("stage", "idle")
            if live_stage in ALL_STAGES:
                render_progress_strip(progress_placeholder, live_stage)
        st.rerun()

    if is_current and stage == "complete":
        final_report = investigation.get("final_report") or {}
        with st.container(border=True):
            st.markdown("**AI Summary**")
            st.markdown(final_report.get("executive_summary") or "_No summary available._")

            root_cause = (final_report.get("root_cause") or "").strip()
            if root_cause and root_cause.lower() not in ("not applicable", "n/a"):
                st.markdown(f"**Root Cause:** {root_cause}")
                st.caption(f"Confidence: {final_report.get('root_cause_confidence', 0.0):.0%}")

        evidence = final_report.get("supporting_evidence") or []
        with st.expander(f"Supporting Evidence ({len(evidence)})"):
            if evidence:
                for item in evidence:
                    st.markdown(
                        f"- **[{item.get('id')}]** ({item.get('domain')}) "
                        f"{item.get('description')} — _{item.get('source')}_"
                    )
            else:
                st.markdown("_No evidence was cited for this investigation._")

        if investigation.get("evidence_sources"):
            st.caption(f"Domains consulted: {', '.join(investigation['evidence_sources'])}")
    elif is_current and investigation.get("error"):
        st.error(investigation["error"])
    else:
        st.caption("Click **Open Investigation** to run LangGraph → CrewAI agents → Claude Sonnet for this resource.")
