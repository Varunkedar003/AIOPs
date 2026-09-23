import logging
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from agents.aks_agent import AKSAgent
from agents.crew import InvestigationCrew
from synthesis.claude_synthesizer import ClaudeSynthesizer
from utils.k8s_safety import AKSUnreachableError
from utils.resource_id import resource_ids_match

logger = logging.getLogger(__name__)

# Bounded, not one thread per namespace/resource-kind call - see run_concurrent_fetches().
_WORKSPACE_FETCH_CONCURRENCY = 3


def run_concurrent_fetches(
    jobs: Dict[str, Callable[[], Any]], max_workers: int = _WORKSPACE_FETCH_CONCURRENCY
) -> Dict[str, Tuple[Optional[Any], Optional[AKSUnreachableError]]]:
    """Run independent, read-only AKS data fetches concurrently instead of one after another.

    Each entry in `jobs` is an already-bound call into ResourceService (e.g. `lambda:
    resource_service.get_cluster_nodes(cluster_id)`) - every existing safety mechanism still
    applies unchanged underneath: the per-kind/per-namespace cache, the Run-Command-aware
    timeout, truncation detection, and the read-only kubectl allow-list (see
    services/resource_service.py, providers/azure/aks.py, providers/azure/aks_run_command.py).
    This function only decides *when* those already-safe calls run relative to each other.

    Concurrency is bounded (default 3, matching AKS Run Command's own effective throughput
    rather than firing every call - or every namespace - at once) and each job is isolated: one
    job raising AKSUnreachableError never affects any other job's result, and this never calls
    into Streamlit (`st.*`) or touches session state - it only fetches data on worker threads
    and hands plain results back to the caller, which renders on the main thread.

    Returns {job_name: (result, None)} on success or {job_name: (None, exception)} on failure.
    """
    results: Dict[str, Tuple[Optional[Any], Optional[AKSUnreachableError]]] = {}

    def _run(fn: Callable[[], Any]) -> Tuple[Optional[Any], Optional[AKSUnreachableError]]:
        try:
            return fn(), None
        except AKSUnreachableError as exc:
            return None, exc

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aks-workspace-fetch") as pool:
        future_to_name = {pool.submit(_run, fn): name for name, fn in jobs.items()}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            # _run() already caught AKSUnreachableError itself - .result() only re-raises for a
            # genuinely unexpected exception, which we deliberately let propagate rather than
            # mask (matches the pre-concurrency behavior of every other AKS call in this app).
            results[name] = future.result()
    return results


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


