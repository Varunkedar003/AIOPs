import logging
import streamlit as st
import sys
from pathlib import Path

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Without this, the [azure-timing] INFO-level logs in providers/azure/ (query duration, etc.)
# are silently dropped - the root logger has no handler by default, so logger.info() goes
# nowhere. basicConfig() only takes effect once per process (Streamlit reruns this script on
# every interaction, but repeated calls are a no-op once the root logger already has a handler).
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from dashboard import render_sidebar
from dashboard.pages.registry import ALL_PAGES, INFRASTRUCTURE_EXPLORER, AKS_WORKSPACE, GITLAB, FINOPS
from services.resource_service import ResourceService

# Initialize session state
if 'selected_resource_id' not in st.session_state:
    st.session_state.selected_resource_id = None
if 'previous_resource_id' not in st.session_state:
    st.session_state.previous_resource_id = None
if 'navigation_history' not in st.session_state:
    st.session_state.navigation_history = []
if 'history_index' not in st.session_state:
    st.session_state.history_index = -1
if 'resource_service' not in st.session_state:
    st.session_state.resource_service = ResourceService()

st.set_page_config(
    page_title="AIOps Commander",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

pg = st.navigation(ALL_PAGES)

# Azure Subscription Explorer sidebar: available on every page except the Infrastructure
# Explorer home page (which shows the full topology instead), the AKS/GitLab workspaces
# (which have their own dedicated cluster/project picker and don't need a second, unrelated
# resource tree alongside it), and FinOps (a subscription-wide cost/utilization view - the
# resource tree isn't part of its workflow and only crowded the page).
NO_SIDEBAR_PAGES = (INFRASTRUCTURE_EXPLORER, AKS_WORKSPACE, GITLAB, FINOPS)
if not any(pg is page for page in NO_SIDEBAR_PAGES):
    with st.sidebar:
        st.markdown("# AIOps Commander")
        st.caption("AI Powered Cloud Operations Platform")
        st.markdown("---")
        render_sidebar()
        st.markdown("---")
        st.caption("v0.1 Local Development")

pg.run()
