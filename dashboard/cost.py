import streamlit as st
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
import pandas as pd


_CURRENCY_SYMBOLS = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£", "JPY": "¥"}

_PERIOD_CAPTIONS = {
    "last_month": (
        "Showing **last month's** actual cost — your billing provider hasn't published "
        "current month-to-date data yet."
    ),
    "unavailable": "Cost data unavailable for both this month and last month.",
}


def _money(amount: float, currency: str) -> str:
    """Format an amount with the currency Cost Management actually reported, instead of
    assuming USD - this subscription bills in INR, and a hardcoded "$" would silently
    mislabel real rupee amounts as dollars."""
    currency = (currency or "USD").upper()
    symbol = _CURRENCY_SYMBOLS.get(currency)
    return f"{symbol}{amount:,.2f}" if symbol else f"{amount:,.2f} {currency}"


def _money_compact(amount: float, currency: str) -> str:
    """Readable, compact currency format for KPI cards and ranked lists (e.g. "₹2.01M" instead
    of "₹2,006,755.06") - non-technical users scan a handful of big numbers far faster than
    long decimals. Nothing is lost: the exact value is always one hover away (each KPI's `help`
    tooltip uses `_money`), and every cost column in a table uses Streamlit's own numeric
    "compact" column format - the underlying value stays a real float, so clicking a column
    header to sort still sorts correctly, unlike sorting a pre-formatted string."""
    currency = (currency or "USD").upper()
    symbol = _CURRENCY_SYMBOLS.get(currency)
    prefix = symbol or f"{currency} "
    sign = "-" if amount < 0 else ""
    n = abs(amount)
    if n >= 1_000_000_000:
        text = f"{n / 1_000_000_000:.2f}B"
    elif n >= 1_000_000:
        text = f"{n / 1_000_000:.2f}M"
    elif n >= 1_000:
        text = f"{n / 1_000:.1f}K"
    else:
        text = f"{n:,.0f}"
    text = text.replace(".00B", "B").replace(".00M", "M").replace(".0K", "K")
    return f"{sign}{prefix}{text}"


# Ordered most-specific-first (a narrow rule must precede a broader catch-all it would
# otherwise be swallowed by). A small, local mapping kept in this file only - the
# Infrastructure Explorer graph has its own equivalent catalogue, but that one lives in a
# vendored, JS-only Streamlit component with no shared module Python code can import.
_FRIENDLY_TYPE_RULES = [
    ("microsoft.web/serverfarms", "App Service Plan"),
    ("microsoft.web/staticsites", "Static Web App"),
    ("microsoft.web/sites/functions", "Function App"),
    ("microsoft.web/sites", "App Service"),
    ("microsoft.containerservice/managedclusters", "AKS Cluster"),
    ("microsoft.containerregistry/registries", "Container Registry"),
    ("microsoft.containerinstance/", "Container Instance"),
    ("microsoft.app/", "Container App"),
    ("microsoft.documentdb/", "Cosmos DB"),
    ("microsoft.dbforpostgresql/", "PostgreSQL"),
    ("microsoft.dbformysql/", "MySQL"),
    ("microsoft.sql/", "Azure SQL"),
    ("microsoft.cache/redis", "Redis"),
    ("microsoft.storage/storageaccounts", "Storage Account"),
    ("microsoft.compute/disks", "Managed Disk"),
    ("microsoft.compute/virtualmachinescalesets", "VM Scale Set"),
    ("microsoft.compute/virtualmachines", "Virtual Machine"),
    ("microsoft.keyvault/vaults", "Key Vault"),
    ("microsoft.insights/components", "Application Insights"),
    ("microsoft.operationalinsights/workspaces", "Log Analytics"),
    ("microsoft.databricks/workspaces", "Databricks Workspace"),
    ("microsoft.cognitiveservices/accounts", "Cognitive Services"),
    ("microsoft.search/", "Cognitive Search"),
    ("microsoft.network/virtualnetworks", "Virtual Network"),
    ("microsoft.network/networksecuritygroups", "Network Security Group"),
    ("microsoft.network/loadbalancers", "Load Balancer"),
    ("microsoft.network/applicationgateways", "Application Gateway"),
    ("microsoft.network/publicipaddresses", "Public IP"),
    ("microsoft.network/", "Networking"),
    ("microsoft.recoveryservices/", "Recovery Services Vault"),
    ("microsoft.automation/", "Automation Account"),
    ("microsoft.datafactory/", "Data Factory"),
    ("microsoft.eventhub/", "Event Hub"),
    ("microsoft.servicebus/", "Service Bus"),
    ("microsoft.apimanagement/", "API Management"),
    ("microsoft.resources/resourcegroups", "Resource Group"),
]


