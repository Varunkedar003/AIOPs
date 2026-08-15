import streamlit as st
from typing import Any, Dict, List, Optional


METRIC_DISPLAY_MAP = {
    "cpu": "CPU Utilization",
    "memory": "Memory Utilization",
    "disk": "Disk Usage",
    "network": "Network In/Out",
    "requests": "Requests/sec",
    "latency": "Response Time",
    "availability": "Availability",
}


def _format_metric_value(metric: Dict[str, Any]) -> str:
    value = metric.get("current_value", 0)
    unit = metric.get("unit", "")
    if unit == "percentage":
        return f"{value:.1f}%"
    if unit == "ms":
        return f"{value:.1f} ms"
    if unit == "mbps":
        return f"{value:.1f} Mbps"
    if unit == "rps":
        return f"{value:.1f}/s"
    return f"{value:.1f} {unit}".strip()


def _metric_status(metric: Dict[str, Any]) -> str:
    value = metric.get("current_value", 0)
    critical = metric.get("threshold_critical")
    warning = metric.get("threshold_warning")
    metric_type = metric.get("metric_type", "")

    if metric_type == "availability":
        if value < (critical or 95):
            return "critical"
        if value < (warning or 99):
            return "warning"
        return "healthy"

    if critical is not None and value >= critical:
        return "critical"
    if warning is not None and value >= warning:
        return "warning"
    return "healthy"


def _status_delta(status: str) -> Optional[str]:
    if status == "critical":
        return "Critical"
    if status == "warning":
        return "Warning"
    return "Normal"


def render_metrics_dashboard(
    resource_service: Any,
    resource_id: str,
    resource_name: Optional[str] = None,
) -> None:
    """Render the metrics dashboard for the selected resource."""
    st.markdown("### Metrics Dashboard")

    display_name = resource_name or resource_id
    st.caption(f"Showing metrics for **{display_name}** — updates automatically when selection changes.")

    metrics = resource_service.get_resource_metrics(resource_id)

    if not metrics:
        st.info("Metrics unavailable")
        return

    metrics_by_type: Dict[str, Dict[str, Any]] = {}
    for metric in metrics:
        metric_type = metric.get("metric_type", "unknown")
        if metric_type not in metrics_by_type:
            metrics_by_type[metric_type] = metric

    priority_types = ["cpu", "memory", "disk", "network", "requests", "latency", "availability"]
    ordered_types = [metric_type for metric_type in priority_types if metric_type in metrics_by_type]
    ordered_types.extend(
        metric_type for metric_type in metrics_by_type if metric_type not in priority_types
    )

    cols = st.columns(2)
    for index, metric_type in enumerate(ordered_types):
        metric = metrics_by_type[metric_type]
        label = METRIC_DISPLAY_MAP.get(metric_type, metric.get("name", metric_type.title()))
        status = _metric_status(metric)
        delta = _status_delta(status)

        with cols[index % 2]:
            st.metric(
                label=label,
                value=_format_metric_value(metric),
                delta=delta,
                delta_color="inverse" if status == "healthy" else "normal",
            )

    st.markdown("#### Metric Details")
    rows: List[Dict[str, Any]] = []
    for metric_type in ordered_types:
        metric = metrics_by_type[metric_type]
        rows.append(
            {
                "Metric": METRIC_DISPLAY_MAP.get(metric_type, metric.get("name", metric_type)),
                "Current": _format_metric_value(metric),
                "Average": f"{metric.get('average_value', 0):.1f}",
                "Max": f"{metric.get('max_value', 0):.1f}",
                "Warning": metric.get("threshold_warning", "—"),
                "Critical": metric.get("threshold_critical", "—"),
                "Status": _status_delta(_metric_status(metric)),
            }
        )

    st.dataframe(rows, use_container_width=True, hide_index=True)

    summary = resource_service.get_resource_metrics_summary(resource_id)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Health", summary.get("health_status", "Unknown"))
    with col2:
        st.metric("Total Metrics", summary.get("metrics_count", 0))
    with col3:
        st.metric("Active Alerts", summary.get("total_alerts", 0))


def render_subscription_health_overview(resource_service: Any, overview: Optional[Dict[str, Any]] = None) -> None:
    """Subscription-wide health/alerts summary shown in place of a blank page when no
    resource is selected (FinOps Utilization tab, Monitoring Metrics/Alerts tabs).

    Pass a precomputed `overview` (from resource_service.get_subscription_health_overview())
    when rendering this more than once per page - st.tabs() bodies all run on every script
    pass regardless of which tab is visible, so callers with multiple tabs should fetch once
    and share the result rather than re-sampling live health/alerts per tab.
    """
    st.markdown("### Subscription Health Overview")

    if overview is None:
        with st.spinner("Sampling resource health across the subscription..."):
            overview = resource_service.get_subscription_health_overview()

    sampled = overview.get("sampled_count", 0)
    total = overview.get("total_resource_count", 0)
    if not sampled:
        st.info("No resources discovered yet.")
        return

    if sampled < total:
        st.caption(f"Based on a sample of {sampled} of {total} discovered resources.")

    counts = overview.get("health_counts", {})
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Healthy", counts.get("healthy", 0))
    col2.metric("Warning", counts.get("warning", 0))
    col3.metric("Critical", counts.get("critical", 0))
    col4.metric("Active Alerts", overview.get("total_active_alerts", 0))

    attention = overview.get("attention", [])
    if attention:
        st.markdown("#### Resources Needing Attention")
        st.dataframe(
            [
                {
                    "Resource": item["name"],
                    "Type": item["type"],
                    "Active Alerts": item["active_alerts"],
                    "Worst Severity": item["worst_severity"],
                }
                for item in attention
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.success("No active alerts on the sampled resources.")

    st.caption("Select a resource from the sidebar or Infrastructure Explorer for full metrics, alerts, and logs.")
