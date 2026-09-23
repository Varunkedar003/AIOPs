"""Azure subscription picker - lets a developer switch which subscription the whole
app queries (Resource Graph discovery, AKS clusters, Cost Management, Monitor/alerts/
Log Analytics) without restarting the app or editing .env.

Single-select, not a merged "both" view - Resource Graph could technically query both
subscriptions in one call (see providers/azure/resource_graph.py), but AKS's
ContainerServiceClient and Cost Management's per-scope calls each only ever bind to one
subscription at a time, and mixing "some data is both, some is only the active one" would
be a confusing, half-multi-subscription experience. Pick one, see everything for it.

Staging and Production are two DIFFERENT Service Principals, not one shared SP used
against two subscriptions - each entry below carries its own client_id/secret from
Config, and the same AAD tenant (Config.AZURE_TENANT_ID) for both.
"""
from typing import Optional

import streamlit as st

from config import Config
from providers.azure.auth import AzureAuth

# (label, subscription ID, client_id, client_secret). Order matters - first entry is
# the fallback/default if the stored session choice is ever invalid (e.g. list edited
# later).
SUBSCRIPTIONS = [
    (
        "OptimusX Dev & Stage (Staging)",
        "17187d06-46b6-402b-a569-f0ecd2b5b968",
        Config.AZURE_CLIENT_ID_STAGING,
        Config.AZURE_CLIENT_SECRET_STAGING,
    ),
    (
        "OptimusX Production",
        "fc3917a9-6bd1-49f6-9b62-30d651321528",
        Config.AZURE_CLIENT_ID_PRODUCTION,
        Config.AZURE_CLIENT_SECRET_PRODUCTION,
    ),
]

# Defaults to Staging, not Production - this is a developer-facing tool, and staging is
# where day-to-day investigation happens; switching to Production is an explicit choice.
_DEFAULT_SUBSCRIPTION_ID = SUBSCRIPTIONS[0][1]


def _entry_for(subscription_id: str):
    for entry in SUBSCRIPTIONS:
        if entry[1] == subscription_id:
            return entry
    return SUBSCRIPTIONS[0]


def _label_for(subscription_id: str) -> str:
    return _entry_for(subscription_id)[0]


def build_auth_for(subscription_id: str) -> AzureAuth:
    """Build the AzureAuth for a given subscription, using THAT subscription's own
    Service Principal (see module docstring - Staging and Production are not the same
    SP), sharing only the tenant."""
    _, sub_id, client_id, client_secret = _entry_for(subscription_id)
    return AzureAuth(
        tenant_id=Config.AZURE_TENANT_ID,
        client_id=client_id,
        client_secret=client_secret,
        subscription_id=sub_id,
        subscription_ids=[sub_id],
    )


def render_subscription_picker() -> None:
    """Render the picker (call once per page, inside st.sidebar, before anything that
    reads st.session_state.resource_service) and rebuild the session's ResourceService
    if the user just switched subscriptions.
    """
    if "active_subscription_id" not in st.session_state:
        st.session_state.active_subscription_id = _DEFAULT_SUBSCRIPTION_ID

    labels = [entry[0] for entry in SUBSCRIPTIONS]
    current_index = next(
        (i for i, entry in enumerate(SUBSCRIPTIONS) if entry[1] == st.session_state.active_subscription_id),
        0,
    )

    chosen_label = st.selectbox("🔀 Subscription", labels, index=current_index, key="subscription_picker")
    chosen_id = next(entry[1] for entry in SUBSCRIPTIONS if entry[0] == chosen_label)

    if not _entry_for(chosen_id)[2]:
        st.warning(
            f"No Service Principal configured for **{chosen_label}** yet - set "
            f"AZURE_CLIENT_ID_{'STAGING' if chosen_id == SUBSCRIPTIONS[0][1] else 'PRODUCTION'} / "
            f"_SECRET in .env.",
            icon="⚠️",
        )

    if chosen_id != st.session_state.active_subscription_id:
        st.session_state.active_subscription_id = chosen_id
        # Rebuild every Azure-backed provider against the newly-chosen subscription (and
        # its own SP) - ResourceService.__init__ wires this one AzureAuth through
        # Resource Graph, AKS, Cost Management, and Monitor/Alerts/Log Analytics
        # consistently (see its docstring).
        from services.resource_service import ResourceService
        st.session_state.resource_service = ResourceService(azure_auth=build_auth_for(chosen_id))
        # A resource selected under the old subscription won't exist under the new one -
        # clearing it avoids the detail page rendering a stale/foreign resource, or erroring
        # trying to resolve an ID that's genuinely gone from this subscription's inventory.
        st.session_state.selected_resource_id = None
        st.session_state.previous_resource_id = None
        st.session_state.navigation_history = []
        st.session_state.history_index = -1
        st.rerun()


def get_active_subscription_label() -> str:
    """The currently active subscription's display label, for showing elsewhere (e.g. a
    page header) without every caller needing to know the SUBSCRIPTIONS list itself."""
    active_id = st.session_state.get("active_subscription_id", _DEFAULT_SUBSCRIPTION_ID)
    return _label_for(active_id)
