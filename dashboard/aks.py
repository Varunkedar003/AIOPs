import streamlit as st
from typing import Any, Dict, List, Optional

from utils.resource_id import resource_ids_match


def _status_color(status: str) -> str:
    status_lower = (status or "").lower()
    if status_lower in ("running", "ready", "healthy", "active", "succeeded"):
        return "green"
    if status_lower in ("pending", "degraded", "notready", "unschedulable"):
        return "orange"
    if status_lower in ("failed", "unknown", "crashloopbackoff"):
        return "red"
    return "blue"


def render_cluster_selector(resource_service: Any) -> Optional[Dict[str, Any]]:
    """Cluster picker; returns the selected cluster dict, or None if no clusters exist."""
    clusters = resource_service.get_aks_clusters()
    if not clusters:
        st.info("No AKS clusters discovered in this subscription.")
        return None

    options = {cluster["name"]: cluster for cluster in clusters}
    names = list(options.keys())

    selected_resource_id = st.session_state.get("selected_resource_id")
    # resource_ids_match (not a raw `==`) because the cluster's ARM id here comes from the
    # ContainerService SDK while `selected_resource_id` (carried over from the Infrastructure
    # Explorer/Resource Workspace) comes from Azure Resource Graph - the two APIs don't guarantee
    # identical casing for the same resource id, so an exact string comparison can silently fail
    # to find the cluster that was actually clicked and fall back to the first one in the list.
    default_name = next(
        (c["name"] for c in clusters if resource_ids_match(c.get("id"), selected_resource_id)),
        names[0],
    )

    selected_name = st.selectbox("Cluster", options=names, index=names.index(default_name))
    return options[selected_name]


def render_cluster_overview(cluster: Dict[str, Any]) -> None:
    """ARM-level cluster metadata (Azure Resource Manager, not the Kubernetes API) - always
    available regardless of whether the cluster's own API server is reachable."""
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Kubernetes Version", cluster.get("kubernetes_version") or "Unknown")
    with col2:
        st.metric("Node Count", cluster.get("node_count", 0))
    with col3:
        status = cluster.get("provisioning_state", "Unknown")
        st.markdown("**Provisioning State**")
        st.markdown(f":{_status_color(status)}[{status}]")
    with col4:
        st.markdown("**Resource Group**")
        st.markdown(cluster.get("resource_group") or "Unknown")

    col5, col6 = st.columns(2)
    with col5:
        st.markdown("**API Server FQDN**")
        st.markdown(f"`{cluster.get('fqdn') or 'Unknown'}`")
    with col6:
        st.markdown("**Node Resource Group**")
        st.markdown(cluster.get("node_resource_group") or "Unknown")

    node_pools = cluster.get("node_pools") or []
    if node_pools:
        st.markdown("**Node Pools**")
        rows = [
            {"Pool": pool.get("name"), "Node Count": pool.get("count"), "VM Size": pool.get("vm_size")}
            for pool in node_pools
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)