def render_namespace_table(namespaces: List[Dict[str, Any]]) -> None:
    if not namespaces:
        st.info("No namespace data available.")
        return
    rows = [
        {"Namespace": ns.get("name"), "Status": ns.get("status"), "Created": ns.get("created_at")}
        for ns in namespaces
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_nodes_tab(nodes: List[Dict[str, Any]]) -> None:
    """Renders pre-fetched node data - the fetch itself now happens concurrently in
    dashboard/pages/aks_workspace.py via run_concurrent_fetches(), not here, so this function
    never blocks on (or is called from) anything but the Streamlit main thread."""
    if not nodes:
        st.info("No node data available.")
        return

    unhealthy = [n for n in nodes if not n.get("is_healthy")]
    if unhealthy:
        st.warning(f"⚠️ {len(unhealthy)} node(s) not Ready: {', '.join(n['name'] for n in unhealthy)}")
    else:
        st.success(f"✅ All {len(nodes)} node(s) Ready.")

    rows = [
        {
            "Health": "✅" if n.get("is_healthy") else "❌",
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


def render_deployments_tab(deployments: List[Dict[str, Any]]) -> None:
    """Renders pre-fetched deployment data - see render_nodes_tab's docstring."""
    if not deployments:
        st.info("No deployment data available.")
        return

    unhealthy = [d for d in deployments if not d.get("is_healthy")]
    if unhealthy:
        st.warning(f"⚠️ {len(unhealthy)} deployment(s) degraded: {', '.join(d['name'] for d in unhealthy)}")
    else:
        st.success(f"✅ All {len(deployments)} deployment(s) healthy.")

    rows = [
        {
            "Health": "✅" if d.get("is_healthy") else "❌",
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


def render_replicasets_tab(replicasets: List[Dict[str, Any]]) -> None:
    """Renders pre-fetched ReplicaSet data - see render_nodes_tab's docstring."""
    if not replicasets:
        st.info("No ReplicaSet data available.")
        return

    unhealthy = [rs for rs in replicasets if not rs.get("is_healthy")]
    if unhealthy:
        st.warning(f"⚠️ {len(unhealthy)} ReplicaSet(s) under-provisioned.")
    else:
        st.success(f"✅ All {len(replicasets)} ReplicaSet(s) fully provisioned.")

    rows = [
        {
            "Health": "✅" if rs.get("is_healthy") else "❌",
            "ReplicaSet": rs.get("name"),
            "Namespace": rs.get("namespace"),
            "Replicas": rs.get("replicas"),
            "Available": rs.get("available_replicas"),
            "Owner": rs.get("owner") or "-",
        }
        for rs in replicasets
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_pods_tab(pods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Renders pre-fetched pod data and returns it (for the Logs tab's pod picker) - see
    render_nodes_tab's docstring."""
    if not pods:
        st.info("No pod data available.")
        return []

    unhealthy = [p for p in pods if not p.get("is_healthy")]
    if unhealthy:
        st.warning(f"⚠️ {len(unhealthy)} pod(s) unhealthy (not Running/Ready, or restarting).")
    else:
        st.success(f"✅ All {len(pods)} pod(s) healthy.")

    rows = [
        {
            "Health": "✅" if p.get("is_healthy") else "❌",
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


def render_services_tab(services: List[Dict[str, Any]]) -> None:
    """Renders pre-fetched service data - see render_nodes_tab's docstring."""
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


def render_ingress_tab(ingresses: List[Dict[str, Any]]) -> None:
    """Renders pre-fetched ingress data - see render_nodes_tab's docstring."""
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


def render_events_tab(events: List[Dict[str, Any]]) -> None:
    """Renders pre-fetched event data - see render_nodes_tab's docstring."""
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


def _get_aks_investigation_agent() -> AKSAgent:
    """Reuses the same AKSAgent the chatbot's "aks" capability uses (see workflow/graph.py's
    orchestrator registration) - cached in session_state so this page doesn't rebuild it (and
    its own MockAKSProvider) on every rerun."""
    if "aks_investigation_agent" not in st.session_state:
        st.session_state["aks_investigation_agent"] = AKSAgent()
    return st.session_state["aks_investigation_agent"]


def _get_aks_investigation_crew() -> InvestigationCrew:
    """Reuses the same InvestigationCrew (and its aks_investigation domain agent) the chatbot's
    Agents stage runs (see workflow/graph.py)."""
    if "aks_investigation_crew" not in st.session_state:
        st.session_state["aks_investigation_crew"] = InvestigationCrew()
    return st.session_state["aks_investigation_crew"]


def _get_aks_claude_synthesizer() -> ClaudeSynthesizer:
    """Reuses the same ClaudeSynthesizer the chatbot's synthesis stage uses (see
    synthesis/claude_synthesizer.py / workflow/graph.py's NODE_COMPLETE), same pattern as
    dashboard/gitlab.py's _get_claude_synthesizer()."""
    if "aks_claude_synthesizer" not in st.session_state:
        st.session_state["aks_claude_synthesizer"] = ClaudeSynthesizer()
    return st.session_state["aks_claude_synthesizer"]


def render_investigation_tab(resource_service: Any, cluster_id: str) -> None:
    """AI-assisted, evidence-grounded AKS investigation - a dedicated button, matching GitLab
    Workspace's Investigation tab, instead of the AKS chatbot's keyword-routed-only entry point.

    Reuses the exact same pipeline the chatbot uses for an AKS question, unmodified:
    agents.aks_agent.AKSAgent (cluster/namespaces/deployments/pods/services evidence, now
    timeout-bounded - see agents/aks_agent.py) -> agents.crew.InvestigationCrew's
    aks_investigation domain agent -> synthesis.ClaudeSynthesizer. No new evidence collection or
    reasoning is added here; Claude only ever sees facts already gathered by AKSAgent.
    """
    st.markdown("##### 🌐 Whole-Cluster Investigation")
    st.caption(
        "Runs the same cluster-wide evidence gathering and AI analysis the chatbot uses for AKS "
        "questions - cluster status, namespaces, deployments, pods, and services - analyzed by "
        "the AKS investigation agent and correlated by Claude Sonnet. Evidence-grounded only; "
        "nothing is invented."
    )

    question = f"Investigate the health of AKS cluster {cluster_id}."
    state_key = f"aks_investigation_{cluster_id}"
    if st.button("Run AKS Investigation", key=f"aks_investigate_btn_{cluster_id}"):
        with st.spinner("Gathering cluster evidence and running Claude synthesis..."):
            agent = _get_aks_investigation_agent()
            evidence = {"aks": agent.run(cluster_id)}

            crew = _get_aks_investigation_crew()
            domain_reports = crew.investigate(cluster_id, evidence, question=question)
            domain_reports_dicts = {domain: report.model_dump() for domain, report in domain_reports.items()}

            synthesizer = _get_aks_claude_synthesizer()
            outcome = synthesizer.synthesize(
                domain_reports=domain_reports_dicts,
                evidence=evidence,
                query=question,
                resource_id=cluster_id,
            )
            st.session_state[state_key] = {
                "evidence": evidence,
                "outcome": outcome,
                "error": synthesizer.last_error,
            }

    result = st.session_state.get(state_key)
    if not result:
        st.info("Click **Run AKS Investigation** to analyze this cluster's current health.")
        return

    aks_evidence = (result["evidence"] or {}).get("aks") or {}
    if aks_evidence.get("unreachable"):
        st.warning(
            "This cluster's Kubernetes API could not be reached "
            f"({aks_evidence.get('reason') or 'unknown reason'}). AKS data unavailable - "
            "findings below are limited to ARM-level cluster metadata."
        )

    if result.get("error"):
        st.error(f"Claude synthesis failed: {result['error']}")

    st.markdown("---")
    st.markdown(result["outcome"]["markdown"])


_SEVERITY_BADGE = {"critical": "🔴", "warning": "🟡", "info": "🔵"}


def render_namespace_investigation(resource_service: Any, cluster_id: str, namespace: Optional[str]) -> None:
    """Focused, one-click investigation of a single namespace - the per-namespace counterpart
    to render_investigation_tab's cluster-wide button, sharing the same evidence -> CrewAI
    domain agent -> Claude synthesis pipeline.

    Unlike the cluster-wide investigation, this also runs deterministic rule-based issue
    detection (utils.aks_diagnostics.detect_namespace_issues) over the collected evidence and
    shows those findings immediately - each with the exact raw evidence it came from - instead
    of waiting on Claude for the obvious cases. Claude is only called when at least one issue was
    actually found, to add a plain-language root cause and a specific fix on top of those same
    facts; logs for the unhealthiest pods are pulled automatically as part of the same click, so
    no separate trip to the Pods/Pod Logs views is needed to see the evidence.
    """
    st.markdown("#### 📦 Namespace Investigation")

    if not namespace:
        st.info(
            "Select a specific namespace above (not \"All namespaces\") to run a focused "
            "investigation that automatically collects its deployments, ReplicaSets, pods, "
            "services, events, and logs from any unhealthy pod."
        )
        return

    st.caption(
        f"One click collects everything in **{namespace}** - deployments, ReplicaSets, pods, "
        "services, events, and logs from any unhealthy pod - then explains any problem found in "
        "plain language, with the exact evidence and a suggested fix. Evidence-grounded only; "
        "nothing is invented. **Always fetched live** - this ignores any cached data from the "
        "views above, so every click reflects the cluster's current state, not an earlier snapshot."
    )

    state_key = f"aks_ns_investigation_{cluster_id}_{namespace}"
    if st.button(f'🔍 Investigate namespace "{namespace}"', key=f"aks_ns_investigate_btn_{cluster_id}_{namespace}"):
        with st.spinner(f'Collecting live pods, replicas, services, events, and logs for "{namespace}"...'):
            namespace_evidence = resource_service.investigate_namespace(cluster_id, namespace)

            outcome = None
            error = None
            if namespace_evidence["issues"]:
                # Only pays for a Claude call when there's something worth explaining - a clean
                # namespace doesn't need an AI narrative layered on top of "0 issues found".
                question = (
                    f"Investigate namespace '{namespace}' in AKS cluster {cluster_id} for errors or "
                    "issues, using only the collected pods/deployments/replicasets/services/events/"
                    "logs evidence, and recommend a specific fix for each issue found."
                )
                crew = _get_aks_investigation_crew()
                domain_reports = crew.investigate(cluster_id, {"aks": namespace_evidence}, question=question)
                domain_reports_dicts = {domain: report.model_dump() for domain, report in domain_reports.items()}

                synthesizer = _get_aks_claude_synthesizer()
                outcome = synthesizer.synthesize(
                    domain_reports=domain_reports_dicts,
                    evidence={"aks": namespace_evidence},
                    query=question,
                    resource_id=f"{cluster_id}/namespaces/{namespace}",
                )
                error = synthesizer.last_error

            st.session_state[state_key] = {
                "evidence": namespace_evidence,
                "outcome": outcome,
                "error": error,
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    result = st.session_state.get(state_key)
    if not result:
        return

    st.caption(f"🕒 Live snapshot fetched at {result['fetched_at']}. Click the button again for the latest state.")

    evidence = result["evidence"]
    issues = evidence.get("issues") or []
    fetch_errors = evidence.get("fetch_errors") or {}

    if fetch_errors:
        details = "; ".join(f"{kind} ({reason})" for kind, reason in fetch_errors.items())
        st.warning(f"Some data couldn't be collected, so findings below may be incomplete: {details}")

    pods = evidence.get("pods") or []
    deployments = evidence.get("deployments") or []
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Pods Healthy", f"{len([p for p in pods if p.get('is_healthy')])}/{len(pods)}")
    with col2:
        st.metric("Deployments Healthy", f"{len([d for d in deployments if d.get('is_healthy')])}/{len(deployments)}")
    with col3:
        st.metric("Issues Found", len(issues))

    if not issues:
        st.success(f'✅ No problems detected in "{namespace}" from the collected evidence.')
        return

    st.markdown("##### What's wrong")
    for issue in issues:
        badge = _SEVERITY_BADGE.get(issue.get("severity"), "⚪")
        with st.expander(f"{badge} {issue['title']}"):
            st.markdown(issue["detail"])
            st.caption("Evidence")
            st.json(issue["evidence"], expanded=False)

    pod_diagnostics = evidence.get("pod_diagnostics") or []
    if pod_diagnostics:
        st.markdown("##### Logs collected automatically from unhealthy pods")
        for diag in pod_diagnostics:
            pod = diag["pod"]
            with st.expander(f"📜 {pod.get('name')} logs"):
                logs = diag.get("logs") or {}
                if logs.get("error"):
                    st.error(logs["error"])
                elif logs.get("logs"):
                    st.code(logs["logs"], language="text")
                else:
                    st.info("No logs available.")

                pod_events = diag.get("events") or []
                if pod_events:
                    st.caption("Pod events")
                    st.dataframe(
                        [
                            {"Last Seen": e.get("last_seen"), "Type": e.get("type"),
                             "Reason": e.get("reason"), "Message": e.get("message")}
                            for e in pod_events
                        ],
                        use_container_width=True, hide_index=True,
                    )

    if result.get("error"):
        st.error(f"Claude synthesis failed: {result['error']}")

    if result.get("outcome"):
        st.markdown("##### Root cause & suggested fix")
        st.markdown(result["outcome"]["markdown"])
