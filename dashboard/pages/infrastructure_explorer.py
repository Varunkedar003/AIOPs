import streamlit as st
from typing import Any, Dict, Optional

from dashboard.ai_side_panel import render_ai_side_panel
from dashboard.components.g6_explorer import g6_explorer
from utils.resource_id import resource_ids_match


def render_infrastructure_explorer() -> None:
    """Landing page: full subscription hierarchy tree, a quick resource summary, and a way into the Resource Workspace."""
    st.markdown("## Infrastructure Explorer")
    st.caption(
        "Complete Azure subscription hierarchy: Subscription → Resource Groups → Resource Types → Resources. "
        "Click a group or type to expand/collapse it, click a resource to preview it below."
    )

    resource_service = st.session_state.resource_service
    # Topology-only fetch (no per-resource ARM properties/identity metadata) - this view only
    # ever groups/labels resources by id/name/type/resource_group, so there's no need to pay for
    # the much larger full-detail payload get_all_azure_resources_raw() fetches for other pages
    # (e.g. Resource Workspace's relationship graph). Keeps "Refresh" fast at 800-1000+ resources.
    resources = resource_service.get_all_azure_resources_topology_only()

    connection_error = resource_service.azure_provider.get_connection_error()
    if connection_error:
        st.error(connection_error)

    subscription_label = resources[0].get("subscription") if resources else None

    selected_resource_id = st.session_state.get("selected_resource_id")

    col_graph, col_summary = st.columns([2, 1])

    with col_graph:
        clicked_id = g6_explorer(
            resources=resources,
            subscription_label=subscription_label or "Subscription",
            selected_id=selected_resource_id,
            reset_token=st.session_state.get("_g6_reset_token", 0),
            key="infrastructure_explorer_g6",
        )

        if isinstance(clicked_id, str) and clicked_id.startswith("__refresh__:"):
            # The component's reported value is sticky (Streamlit custom components keep
            # returning the same value on every rerun until JS sends a new one) - guard on a
            # per-click token so a refresh actually only fires once per click instead of forever
            # on every subsequent rerun (that repeat-refetch loop is what made this look broken/
            # slow).
            if clicked_id != st.session_state.get("_last_refresh_token"):
                st.session_state._last_refresh_token = clicked_id
                resource_service.azure_provider.refresh()
                # Refresh also returns the graph to its original setup - clears the current
                # selection/highlight (and, via reset_token, tells the component to discard any
                # expand/collapse exploration and manually-dragged positions too) rather than
                # silently re-fetching underneath whatever the user had open.
                st.session_state.selected_resource_id = None
                st.session_state._g6_reset_token = st.session_state.get("_g6_reset_token", 0) + 1
                st.rerun()
        elif clicked_id == "__deselect__":
            # Clicking empty canvas - clear the selection/highlight. Guarded on an existing
            # selection (like the resource-select branch below is guarded by resource_ids_match)
            # so this doesn't re-fire every rerun just because the component's last-returned value
            # is sticky until JS sends a new one.
            if selected_resource_id:
                st.session_state.selected_resource_id = None
                st.rerun()
        elif clicked_id and not resource_ids_match(clicked_id, selected_resource_id):
            st.session_state.selected_resource_id = clicked_id

            navigation_history = st.session_state.get("navigation_history", [])
            history_index = st.session_state.get("history_index", -1)
            if history_index < len(navigation_history) - 1:
                navigation_history = navigation_history[: history_index + 1]
            navigation_history.append(clicked_id)
            st.session_state.navigation_history = navigation_history
            st.session_state.history_index = len(navigation_history) - 1

            st.rerun()

        _render_g6_legend()

    with col_summary:
        resource_service = st.session_state.resource_service
        selected_resource_id = st.session_state.get("selected_resource_id")

        if selected_resource_id:
            with st.spinner("Loading resource details..."):
                resource_data = resource_service.get_resource_details(selected_resource_id)
            _render_quick_resource_summary(resource_data)

            st.markdown("---")
            render_ai_side_panel(resource_service, selected_resource_id, compact=True)

            st.markdown("---")
            if st.button("Open Resource Workspace →", type="primary", use_container_width=True):
                from dashboard.pages.registry import RESOURCE_WORKSPACE

                st.switch_page(RESOURCE_WORKSPACE)
        else:
            _render_quick_resource_summary(None)


