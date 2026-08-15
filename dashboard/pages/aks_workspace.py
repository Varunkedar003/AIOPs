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
)
from utils.k8s_safety import AKSUnreachableError, is_private_cluster

_UNREACHABLE_MESSAGE = (
    "This AKS cluster uses a private API endpoint and is not reachable from the current "
    "network. Connect through VPN, Azure Bastion, ExpressRoute, or a VM inside the VNet to "
    "browse namespaces, pods, and workloads."
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
    "unknown": (
        "Could not reach the cluster's Kubernetes API right now. Showing ARM-level cluster "
        "metadata only."
    ),
}


def render_aks_workspace() -> None:
    """AKS Workspace: live, read-only Kubernetes cluster data for a selected AKS cluster."""
    st.markdown("## AKS Workspace")
    st.caption(
        "Live cluster data (read-only): namespaces, nodes, workloads, services, ingress, events, and pod logs."
    )

    resource_service = st.session_state.resource_service
    cluster = render_cluster_selector(resource_service)
    if not cluster:
        return

    cluster_id = cluster["id"]
    render_cluster_overview(cluster)
    st.markdown("---")

    if is_private_cluster(cluster):
        st.warning(_UNREACHABLE_MESSAGE)
        return

    try:
        namespaces = resource_service.get_cluster_namespaces(cluster_id)
    except AKSUnreachableError as exc:
        st.warning(_ERROR_MESSAGES.get(exc.reason, _ERROR_MESSAGES["unknown"]))
        return

    namespace_options = ["All namespaces"] + [ns.get("name") for ns in namespaces if ns.get("name")]
    selected_namespace = st.selectbox("Namespace", options=namespace_options, key=f"aks_namespace_{cluster_id}")
    namespace = None if selected_namespace == "All namespaces" else selected_namespace

    (
        tab_namespaces, tab_nodes, tab_deployments, tab_replicasets,
        tab_pods, tab_services, tab_ingress, tab_events, tab_logs,
    ) = st.tabs(
        ["Namespaces", "Nodes", "Deployments", "ReplicaSets", "Pods", "Services", "Ingress", "Events", "Pod Logs"]
    )

    try:
        with tab_namespaces:
            render_namespace_table(namespaces)

        with tab_nodes:
            render_nodes_tab(resource_service, cluster_id)

        with tab_deployments:
            render_deployments_tab(resource_service, cluster_id, namespace)

        with tab_replicasets:
            render_replicasets_tab(resource_service, cluster_id, namespace)

        with tab_pods:
            pods = render_pods_tab(resource_service, cluster_id, namespace)

        with tab_services:
            render_services_tab(resource_service, cluster_id, namespace)

        with tab_ingress:
            render_ingress_tab(resource_service, cluster_id, namespace)

        with tab_events:
            render_events_tab(resource_service, cluster_id, namespace)

        with tab_logs:
            render_logs_tab(resource_service, cluster_id, pods)
    except AKSUnreachableError as exc:
        st.warning(_ERROR_MESSAGES.get(exc.reason, _ERROR_MESSAGES["unknown"]))