def render_namespace_table(namespaces: List[Dict[str, Any]]) -> None:
    if not namespaces:
        st.info("No namespace data available.")
        return
    rows = [
        {"Namespace": ns.get("name"), "Status": ns.get("status"), "Created": ns.get("created_at")}
        for ns in namespaces
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_nodes_tab(resource_service: Any, cluster_id: str) -> None:
    nodes = resource_service.get_cluster_nodes(cluster_id)
    if not nodes:
        st.info("No node data available.")
        return

    unhealthy = [n for n in nodes if not n.get("is_healthy")]
    if unhealthy:
        st.warning(f"⚠️ {len(unhealthy)} node(s) not Ready: {', '.join(n['name'] for n in unhealthy)}")

    rows = [
        {
            "Node": n.get("name"),
            "Status": n.get("status"),
            "Kubelet Version": n.get("kubelet_version"),
            "OS Image": n.get("os_image"),
            "CPU": n.get("capacity_cpu"),
            "Memory": n.get("capacity_memory"),
        }
        for n in nodes
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_deployments_tab(resource_service: Any, cluster_id: str, namespace: Optional[str]) -> None:
    deployments = resource_service.get_cluster_deployments(cluster_id, namespace)
    if not deployments:
        st.info("No deployment data available.")
        return

    unhealthy = [d for d in deployments if not d.get("is_healthy")]
    if unhealthy:
        st.warning(f"⚠️ {len(unhealthy)} deployment(s) degraded: {', '.join(d['name'] for d in unhealthy)}")

    rows = [
        {
            "Deployment": d.get("name"),
            "Namespace": d.get("namespace"),
            "Status": d.get("status"),
            "Ready": f"{d.get('ready_replicas', 0)}/{d.get('replicas', 0)}",
            "Available": d.get("available_replicas"),
            "Image": d.get("image"),
        }
        for d in deployments
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_replicasets_tab(resource_service: Any, cluster_id: str, namespace: Optional[str]) -> None:
    replicasets = resource_service.get_cluster_replicasets(cluster_id, namespace)
    if not replicasets:
        st.info("No ReplicaSet data available.")
        return

    unhealthy = [rs for rs in replicasets if not rs.get("is_healthy")]
    if unhealthy:
        st.warning(f"⚠️ {len(unhealthy)} ReplicaSet(s) under-provisioned.")

    rows = [
        {
            "ReplicaSet": rs.get("name"),
            "Namespace": rs.get("namespace"),
            "Replicas": rs.get("replicas"),
            "Available": rs.get("available_replicas"),
            "Owner": rs.get("owner") or "-",
        }
        for rs in replicasets
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_pods_tab(resource_service: Any, cluster_id: str, namespace: Optional[str]) -> List[Dict[str, Any]]:
    pods = resource_service.get_cluster_pods(cluster_id, namespace)
    if not pods:
        st.info("No pod data available.")
        return []

    unhealthy = [p for p in pods if not p.get("is_healthy")]
    if unhealthy:
        st.warning(f"⚠️ {len(unhealthy)} pod(s) unhealthy (not Running/Ready, or restarting).")

    rows = [
        {
            "Pod": p.get("name"),
            "Namespace": p.get("namespace"),
            "Status": p.get("phase"),
            "Ready": p.get("ready"),
            "Restarts": p.get("restart_count"),
            "Node": p.get("node"),
        }
        for p in pods
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    return pods


def render_services_tab(resource_service: Any, cluster_id: str, namespace: Optional[str]) -> None:
    services = resource_service.get_cluster_services(cluster_id, namespace)
    if not services:
        st.info("No service data available.")
        return

    rows = [
        {
            "Service": s.get("name"),
            "Namespace": s.get("namespace"),
            "Type": s.get("type"),
            "Cluster IP": s.get("cluster_ip"),
            "External IP": s.get("external_ip") or "-",
            "Ports": ", ".join(s.get("ports", [])),
        }
        for s in services
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_ingress_tab(resource_service: Any, cluster_id: str, namespace: Optional[str]) -> None:
    ingresses = resource_service.get_cluster_ingress(cluster_id, namespace)
    if not ingresses:
        st.info("No ingress data available.")
        return

    rows = [
        {
            "Ingress": i.get("name"),
            "Namespace": i.get("namespace"),
            "Class": i.get("class_name") or "-",
            "Hosts": ", ".join(i.get("hosts", [])) or "-",
            "Address": i.get("address") or "-",
        }
        for i in ingresses
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_events_tab(resource_service: Any, cluster_id: str, namespace: Optional[str]) -> None:
    events = resource_service.get_cluster_events(cluster_id, namespace)
    if not events:
        st.info("No recent events.")
        return

    warning_count = len([e for e in events if e.get("type") == "Warning"])
    if warning_count:
        st.warning(f"⚠️ {warning_count} warning event(s) in the selected scope.")

    rows = [
        {
            "Last Seen": e.get("last_seen"),
            "Type": e.get("type"),
            "Reason": e.get("reason"),
            "Object": e.get("involved_object"),
            "Message": e.get("message"),
            "Count": e.get("count"),
        }
        for e in events[:200]
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_logs_tab(resource_service: Any, cluster_id: str, pods: List[Dict[str, Any]]) -> None:
    if not pods:
        st.info("No pods available to fetch logs from.")
        return

    pod_options = {f"{p['namespace']}/{p['name']}": p for p in pods}
    selected_key = st.selectbox("Pod", options=list(pod_options.keys()), key=f"aks_log_pod_{cluster_id}")
    pod = pod_options[selected_key]

    containers = pod.get("containers") or []
    container = st.selectbox("Container", options=containers, key=f"aks_log_container_{cluster_id}") if len(containers) > 1 else (containers[0] if containers else None)

    if st.button("Fetch Latest Logs", key=f"aks_log_fetch_{cluster_id}"):
        result = resource_service.get_pod_logs(cluster_id, pod["namespace"], pod["name"], container=container)
        if result.get("error"):
            st.error(result["error"])
        elif result.get("logs"):
            st.code(result["logs"], language="text")
        else:
            st.info("No logs available.")

    st.markdown("#### Pod Events")
    events = resource_service.get_pod_events(cluster_id, pod["namespace"], pod["name"])
    if not events:
        st.info("No events for this pod.")
        return

    rows = [
        {
            "Last Seen": e.get("last_seen"),
            "Type": e.get("type"),
            "Reason": e.get("reason"),
            "Message": e.get("message"),
        }
        for e in events
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