def _render_quick_resource_summary(resource_data: Optional[Dict[str, Any]]) -> None:
    st.markdown("### Quick Resource Summary")

    if not resource_data:
        st.markdown("*No resource selected.*")
        st.caption("Click a node in the topology to preview it here.")
        return

    status = resource_data.get("state", resource_data.get("health_status", "Unknown"))
    resource_type = resource_data.get("resource_type", resource_data.get("type", "Unknown"))

    st.markdown(f"**Name:** {resource_data.get('name', 'Unknown')}")
    st.markdown(f"**Type:** {resource_type}")
    st.caption(_short_description(resource_data, resource_type))
    st.markdown(f"**Resource Group:** {resource_data.get('resource_group', 'Unknown')}")
    st.markdown(f"**Subscription:** {resource_data.get('subscription', resource_data.get('subscription_id', 'Unknown'))}")
    st.markdown(f"**Region:** {resource_data.get('region', resource_data.get('location', 'Unknown'))}")
    st.markdown(f"**Status:** :{_status_color(status)}[{status}]")


def _short_description(resource_data: Dict[str, Any], resource_type: str) -> str:
    """One-line description composed from fields Azure Resource Graph already returns for every
    resource (type/resource group/region) - ARM resources don't carry a free-text description, so
    this phrases existing data rather than inventing new facts."""
    parts = [resource_type or "Resource"]
    resource_group = resource_data.get("resource_group")
    if resource_group:
        parts.append(f"in {resource_group}")
    region = resource_data.get("region", resource_data.get("location"))
    if region:
        parts.append(f"({region})")
    return " ".join(parts)


def _status_color(status: str) -> str:
    status_lower = (status or "").lower()
    if status_lower in ["healthy", "running", "active", "online", "success"]:
        return "green"
    if status_lower in ["warning", "degraded", "unhealthy"]:
        return "orange"
    if status_lower in ["critical", "error", "failed", "stopped", "offline"]:
        return "red"
    return "blue"


def _legend_swatch(hex_color: str) -> str:
    return (
        f'<span style="display:inline-block;width:13px;height:13px;border-radius:3px;'
        f'background:{hex_color};vertical-align:middle;margin-right:6px;"></span>{hex_color}'
    )


def _legend_line_swatch(hex_color: str, label: str, thickness_px: int = 3, glow: bool = False) -> str:
    """A short horizontal line sample for an edge/connection legend entry - rendered as an
    actual colored line (not just "──" text) so it reads clearly regardless of the
    surrounding page's light/dark theme, and drawn a bit brighter/thicker here than the real
    in-graph edge (which is a much dimmer, hairline gray meant to stay unobtrusive on the
    graph's own dark canvas) purely so the swatch itself stays legible as a legend sample."""
    shadow = f"box-shadow:0 0 6px 1px {hex_color};" if glow else ""
    return (
        f'<span style="display:inline-block;width:32px;height:{thickness_px}px;'
        f'background:{hex_color};border-radius:2px;vertical-align:middle;margin-right:6px;{shadow}"></span>{label}'
    )


def _render_legend_table(rows) -> None:
    """Render a 3-column (Symbol/Indicator | Meaning | Description) legend table as a single
    static HTML table - `unsafe_allow_html` is already an established pattern elsewhere in this
    app (dashboard/topology.py, dashboard/graph_visualization.py) for exactly this kind of
    colored-swatch legend, and every string here is a fixed literal this function writes
    itself, never user input, so there's no injection risk.

    `rows` is a list of (symbol_html, meaning, description) or (symbol_html, meaning,
    description, tooltip) 4-tuples - the optional 4th item becomes a native browser tooltip
    (a `title` attribute) on that row, for a bit of extra detail beyond the description column.
    """
    row_html = []
    for row in rows:
        symbol, meaning, description = row[0], row[1], row[2]
        title_attr = f' title="{row[3]}"' if len(row) > 3 else ""
        row_html.append(
            f'<tr style="border-bottom:1px solid rgba(128,128,128,0.15);"{title_attr}>'
            f'<td style="padding:6px 10px;white-space:nowrap;">{symbol}</td>'
            f'<td style="padding:6px 10px;white-space:nowrap;font-weight:600;">{meaning}</td>'
            f'<td style="padding:6px 10px;">{description}</td>'
            f"</tr>"
        )
    st.markdown(
        '<div style="overflow-x:auto;">'
        '<table style="width:100%;border-collapse:collapse;font-size:0.85rem;">'
        '<thead><tr style="text-align:left;border-bottom:1px solid rgba(128,128,128,0.4);">'
        '<th style="padding:6px 10px;white-space:nowrap;">Symbol / Indicator</th>'
        '<th style="padding:6px 10px;white-space:nowrap;">Meaning</th>'
        '<th style="padding:6px 10px;">Description</th>'
        "</tr></thead><tbody>" + "".join(row_html) + "</tbody></table></div>",
        unsafe_allow_html=True,
    )


