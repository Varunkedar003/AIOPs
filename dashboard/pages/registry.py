import streamlit as st

from dashboard.pages.infrastructure_explorer import render_infrastructure_explorer
from dashboard.pages.resource_workspace import render_resource_workspace
from dashboard.pages.ai_copilot import render_ai_copilot
from dashboard.pages.monitoring import render_monitoring
from dashboard.pages.finops import render_finops
from dashboard.pages.aks_workspace import render_aks_workspace
from dashboard.pages.gitlab_workspace import render_gitlab_workspace
from dashboard.pages.settings import render_settings

INFRASTRUCTURE_EXPLORER = st.Page(
    render_infrastructure_explorer,
    title="Infrastructure Explorer",
    icon="🗺️",
    url_path="infrastructure-explorer",
    default=True,
)
RESOURCE_WORKSPACE = st.Page(
    render_resource_workspace,
    title="Resource Workspace",
    icon="🧰",
    url_path="resource-workspace",
)
AI_COPILOT = st.Page(
    render_ai_copilot,
    title="AI Operations Copilot",
    icon="🤖",
    url_path="ai-copilot",
)
MONITORING = st.Page(
    render_monitoring,
    title="Monitoring & Observability",
    icon="📈",
    url_path="monitoring",
)
FINOPS = st.Page(
    render_finops,
    title="FinOps",
    icon="💰",
    url_path="finops",
)
AKS_WORKSPACE = st.Page(
    render_aks_workspace,
    title="AKS Workspace",
    icon="☸️",
    url_path="aks-workspace",
)
GITLAB = st.Page(
    render_gitlab_workspace,
    title="GitLab",
    icon="🦊",
    url_path="gitlab",
)
SETTINGS = st.Page(
    render_settings,
    title="Settings",
    icon="⚙️",
    url_path="settings",
)

ALL_PAGES = [
    INFRASTRUCTURE_EXPLORER,
    RESOURCE_WORKSPACE,
    AI_COPILOT,
    MONITORING,
    FINOPS,
    AKS_WORKSPACE,
    GITLAB,
    SETTINGS,
]