def _friendly_type(raw_type: str) -> str:
    """Human-friendly label for an Azure resource type, e.g. "microsoft.web/sites" ->
    "App Service". The raw ARM type is never discarded, just not what's shown by default -
    it's kept alongside as a "Type (Azure)" column in the resource table for anyone who wants
    it."""
    lowered = (raw_type or "").lower()
    for prefix, name in _FRIENDLY_TYPE_RULES:
        if prefix in lowered:
            return name
    tail = lowered.rstrip("/").split("/")[-1] or lowered
    return tail.replace("-", " ").replace("_", " ").title() or "Unknown"


def _format_date_range(start: date, end: date) -> str:
    """Human-friendly date range, e.g. "Aug 1 - Aug 14, 2026" instead of raw ISO strings.
    Built from `.day`/`.year` rather than strftime's zero-padded "%d" (and its non-portable
    "%-d"/"%#d" no-leading-zero variants, which differ between Linux and Windows) so the day
    reads as "1", not "01", on any platform."""
    def _month_day(d: date) -> str:
        return f"{d.strftime('%b')} {d.day}"

    if start.year != end.year:
        return f"{_month_day(start)}, {start.year} – {_month_day(end)}, {end.year}"
    if start == end:
        return f"{_month_day(start)}, {start.year}"
    return f"{_month_day(start)} – {_month_day(end)}, {end.year}"


def _render_ranked_cost_table(
    totals_by_label: Dict[str, float],
    currency: str,
    total_cost: float,
    label_header: str,
    key: str,
    top_n: int = 10,
) -> None:
    """Clean "Top N by cost" ranked list - an inline proportion bar plus compact currency,
    instead of a bar chart whose category axis turns crowded/unreadable once there are more
    than a handful of resource groups or types."""
    ranked = sorted(totals_by_label.items(), key=lambda kv: kv[1], reverse=True)
    shown = ranked[:top_n]
    if not shown:
        st.info("No cost data to show.")
        return

    symbol = _CURRENCY_SYMBOLS.get((currency or "USD").upper(), currency)
    cost_col = f"Cost ({symbol})"
    max_cost = shown[0][1] or 1.0

    df = pd.DataFrame([
        {
            label_header: label,
            cost_col: cost,
            "% of Total": (cost / total_cost * 100) if total_cost else 0.0,
        }
        for label, cost in shown
    ])
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            cost_col: st.column_config.ProgressColumn(cost_col, format="compact", min_value=0, max_value=max_cost),
            "% of Total": st.column_config.NumberColumn("% of Total", format="%.1f%%"),
        },
        key=key,
    )
    if len(ranked) > top_n:
        st.caption(f"Top {top_n} of {len(ranked)} shown, by cost.")