def _render_g6_legend() -> None:
    """Legend for the Infrastructure Explorer graph (dashboard/components/g6_explorer/index.html).

    Every entry below is read directly off that component's actual rendering logic - not
    guessed - so it stays accurate to what the graph really draws:
      - Node shape/border-dash and the count badge/chevron: styleForGroupNode/styleForResourceNode.
      - Border colors: healthColor() (health) and the `selected ? "#FF6B6B" : ...` override.
      - Fill color/icon per resource type: the RESOURCE_TYPES catalogue + typeMeta().
      - Edge colors/widths: edgeStyleFor(); the highlighted path comes from
        highlightedEdgeIdsFor()/highlightedAncestorNodeIdsFor().
      - Dimmed/matched opacity and glow: styleForGroupNode/styleForResourceNode's
        `dimmed`/`matched` parameters, computed in computeVisibleG6Data.
      - The Resource Group hover tooltip's Contains/Purpose/Region fields and the Network
        layout's plain-circle treatment (no badge/chevron): buildResourceGroupTooltipContent()
        and styleForGroupNode's `network` branch, respectively.
    """
    with st.expander("Graph Legend — what the symbols and colors mean", icon="📖"):
        st.caption("Explains every visual cue in the graph above: shapes, colors, badges, and connecting lines.")

        st.markdown("##### Node Shape & Structure")
        _render_legend_table([
            ("▭ <span style='border-bottom:2px dashed #a9afc0;'>┄┄┄</span> dashed box", "Group node",
             "A Subscription, Resource Group, or Resource Type - a container, not an actual Azure "
             "resource. Click it to expand or collapse what's inside."),
            ("▭ solid box", "Resource node",
             "One individual Azure resource - e.g. a specific VM, database, or storage account."),
            ("▼ / ▶ in a group's label", "Expanded / Collapsed",
             "Shows whether that group's contents are currently shown (▼) or hidden (▶)."),
            ("🔵 number badge", "Resource count",
             "How many resources are inside that group, type, or the whole subscription."),
        ])

        st.markdown("##### Node Border Color (Resource Health & Selection)")
        _render_legend_table([
            (_legend_swatch("#2ecc71"), "Healthy", "Running, active, online, or succeeded - no known issues."),
            (_legend_swatch("#f1c40f"), "Warning", "Degraded or unhealthy - may need attention."),
            (_legend_swatch("#e74c3c"), "Critical", "Error, failed, stopped, offline, or unavailable."),
            (_legend_swatch("#5c6370"), "Unknown", "Health status hasn't been determined for this resource."),
            (
                _legend_swatch("#FF6B6B"), "Selected",
                "The node you last clicked - its border always turns this bright coral-red, "
                "overriding the health color above.",
                "A selected Critical resource shows this bright coral-red, not the darker Critical "
                "red - click empty canvas to deselect and see its real health color again.",
            ),
        ])

        st.markdown("##### Node Fill Color & Icon (Resource Type)")
        st.caption("Every resource type has its own icon and tint color, used consistently across the graph - a few examples:")
        _render_legend_table([
            ("🏢", "Subscription", "The root node - your whole Azure subscription."),
            ("📁", "Resource Group", "A folder-like container grouping related resources together."),
            ("🌐", "App Service", "A web app or API hosted on Azure App Service."),
            ("🗄", "Azure SQL", "A SQL Server or SQL Database."),
            ("🐘", "PostgreSQL", "A PostgreSQL flexible server."),
            ("⚡", "Redis", "An Azure Cache for Redis instance."),
            ("💾", "Storage Account", "Blob/file/queue/table storage."),
            ("🖥", "Virtual Machine", "An Azure Virtual Machine."),
            ("☸", "AKS Cluster", "An Azure Kubernetes Service cluster."),
            ("🔐", "Key Vault", "Secrets, keys, and certificates."),
        ])
        st.caption("These are the most common types - hover any node in the graph to see its exact type and name.")

        st.markdown("##### Connecting Lines & Highlighting")
        _render_legend_table([
            (
                _legend_line_swatch("#c7cbd6", "gray line"), "Contains",
                "Connects a group to what's inside it - a containment relationship, not a live "
                "network connection.",
                "Shown brighter here for visibility - on the graph itself this line is a much "
                "dimmer, thin gray so it stays unobtrusive against the dark canvas.",
            ),
            (
                _legend_line_swatch("#FF6B6B", "glowing red line", glow=True), "Selected path",
                "Highlights the chain from your selected resource up to the subscription, so you can trace where it lives.",
            ),
            ("faded / dimmed nodes", "Not on the path",
             "Everything not on the highlighted path fades out once you select a resource."),
            ("green glow around a node", "Search / filter match",
             "This node matches your current search text, type filter, or health filter."),
        ])

        st.info(
            "💡 **Tip:** Hover any node for its full name and type. Hover a Resource Group to see "
            "what it contains, its likely purpose, and its region. Click a resource for a details "
            "card, or click a group to expand/collapse it. Switching to the **Network** layout "
            "replaces group badges/chevrons with plain circles sized by how many resources they contain."
        )
