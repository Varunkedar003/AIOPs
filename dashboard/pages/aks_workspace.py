import streamlit as st

from dashboard.aks import (
    render_cluster_selector,
    render_cluster_overview,
    render_namespace_table,
    render_nodes_tab,
    render_deployments_tab,
    render_replicasets_tab,
    render_pods_tab,
    render_services_tab,
    render_ingress_tab,
    render_events_tab,
    render_logs_tab,
    render_investigation_tab,
    render_namespace_investigation,
)
from utils.k8s_safety import AKSUnreachableError

# Each view maps to exactly one resource_service call, so there's nothing left to batch
# concurrently within a view (dashboard.aks.run_concurrent_fetches - added for the previous,
# fetch-everything-up-front design - is no longer called from this page for that reason, though
# it's still defined there and safe to reuse elsewhere).
_VIEWS = [
    "Namespaces", "Nodes", "Deployments", "ReplicaSets", "Pods",
    "Services", "Ingress", "Events", "Pod Logs", "Investigation",
]

# Icons + one-line, jargon-free explanations shown next to the view picker - purely cosmetic
# (the underlying `_VIEWS` strings, session-state keys, and if/elif dispatch below are
# unchanged), so both a Kubernetes engineer and a non-technical stakeholder can tell at a glance
# what each view is for before clicking into it.
_VIEW_ICONS = {
    "Namespaces": "🗂️", "Nodes": "🖥️", "Deployments": "🚀", "ReplicaSets": "🔁", "Pods": "📦",
    "Services": "🌐", "Ingress": "🔀", "Events": "📋", "Pod Logs": "📜", "Investigation": "🔎",
}
_VIEW_HELP = {
    "Namespaces": "Namespaces are folders inside the cluster used to keep different apps/teams separate.",
    "Nodes": "Nodes are the virtual machines that actually run your apps.",
    "Deployments": "Deployments describe how many copies (replicas) of an app should be running.",
    "ReplicaSets": "ReplicaSets are what Deployments use behind the scenes to keep the right number of copies alive.",
    "Pods": "Pods are the running instances of your app - the smallest unit Kubernetes manages.",
    "Services": "Services give a stable network address to a group of pods.",
    "Ingress": "Ingress rules route external web traffic into services inside the cluster.",
    "Events": "Events are Kubernetes' own recent-history log - scheduling, failures, restarts.",
    "Pod Logs": "Pod Logs show the console output written by an app inside a pod.",
    "Investigation": "Runs an automated health check and explains any problem found, with evidence and a fix.",
}

_UNREACHABLE_MESSAGE = (
    "This AKS cluster uses a private API endpoint and is not reachable from the current "
    "network, and the AKS Run Command fallback could not retrieve data either. Connect through "
    "VPN, Azure Bastion, ExpressRoute, or a VM inside the VNet to browse namespaces, pods, and "
    "workloads directly."
)

_ERROR_MESSAGES = {
    "dns": _UNREACHABLE_MESSAGE,
    "timeout": (
        "Timed out waiting for the cluster's Kubernetes API server. It may be a private "
        "endpoint or otherwise unreachable from this network. " + _UNREACHABLE_MESSAGE
    ),
    "auth": (
        "Authentication to the cluster's Kubernetes API failed (the current identity may lack "
        "the Azure Kubernetes Service Cluster User Role, or the cluster requires Azure AD/RBAC "
        "steps not completed here). Showing ARM-level cluster metadata only."
    ),
    "network": _UNREACHABLE_MESSAGE,
    "run_command_forbidden": (
        "This is a private AKS cluster. Direct Kubernetes API access isn't available, and the "
        "app's Azure identity is not authorized to use AKS Run Command "
        "(`Microsoft.ContainerService/managedClusters/runCommand/action`) on this cluster. "
        "Grant that permission (e.g. the built-in \"Azure Kubernetes Service Cluster User "
        "Role\") to the app's identity to see live cluster data here. Showing ARM-level cluster "
        "metadata only."
    ),
    "run_command_failed": (
        "This is a private AKS cluster. Live data was requested via AKS Run Command, but the "
        "attempt failed (see logs for detail). Showing ARM-level cluster metadata only."
    ),
    "blocked_command": (
        "A cluster data request was blocked by the read-only command safety check and never "
        "reached Azure. Showing ARM-level cluster metadata only."
    ),
    "scope_too_broad": (
        "This cluster has too much data of this type to fetch for **all namespaces** through "
        "AKS Run Command (Azure enforces a 512 KB output limit per call). Pick a specific "
        "namespace above to view it."
    ),
    "namespace_too_large": (
        "Even scoped to the selected namespace, this data is too large to fetch through AKS "
        "Run Command (Azure enforces a 512 KB output limit per call). Try a different "
        "namespace, or use `kubectl` directly from within the cluster's network."
    ),
    "nodes_unavailable": (
        "This cluster's node list is too large to retrieve through AKS Run Command, even in a "
        "reduced form. Showing ARM-level cluster metadata only."
    ),
    "unknown": (
        "Could not reach the cluster's Kubernetes API right now. Showing ARM-level cluster "
        "metadata only."
    ),
}


def _show_error(exc: AKSUnreachableError) -> None:
    st.warning(_ERROR_MESSAGES.get(exc.reason, _ERROR_MESSAGES["unknown"]))