def render_cost_dashboard(
    resource_service: Any,
    resource_id: str,
    resource_name: Optional[str] = None,
) -> None:
    """Render the cost dashboard for the selected resource."""
    st.markdown("### Cost Dashboard")

    display_name = resource_name or resource_id
    st.caption(f"Showing cost data for **{display_name}** — updates automatically when selection changes.")

    # Five independent live Cost Management queries - fired concurrently instead of one
    # after another. Each can individually block for tens of seconds under Cost
    # Management's throttling (see AzureCostManagement._run_query's retry/backoff), and
    # running them serially is what made this tab take minutes to render.
    with st.spinner("Loading cost data..."):
        with ThreadPoolExecutor(max_workers=5) as pool:
            cost_future = pool.submit(resource_service.get_resource_cost, resource_id)
            summary_future = pool.submit(resource_service.get_subscription_cost_summary)
            trend_future = pool.submit(resource_service.get_resource_daily_cost_trend, resource_id)
            breakdown_future = pool.submit(resource_service.get_resource_cost_breakdown, resource_id)
            top_future = pool.submit(resource_service.get_top_cost_resources, limit=5)

            cost_data = cost_future.result()
            subscription_summary = summary_future.result()
            daily_trend = trend_future.result()
            breakdown = breakdown_future.result()
            top_resources = top_future.result()

    if not cost_data:
        st.info(
            "Cost data unavailable for this resource — both this month and last month came "
            "back with no recorded cost."
        )
        _render_subscription_overview(subscription_summary, top_resources)
        return

    currency = cost_data.get("currency", "USD")
    monthly_cost = cost_data.get("monthly_cost", 0.0)
    last_month_cost = cost_data.get("last_month_cost", 0.0)
    daily_cost = cost_data.get("daily_cost", 0.0)
    change_pct = cost_data.get("cost_change_percentage", 0.0)
    trend = cost_data.get("cost_trend", "stable")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Current Month Cost", _money(monthly_cost, currency), delta=f"{change_pct:+.1f}% vs last month")
    with col2:
        st.metric("Last Month Cost", _money(last_month_cost, currency))
    with col3:
        st.metric("Daily Cost", _money(daily_cost, currency))

    st.markdown("#### Daily Cost Trend")
    if daily_trend:
        trend_df = pd.DataFrame(daily_trend).rename(columns={"date": "Date", "cost": f"Cost ({currency})"})
        st.line_chart(trend_df.set_index("Date"), use_container_width=True)
        st.caption(f"Trend: **{trend.title()}** ({change_pct:+.1f}% vs prior month)")
    else:
        st.info("Cost data unavailable.")

    if breakdown and breakdown.get("cost_breakdown"):
        st.markdown("#### Cost Breakdown")
        breakdown_period = breakdown.get("cost_period", "current_month")
        if breakdown_period in _PERIOD_CAPTIONS:
            st.caption(_PERIOD_CAPTIONS[breakdown_period])
        breakdown_items = breakdown["cost_breakdown"]
        breakdown_df = pd.DataFrame(
            {"Category": list(breakdown_items.keys()), f"Cost ({currency})": list(breakdown_items.values())}
        )
        st.bar_chart(breakdown_df.set_index("Category"), use_container_width=True)

    _render_subscription_overview(subscription_summary, top_resources)


def _render_subscription_overview(
    subscription_summary: Dict[str, Any],
    top_resources: List[Dict[str, Any]],
) -> None:
    """Render subscription-level cost overview."""
    st.markdown("#### Subscription Overview")

    currency = subscription_summary.get("currency", "USD")
    period = subscription_summary.get("cost_period", "current_month")
    if period in _PERIOD_CAPTIONS:
        st.caption(_PERIOD_CAPTIONS[period])

    col1, col2 = st.columns(2)
    with col1:
        st.metric(
            "Total Subscription Cost",
            f"{_money(subscription_summary.get('total_monthly_cost', 0), currency)}/mo",
        )
    with col2:
        st.metric(
            "Total Daily Cost",
            _money(subscription_summary.get('total_daily_cost', 0), currency),
        )

    st.markdown("#### Cost by Resource Type")
    cost_by_type = subscription_summary.get("cost_by_type", {})
    if cost_by_type:
        type_df = pd.DataFrame(
            {"Resource Type": list(cost_by_type.keys()), f"Monthly Cost ({currency})": list(cost_by_type.values())}
        ).sort_values(f"Monthly Cost ({currency})", ascending=False)
        st.bar_chart(type_df.set_index("Resource Type"), use_container_width=True)
    else:
        st.info("Cost data unavailable.")

    st.markdown("#### Cost by Resource Group")
    cost_by_group = subscription_summary.get("cost_by_resource_group", {})
    if cost_by_group:
        group_df = pd.DataFrame(
            {"Resource Group": list(cost_by_group.keys()), f"Monthly Cost ({currency})": list(cost_by_group.values())}
        ).sort_values(f"Monthly Cost ({currency})", ascending=False)
        st.bar_chart(group_df.set_index("Resource Group"), use_container_width=True)
    else:
        st.info("Cost data unavailable.")

    st.markdown("#### Top Costly Resources")
    if top_resources:
        top_rows = [
            {
                "Resource": item.get("resource_name", "Unknown"),
                "Type": item.get("resource_type", "Unknown"),
                "Region": item.get("region", "Unknown"),
                "Monthly Cost": _money(item.get('monthly_cost', 0), currency),
                "Trend": item.get("cost_trend", "stable").title(),
            }
            for item in top_resources
        ]
        st.dataframe(top_rows, use_container_width=True, hide_index=True)


def _last_month_range(today: date) -> tuple:
    first_of_this_month = today.replace(day=1)
    last_day_of_prev_month = first_of_this_month - timedelta(days=1)
    return last_day_of_prev_month.replace(day=1), last_day_of_prev_month


