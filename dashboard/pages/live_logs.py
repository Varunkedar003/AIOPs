"""Live Logs page - the in-app replacement for the standalone production-logs /
App-Testing App Service log viewers. Subscription-aware for free: the app picker below
lists whatever App Services the currently-active subscription (see
dashboard/subscription_picker.py) actually has, so switching Staging/Production in the
sidebar switches which apps show up here too - no separate "staging" vs "production"
page needed.

Backed by Kudu VFS (providers/azure/app_service_logs.py), not Log Analytics - this
environment has no Log Analytics workspace at all (confirmed live), so that path would
always return empty regardless of how it's queried. Kudu reads each app's own container
stdout/stderr log file directly - the same underlying data `az webapp log tail` and the
standalone viewers ultimately read, no extra Azure infrastructure required. Lines are
raw text (no structured severity/timestamp field the way a Log Analytics row would have),
so level detection/highlighting below is regex-based over the line's own text.

v1 is polling-based (manual + optional auto-refresh), not a true push/websocket tail
like the standalone tool. Once this is verified as a real replacement, the standalone
App Service viewers can be decommissioned.
"""
import re
import time
from datetime import datetime
from typing import Any, Dict, List

import streamlit as st

from dashboard.subscription_picker import get_active_subscription_label

_LINE_OPTIONS = {"Last 100 lines": 100, "Last 300 lines": 300, "Last 500 lines": 500, "Last 1000 lines": 1000}
_AUTO_REFRESH_SECONDS = 10

_LEVEL_PATTERNS = [
    ("critical", re.compile(r"\b(CRITICAL|FATAL)\b", re.IGNORECASE), "#f85149"),
    ("error", re.compile(r"\bERROR\b", re.IGNORECASE), "#f85149"),
    ("warning", re.compile(r"\bWARN(ING)?\b", re.IGNORECASE), "#d29922"),
    ("info", re.compile(r"\bINFO\b", re.IGNORECASE), "#58a6ff"),
]
_DEFAULT_COLOR = "#8b949e"

_PAGE_CSS = """
<style>
.live-log-line {
    font-family: ui-monospace, "SF Mono", Consolas, monospace;
    font-size: 12.5px;
    padding: 2px 8px;
    border-bottom: 1px solid #21262d;
    white-space: pre-wrap;
    word-break: break-word;
}
.live-log-line:hover { background: #161b22; }
.live-log-badge {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 4px;
    margin-right: 8px;
    text-transform: uppercase;
    color: #0d1117;
}
.live-log-container {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 8px;
    max-height: 65vh;
    overflow-y: auto;
}
mark { background: #d29922; color: #0d1117; border-radius: 2px; padding: 0 2px; }
</style>
"""


def _detect_level(line: str) -> tuple:
    for label, pattern, color in _LEVEL_PATTERNS:
        if pattern.search(line):
            return label, color
    return "", _DEFAULT_COLOR


def _highlight(text: str, query: str) -> str:
    if not query:
        return text
    return re.sub(f"({re.escape(query)})", r"<mark>\1</mark>", text, flags=re.IGNORECASE)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_line(line: str, search_query: str) -> str:
    level, color = _detect_level(line)
    badge = f'<span class="live-log-badge" style="background:{color}">{level}</span>' if level else ""
    text = _highlight(_escape(line), search_query)
    return f'<div class="live-log-line">{badge}{text}</div>'


def render_live_logs() -> None:
    st.markdown(_PAGE_CSS, unsafe_allow_html=True)
    st.markdown("## 📜 Live Logs")
    st.caption(f"Subscription: **{get_active_subscription_label()}**")

    resource_service = st.session_state.resource_service
    app_services: List[Dict[str, Any]] = resource_service.get_azure_resources().get("app_services", [])

    if not app_services:
        st.info("No App Services found in the active subscription.")
        return

    app_by_name = {app["name"]: app for app in app_services}

    header_cols = st.columns([3, 1.6, 1, 1, 2])
    with header_cols[0]:
        app_name = st.selectbox("App", sorted(app_by_name.keys()), key="live_logs_app")
    with header_cols[1]:
        lines_label = st.selectbox("Lines", list(_LINE_OPTIONS.keys()), index=1, key="live_logs_lines")
    with header_cols[2]:
        auto_refresh = st.toggle("Auto-refresh", value=False, key="live_logs_auto_refresh")
    with header_cols[3]:
        refresh_clicked = st.button("🔄 Reload", use_container_width=True)
    with header_cols[4]:
        search_query = st.text_input("🔍 Filter", key="live_logs_search", placeholder="Search log text...")

    level_cols = st.columns(6)
    all_levels = ["critical", "error", "warning", "info"]
    selected_levels = set()
    for col, level in zip(level_cols, all_levels):
        with col:
            if st.checkbox(level.capitalize(), value=True, key=f"live_logs_level_{level}"):
                selected_levels.add(level)
    with level_cols[4]:
        show_unlabeled = st.checkbox("Other", value=True, key="live_logs_level_other")

    default_host_name = (app_by_name[app_name].get("_properties") or {}).get("defaultHostName", "")
    max_lines = _LINE_OPTIONS[lines_label]

    with st.spinner(f"Fetching logs for {app_name}..."):
        raw_lines = resource_service.get_live_app_logs(default_host_name, max_lines=max_lines)

    def _keep(line: str) -> bool:
        level, _ = _detect_level(line)
        level_ok = (level in selected_levels) if level else show_unlabeled
        search_ok = not search_query or search_query.lower() in line.lower()
        return level_ok and search_ok

    filtered = [line for line in raw_lines if _keep(line)]

    status_cols = st.columns([1, 1, 1, 3])
    with status_cols[0]:
        st.metric("Lines shown", len(filtered))
    with status_cols[1]:
        st.metric("Total fetched", len(raw_lines))
    with status_cols[2]:
        error_count = sum(1 for line in raw_lines if _detect_level(line)[0] in ("error", "critical"))
        st.metric("Errors/Critical", error_count)
    with status_cols[3]:
        st.download_button(
            "⬇️ Download shown logs",
            data="\n".join(filtered),
            file_name=f"{app_name}_{lines_label.replace(' ', '_')}.txt",
            mime="text/plain",
            use_container_width=True,
        )

    provider_error = resource_service.app_service_logs_provider.last_error
    if provider_error:
        st.error(f"Couldn't fetch logs: {provider_error}")
    elif not raw_lines:
        st.info(
            "No container log file found for this app yet - it may not be running as a "
            "container, or hasn't logged anything yet."
        )
    elif not filtered:
        st.info("No log lines match the current filters.")
    else:
        html = '<div class="live-log-container">' + "".join(
            _render_line(line, search_query) for line in filtered
        ) + "</div>"
        st.markdown(html, unsafe_allow_html=True)

    st.caption(f"Last refreshed {datetime.now().strftime('%H:%M:%S')} · reading {app_name}'s live container log file.")

    if auto_refresh and not refresh_clicked:
        time.sleep(_AUTO_REFRESH_SECONDS)
        st.rerun()