def render_aks_workspace() -> None:
    """AKS Workspace: live, read-only Kubernetes cluster data for a selected AKS cluster.

    True lazy loading: only ARM cluster metadata (render_cluster_overview - already free, no
    Run Command call) and the namespace list are fetched on initial load. Nodes/deployments/
    replicasets/pods/services/ingress/events are fetched only when their view is actually
    selected below via `st.radio` - not `st.tabs`. Tabs can't do this: switching tabs is a
    client-side-only visibility toggle that never reruns the script, so every tab body (and
    every fetch inside it) runs on every load regardless of which tab is visible. A radio/
    selectbox/segmented-control's value change *does* rerun the script, so the plain
    if/elif chain below only ever calls into resource_service for the branch matching the
    current selection - every other kind's fetch code simply isn't reached this run.
    """
    st.markdown("## AKS Workspace")
    st.caption(
        "Live cluster data (read-only). Select a view below to load it - only that view's data "
        "is fetched; switching back to a previously-viewed one reuses the cached result for the "
        "rest of this session. If something changed in the cluster since (a new deploy, a pod "
        "restart) and a view looks out of date, use **Refresh** below to force a live re-check. "
        "**Investigation** (both cluster-wide and per-namespace) always fetches live and never "
        "needs a manual refresh."
    )

    resource_service = st.session_state.resource_service
    cluster = render_cluster_selector(resource_service)
    if not cluster:
        return

    cluster_id = cluster["id"]
    render_cluster_overview(cluster)  # ARM metadata only - no Run Command call

    if st.button("🔄 Refresh cluster data", key=f"aks_refresh_{cluster_id}"):
        resource_service.invalidate_resource_cache(cluster_id)
        st.rerun()

    st.markdown("---")

    # Initial load: namespaces only - the one Run Command call that can't be deferred, since
    # the namespace filter below (and several views) need to know which namespaces exist.
    # Nothing else is fetched here.
    try:
        namespaces = resource_service.get_cluster_namespaces(cluster_id)
    except AKSUnreachableError as exc:
        _show_error(exc)
        st.markdown("---")
        render_investigation_tab(resource_service, cluster_id)
        return

    namespace_options = ["All namespaces"] + [ns.get("name") for ns in namespaces if ns.get("name")]
    selected_namespace = st.selectbox("Namespace", options=namespace_options, key=f"aks_namespace_{cluster_id}")
    namespace = None if selected_namespace == "All namespaces" else selected_namespace

    view = st.radio(
        "View", _VIEWS, horizontal=True, key=f"aks_view_{cluster_id}",
        format_func=lambda v: f"{_VIEW_ICONS.get(v, '')} {v}",
    )
    st.caption(_VIEW_HELP.get(view, ""))
    st.markdown("---")

    # Only the branch matching `view` fetches anything this run. Whatever was already fetched
    # for a given (cluster, kind, namespace) stays in ResourceService's/AzureAKS's existing
    # cache (see services/resource_service.py, providers/azure/aks.py) - switching to a
    # different view and back is a cache hit, not a new Run Command call. A failure fetching
    # one view never affects any other view (each is independent and only fetched on demand).
    if view == "Namespaces":
        render_namespace_table(namespaces)

    elif view == "Nodes":
        try:
            render_nodes_tab(resource_service.get_cluster_nodes(cluster_id))
        except AKSUnreachableError as exc:
            _show_error(exc)

    elif view == "Deployments":
        try:
            render_deployments_tab(resource_service.get_cluster_deployments(cluster_id, namespace))
        except AKSUnreachableError as exc:
            _show_error(exc)

    elif view == "ReplicaSets":
        try:
            render_replicasets_tab(resource_service.get_cluster_replicasets(cluster_id, namespace))
        except AKSUnreachableError as exc:
            _show_error(exc)

    elif view == "Pods":
        try:
            render_pods_tab(resource_service.get_cluster_pods(cluster_id, namespace))
        except AKSUnreachableError as exc:
            _show_error(exc)

    elif view == "Services":
        try:
            render_services_tab(resource_service.get_cluster_services(cluster_id, namespace))
        except AKSUnreachableError as exc:
            _show_error(exc)

    elif view == "Ingress":
        try:
            render_ingress_tab(resource_service.get_cluster_ingress(cluster_id, namespace))
        except AKSUnreachableError as exc:
            _show_error(exc)

    elif view == "Events":
        try:
            render_events_tab(resource_service.get_cluster_events(cluster_id, namespace))
        except AKSUnreachableError as exc:
            _show_error(exc)

    elif view == "Pod Logs":
        # Listing pods (to populate the pod picker) is a normal read-only list fetch - the same
        # call the Pods view makes, sharing its cache. Only the actual log content stays
        # explicitly behind the "Fetch Latest Logs" button inside render_logs_tab.
        try:
            pods = resource_service.get_cluster_pods(cluster_id, namespace)
        except AKSUnreachableError as exc:
            _show_error(exc)
        else:
            try:
                render_logs_tab(resource_service, cluster_id, pods)
            except AKSUnreachableError as exc:
                _show_error(exc)

    elif view == "Investigation":
        render_investigation_tab(resource_service, cluster_id)
        st.markdown("---")
        render_namespace_investigation(resource_service, cluster_id, namespace)
