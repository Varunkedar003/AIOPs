import streamlit as st
from typing import Any, Dict, Optional
from datetime import datetime


def _format_timestamp(value: Optional[str]) -> str:
    if not value:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return str(value)


def render_logs_dashboard(
    resource_service: Any,
    resource_id: str,
    resource_name: Optional[str] = None,
) -> None:
    """Render the Log Analytics logs dashboard for the selected resource."""
    st.markdown("### Logs")

    display_name = resource_name or resource_id
    st.caption(f"Showing recent logs for **{display_name}** — last 24 hours, most recent first.")

    logs = resource_service.get_resource_logs(resource_id)

    if not logs:
        st.info("No logs available.")
        return

    severity_options = ["All"] + sorted({log.get("severity", "Unknown") for log in logs})
    severity_filter = st.selectbox(
        "Filter by Severity",
        options=severity_options,
        key=f"log_severity_filter_{resource_id}",
    )

    filtered_logs = logs
    if severity_filter != "All":
        filtered_logs = [log for log in logs if log.get("severity") == severity_filter]

    st.metric("Log Entries", len(filtered_logs))

    if not filtered_logs:
        st.info(f"No logs with severity **{severity_filter}**.")
        return

    rows = [
        {
            "Timestamp": _format_timestamp(log.get("timestamp")),
            "Severity": log.get("severity", "Unknown"),
            "Message": log.get("message", ""),
            "Source": log.get("source", "Unknown"),
        }
        for log in filtered_logs
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
