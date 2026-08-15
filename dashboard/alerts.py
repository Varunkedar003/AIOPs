import streamlit as st
from typing import Any, Dict, List, Optional
from datetime import datetime


SEVERITY_OPTIONS = ["All", "Critical", "Warning", "Info"]


def _get_recommended_action(alert: Dict[str, Any]) -> str:
    """Derive a recommended action from alert metadata."""
    name = alert.get("name", "").lower()
    severity = alert.get("severity", "")
    message = alert.get("message", "").lower()

    if "crashloop" in name:
        return "Inspect pod logs, verify container image and startup configuration."
    if "imagepull" in name:
        return "Verify container image name, registry credentials, and image availability."
    if "oom" in name or "memory" in message:
        return "Increase memory limits or optimize application memory usage."
    if "cpu" in message or "high cpu" in name:
        return "Scale out replicas or investigate CPU-intensive processes."
    if "latency" in name or "latency" in message:
        return "Review downstream dependencies, database queries, and API performance."
    if "connection" in message or "database" in name.lower():
        return "Check database connectivity, firewall rules, and connection pool settings."
    if "deployment" in name.lower() or "pipeline" in name.lower():
        return "Review deployment logs, rollback if needed, and fix failing pipeline stage."
    if "restart" in name.lower():
        return "Analyze pod events and application logs for recurring crash causes."
    if "node" in name.lower() or "availability" in name.lower():
        return "Investigate node health, drain affected nodes, and verify cluster capacity."
    if severity == "Critical":
        return "Immediate investigation required — escalate to on-call engineer."
    if severity == "Warning":
        return "Monitor closely and plan remediation during next maintenance window."
    return "Review alert details and validate resource health metrics."


def _format_timestamp(value: Optional[str]) -> str:
    if not value:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return value


def _severity_color(severity: str) -> str:
    severity_lower = severity.lower()
    if severity_lower == "critical":
        return "red"
    if severity_lower == "warning":
        return "orange"
    return "blue"


def _health_color(status: str) -> str:
    status_lower = (status or "").lower()
    if status_lower == "available":
        return "green"
    if status_lower == "degraded":
        return "orange"
    if status_lower == "unavailable":
        return "red"
    return "blue"


def _render_health_banner(health: Dict[str, Any]) -> None:
    status = health.get("health_status", "Unknown")
    st.markdown(f"**Resource Health:** :{_health_color(status)}[{status}]")
    message = health.get("message")
    if message:
        st.caption(message)


def render_alerts_dashboard(
    resource_service: Any,
    resource_id: str,
    resource_name: Optional[str] = None,
) -> None:
    """Render the alerts and resource health dashboard for the selected resource."""
    st.markdown("### Alerts & Health")

    display_name = resource_name or resource_id
    st.caption(f"Showing alerts and health for **{display_name}** — updates automatically when selection changes.")

    health = resource_service.get_resource_health(resource_id)
    _render_health_banner(health)

    alerts = resource_service.get_resource_alerts(resource_id)
    active_alerts = [alert for alert in alerts if alert.get("status", "").lower() == "active"]

    severity_filter = st.selectbox(
        "Filter by Severity",
        options=SEVERITY_OPTIONS,
        key=f"alert_severity_filter_{resource_id}",
    )

    filtered_alerts = active_alerts
    if severity_filter != "All":
        filtered_alerts = [
            alert for alert in active_alerts
            if alert.get("severity", "").lower() == severity_filter.lower()
        ]

    critical_count = len([a for a in active_alerts if a.get("severity") == "Critical"])
    warning_count = len([a for a in active_alerts if a.get("severity") == "Warning"])
    total_count = sum(alert.get("count", 1) for alert in filtered_alerts)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Active Alerts", len(active_alerts))
    with col2:
        st.metric("Critical", critical_count)
    with col3:
        st.metric("Alert Count", total_count)

    if not filtered_alerts:
        if not active_alerts:
            st.success("No active alerts for this resource.")
        else:
            st.info(f"No active alerts with severity **{severity_filter}**.")
        return

    for alert in filtered_alerts:
        severity = alert.get("severity", "Unknown")
        status = alert.get("status", "Unknown")
        with st.expander(
            f"{severity} — {alert.get('name', 'Unknown Alert')} ({status})",
            expanded=severity == "Critical",
        ):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Severity:** :{_severity_color(severity)}[{severity}]")
                st.markdown(f"**Status:** {status}")
                st.markdown(f"**Trigger Time:** {_format_timestamp(alert.get('created_at'))}")
            with col2:
                st.markdown(f"**Alert Count:** {alert.get('count', 1)}")
                st.markdown(f"**Last Updated:** {_format_timestamp(alert.get('last_updated'))}")
                st.markdown(f"**Resource:** {alert.get('resource_name', display_name)}")

            st.markdown("**Description**")
            st.markdown(alert.get("message", "No description available."))

            st.markdown("**Recommended Action**")
            st.info(_get_recommended_action(alert))

    st.markdown("#### Alerts Summary")
    summary_rows = [
        {
            "Alert": alert.get("name", "Unknown"),
            "Severity": alert.get("severity", "Unknown"),
            "Status": alert.get("status", "Unknown"),
            "Count": alert.get("count", 1),
            "Trigger Time": _format_timestamp(alert.get("created_at")),
            "Recommended Action": _get_recommended_action(alert),
        }
        for alert in filtered_alerts
    ]
    st.dataframe(summary_rows, use_container_width=True, hide_index=True)