_DATE_PRESETS = {
    "Month to date": lambda today: (today.replace(day=1), today),
    "Last month": _last_month_range,
    "Last 7 days": lambda today: (today - timedelta(days=6), today),
    "Last 30 days": lambda today: (today - timedelta(days=29), today),
    "Last 3 months": lambda today: (today - timedelta(days=89), today),
}


def render_cost_analysis(resource_service: Any) -> None:
    """Azure Portal-style Cost Analysis: subscription-wide actual cost from Azure Cost
    Management (never Azure Monitor - CPU/memory metrics live entirely in the separate
    Utilization tab), filterable/searchable/sortable by resource, resource group, and
    resource type, for an arbitrary date range.

    Backed by ResourceService.get_cost_analysis/get_cost_trend, which are themselves backed
    by a single Cost Management query grouped by ResourceId (see
    AzureCostManagement.get_cost_by_resource) - switching filters, search, or sort here never
    issues a new Azure call; only changing the date range or trend granularity does, and even
    that is cached per (range, granularity) so revisiting one is free the second time. This
    function only ever reads that already-loaded dataset - every KPI/chart/table below is
    computed from the same `analysis`/`trend` result, never a fresh Azure call of its own.

    Laid out to answer four questions a non-technical reader would actually ask, top to
    bottom: how much am I spending (KPI cards), where (Top Resource Group/Type), what costs
    the most (Top Cost Drivers), and how is it changing (trend) - with the full sortable/
    filterable detail table last, for anyone who wants to dig in.
    """
    st.markdown("### 💰 Cost Analysis")
    st.caption("Actual spend from **Azure Cost Management** for this subscription — not a forecast or budget.")

    # Azure Cost Management bills in UTC. Using the local server date here (date.today(), as
    # this used to) can disagree with that by up to a day depending on the server's timezone -
    # e.g. in IST (UTC+5:30), the local date can already be "tomorrow" relative to the UTC day
    # Cost Management is still on, silently asking for a day that hasn't started billing yet.
    # AzureCostManagement's own _month_to_date_period()/_last_month_period() already compute
    # "today" via datetime.utcnow().date() - matching that here keeps the UI's date-range
    # presets and the backend's own period fallbacks referring to the same "today".
    today = datetime.utcnow().date()

    control_cols = st.columns([2, 1.3, 1.3])
    with control_cols[0]:
        preset = st.selectbox("Date range", list(_DATE_PRESETS.keys()) + ["Custom range"], key="cost_analysis_preset")
    with control_cols[1]:
        granularity = st.selectbox("Trend granularity", ["Daily", "Monthly"], key="cost_analysis_granularity")
    with control_cols[2]:
        st.markdown("&nbsp;")
        if st.button("🔄 Refresh Cost Data", use_container_width=True):
            # refresh_cost_data() is cooldown-limited application-wide (shared by every
            # session), so a click while another refresh is still on cooldown clears nothing
            # and just reports how long to wait - it never fires an extra Cost Management call.
            result = resource_service.refresh_cost_data(caller="finops_refresh_button")
            if result.get("refreshed"):
                st.rerun()
            else:
                st.warning(
                    f"Refresh was just used — please wait {result.get('retry_after_seconds', 0):.0f}s "
                    "before refreshing again."
                )

    if preset == "Custom range":
        custom_range = st.date_input(
            "Custom range", value=(today.replace(day=1), today), max_value=today, key="cost_analysis_custom_range"
        )
        if isinstance(custom_range, tuple) and len(custom_range) == 2:
            start, end = custom_range
        else:
            start, end = today.replace(day=1), today
    else:
        start, end = _DATE_PRESETS[preset](today)

    if start > end:
        st.error("The start date must be before the end date.")
        return

    days_in_range = (end - start).days + 1
    st.caption(f"📅 Showing **{_format_date_range(start, end)}** ({days_in_range} day{'s' if days_in_range != 1 else ''}).")

    time_period = {"from": start.isoformat(), "to": end.isoformat()}

    with st.spinner("Loading cost data..."):
        with ThreadPoolExecutor(max_workers=2) as pool:
            analysis_future = pool.submit(resource_service.get_cost_analysis, time_period, caller="finops_cost_analysis")
            trend_future = pool.submit(
                resource_service.get_cost_trend, time_period, granularity, caller="finops_cost_analysis_trend"
            )
            analysis = analysis_future.result()
            trend = trend_future.result()

    currency = analysis.get("currency", "USD")
    symbol = _CURRENCY_SYMBOLS.get((currency or "USD").upper(), currency)
    rows = analysis.get("rows") or []

    if not analysis.get("available") or not rows:
        st.info(
            "Cost data unavailable for the selected date range — Cost Management returned no "
            "recorded cost for this period (it may not be finalized/published yet). Try "
            "**Last month** or a wider custom range."
        )
        return

    total_cost = analysis.get("total_cost", 0.0)
    # Friendly type is computed into a fresh dict per row (never mutating `rows`, which is a
    # brand-new list from this call to get_cost_analysis - not the cached dataset itself, but
    # no reason to write into data we don't own) so every section below - KPIs, ranked tables,
    # drivers, the detail table - shares one consistent, non-technical resource type label.
    enriched_rows = [{**r, "resource_type_friendly": _friendly_type(r["resource_type"])} for r in rows]
    top_resource = max(enriched_rows, key=lambda r: r["cost"])
    resource_group_count = len({r["resource_group"] for r in enriched_rows})

    # --- How much am I spending? -----------------------------------------------------------
    st.divider()
    st.markdown("#### At a Glance")
    kpi_cols = st.columns(4)
    with kpi_cols[0]:
        st.metric(
            "Total Cost", _money_compact(total_cost, currency),
            delta=f"over {days_in_range} day{'s' if days_in_range != 1 else ''}", delta_color="off",
            help=f"Exact: {_money(total_cost, currency)}",
        )
    with kpi_cols[1]:
        avg_daily = (total_cost / days_in_range) if days_in_range else 0.0
        st.metric(
            "Avg Daily Cost", _money_compact(avg_daily, currency),
            delta="total ÷ days in range", delta_color="off",
            help=f"Exact: {_money(avg_daily, currency)}",
        )
    with kpi_cols[2]:
        top_name = top_resource["resource_name"]
        st.metric(
            "Top Cost Resource",
            top_name if len(top_name) <= 22 else top_name[:21] + "…",
            delta=_money_compact(top_resource["cost"], currency), delta_color="off",
            help=(
                f"{top_resource['resource_name']} ({top_resource['resource_type_friendly']}) in "
                f"{top_resource['resource_group']} — exact cost: {_money(top_resource['cost'], currency)}"
            ),
        )
    with kpi_cols[3]:
        st.metric(
            "Resource Count", str(len(enriched_rows)),
            delta=f"across {resource_group_count} resource group{'s' if resource_group_count != 1 else ''}",
            delta_color="off",
            help="Resources with recorded cost in this period - resources with no usage in this range aren't counted.",
        )

    cost_by_group: Dict[str, float] = {}
    cost_by_type: Dict[str, float] = {}
    for r in enriched_rows:
        cost_by_group[r["resource_group"]] = cost_by_group.get(r["resource_group"], 0.0) + r["cost"]
        cost_by_type[r["resource_type_friendly"]] = cost_by_type.get(r["resource_type_friendly"], 0.0) + r["cost"]

    # --- Where am I spending? ----------------------------------------------------------------
    st.divider()
    st.markdown("#### 📍 Where You're Spending")
    where_cols = st.columns(2)
    with where_cols[0]:
        st.markdown("##### Top 10 Resource Groups")
        _render_ranked_cost_table(cost_by_group, currency, total_cost, "Resource Group", key="cost_analysis_top_rg")
    with where_cols[1]:
        st.markdown("##### Top 10 Resource Types")
        _render_ranked_cost_table(cost_by_type, currency, total_cost, "Resource Type", key="cost_analysis_top_type")

    # --- What costs the most? -----------------------------------------------------------------
    st.divider()
    st.markdown("#### 🔥 Top Cost Drivers")
    st.caption("The 10 individual resources costing the most in this period.")
    top_driver_rows = sorted(enriched_rows, key=lambda r: r["cost"], reverse=True)[:10]
    max_driver_cost = top_driver_rows[0]["cost"] if top_driver_rows else 1.0
    cost_col = f"Cost ({symbol})"
    drivers_df = pd.DataFrame([
        {
            "Resource Name": r["resource_name"],
            "Type": r["resource_type_friendly"],
            "Resource Group": r["resource_group"],
            "Region": r["region"],
            cost_col: r["cost"],
            "% of Total": (r["cost"] / total_cost * 100) if total_cost else 0.0,
        }
        for r in top_driver_rows
    ])
    st.dataframe(
        drivers_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            cost_col: st.column_config.ProgressColumn(cost_col, format="compact", min_value=0, max_value=max_driver_cost),
            "% of Total": st.column_config.NumberColumn("% of Total", format="%.1f%%"),
        },
        key="cost_analysis_top_drivers_table",
    )
    if len(enriched_rows) > 10:
        st.caption(f"Top 10 of {len(enriched_rows)} resources with recorded cost.")

    # --- How is spending changing? ------------------------------------------------------------
    st.divider()
    st.markdown(f"#### 📈 Cost Trend Over Time ({granularity})")
    if trend:
        # Only Date/Cost go into the chart - `trend` records also carry a "currency" field
        # (e.g. "INR" repeated on every row), and feeding that into st.line_chart alongside the
        # numeric cost made it render as a second, meaningless series.
        trend_df = pd.DataFrame(trend)[["date", "cost"]].rename(columns={"date": "Date", "cost": cost_col})
        st.line_chart(trend_df.set_index("Date"), use_container_width=True)
    else:
        st.info("Cost trend unavailable for the selected range.")

    # --- Full detail: filter/search/sort every resource ---------------------------------------
    st.divider()
    st.markdown("#### 🔍 All Resources")
    filter_cols = st.columns([1.4, 1.4, 1.6, 1])
    resource_groups = sorted({r["resource_group"] for r in enriched_rows})
    resource_types_friendly = sorted({r["resource_type_friendly"] for r in enriched_rows})
    with filter_cols[0]:
        rg_filter = st.multiselect("Resource Group", resource_groups, key="cost_analysis_rg_filter")
    with filter_cols[1]:
        type_filter = st.multiselect("Resource Type", resource_types_friendly, key="cost_analysis_type_filter")
    with filter_cols[2]:
        search = st.text_input(
            "Search resource", value="", placeholder="Name or resource ID...", key="cost_analysis_search"
        )
    with filter_cols[3]:
        sort_order = st.selectbox("Sort by cost", ["Highest first", "Lowest first"], key="cost_analysis_sort")

    filtered = enriched_rows
    if rg_filter:
        filtered = [r for r in filtered if r["resource_group"] in rg_filter]
    if type_filter:
        filtered = [r for r in filtered if r["resource_type_friendly"] in type_filter]
    if search:
        needle = search.strip().lower()
        filtered = [
            r for r in filtered
            if needle in r["resource_name"].lower() or needle in r["resource_id"].lower()
        ]

    filtered = sorted(filtered, key=lambda r: r["cost"], reverse=(sort_order == "Highest first"))

    st.caption(f"{len(filtered)} of {len(enriched_rows)} resources with recorded cost in this range.")

    if not filtered:
        st.info("No resources match the current filters.")
        return

    max_cost_overall = max(r["cost"] for r in enriched_rows) or 1.0
    table_df = pd.DataFrame([
        {
            "Resource Name": r["resource_name"],
            "Resource Type": r["resource_type_friendly"],
            "Resource Group": r["resource_group"],
            "Region": r["region"],
            cost_col: r["cost"],
            "% of Total": (r["cost"] / total_cost * 100) if total_cost else 0.0,
            "Type (Azure)": r["resource_type"],
            "Resource ID": r["resource_id"],
        }
        for r in filtered
    ])

    event = st.dataframe(
        table_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            cost_col: st.column_config.ProgressColumn(cost_col, format="compact", min_value=0, max_value=max_cost_overall),
            "% of Total": st.column_config.NumberColumn("% of Total", format="%.1f%%"),
            "Type (Azure)": st.column_config.TextColumn("Type (Azure)", help="Original Azure resource type"),
        },
        on_select="rerun",
        selection_mode="single-row",
        key="cost_analysis_table",
    )

    selected_rows = event.selection.rows if event and event.selection else []
    if selected_rows:
        picked = filtered[selected_rows[0]]
        st.session_state.selected_resource_id = picked["resource_id"]
        st.success(
            f"Selected **{picked['resource_name']}** — {_money(picked['cost'], picked['currency'])} for this "
            "period. Its actual cost now shows in the Resource Summary (AI panel) on Infrastructure "
            "Explorer / Resource Workspace."
        )
        if st.button("Open Resource Workspace →", key="cost_analysis_open_workspace"):
            from dashboard.pages.registry import RESOURCE_WORKSPACE
            st.switch_page(RESOURCE_WORKSPACE)
