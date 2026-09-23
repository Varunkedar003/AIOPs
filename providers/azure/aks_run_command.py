"""AKS Run Command execution via the Azure Container Service management-plane SDK.

This is the direct Python SDK equivalent of `az aks command invoke --resource-group ... --name
... --command "..."`: both call the exact same ARM operation
(`Microsoft.ContainerService/managedClusters/runCommand/action`, wrapped by
`ContainerServiceClient.managed_clusters.begin_run_command`/`get_command_result`). AKS itself
provisions a short-lived "aks-command" pod inside the cluster to run the command and reports the
result back through ARM - nothing here creates, deletes, or otherwise manages that pod, and no
Azure resource/configuration is created or changed by calling this.

Used as a fallback data path (see providers/azure/aks.py) for clusters whose Kubernetes API
server can't be reached directly - typically a private cluster with no VNet/VPN/Bastion
connectivity from wherever this app runs (e.g. Streamlit Community Cloud). Every command passed
to `AKSRunCommandClient.run()` is validated read-only by
`utils.k8s_safety.assert_read_only_kubectl` first; this module never accepts a free-form or
user-supplied command string.
"""
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from azure.mgmt.containerservice import ContainerServiceClient
from azure.mgmt.containerservice.models import RunCommandRequest

from utils.k8s_safety import assert_read_only_kubectl
from utils.timing import log_timing

logger = logging.getLogger(__name__)

# Bounded wait for one Run Command invocation. AKS has to schedule an "aks-command" pod, pull
# its image if not already cached, run kubectl inside it, and report back - materially slower
# than a direct Kubernetes API call, so this is deliberately much larger than
# utils/k8s_safety.py's 12s direct-connection timeout rather than sharing it (see task
# requirement: don't blindly apply the 12s timeout to a valid Run Command operation).
RUN_COMMAND_TIMEOUT_SECONDS = 90

# AKS Run Command hard-caps output at 512 KiB (documented by Microsoft: "Output size limit:
# 512kB" - see https://learn.microsoft.com/azure/aks/access-private-cluster). Confirmed
# empirically against two real clusters: every truncated response observed was exactly this many
# bytes, cut off mid-string. Output at or past this size is never trustworthy and must never be
# parsed as JSON (see `_parse_list_or_raise`).
_OUTPUT_CAP_BYTES = 512 * 1024

# Every full-JSON `kubectl get` command strips managedFields (apply-history bookkeeping the
# dashboard/investigation code never reads) - a real kubectl flag, not a pipe/jq trick, so it
# stays within the read-only allow-list and costs nothing when it doesn't help.
_NO_MANAGED_FIELDS = "--show-managed-fields=false"

_K8S_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,253}$")


class AKSRunCommandError(Exception):
    """A Run Command invocation was rejected, failed, or timed out.

    `status_code` is populated from the underlying Azure error when available (e.g. 403 for an
    RBAC/authorization failure), so callers can distinguish "not authorized to use Run Command"
    from "Run Command ran but kubectl itself failed" without string-matching the message.
    `reason` carries a specific machine-readable cause (e.g. "scope_too_broad") for callers that
    need to react differently than the generic case - see AKSRunCommandTruncatedError.
    """

    def __init__(self, message: str, status_code: Optional[int] = None, reason: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


class AKSRunCommandTruncatedError(AKSRunCommandError):
    """Run Command output hit (or appears to have hit) the 512 KiB output cap, or failed to
    parse as valid JSON - in either case the data is never trusted or partially parsed.

    `reason` is one of:
      - "scope_too_broad": an all-namespaces fetch for this kind was too large; the caller
        should narrow to one namespace (which is expected to fit - see module docstring).
      - "namespace_too_large": even a single namespace's data for this kind is too large: this
        specific (kind, namespace) genuinely can't be retrieved via Run Command.
      - "nodes_unavailable": the cluster's node list is too large even in the reduced-field
        custom-columns form - effectively unreachable via Run Command.
    """


@dataclass
class AKSRunCommandStats:
    """Call-count/latency instrumentation for AKS Run Command usage - exposed so the AKS
    Workspace page can report how many Run Command calls a page load actually made and how long
    they took (task requirement: measure calls-per-load and latency, and avoid one call per
    resource/row)."""

    call_count: int = 0
    total_seconds: float = 0.0
    calls: List[Dict[str, Any]] = field(default_factory=list)


class AKSRunCommandClient:
    """Runs a single validated, read-only kubectl command against a cluster via AKS Run Command
    and returns its combined stdout/stderr output (`CommandResultProperties.logs`).

    Safe to call concurrently from multiple worker threads (see dashboard/aks.py's
    run_concurrent_fetches) - each `run()` call is otherwise independent (its own ARM Run
    Command invocation/polling), and `stats` updates are serialized by `_stats_lock` so
    concurrent calls can't lose a count/latency update to a `+=` race.
    """

    def __init__(self, mgmt_client_factory: Callable[[], ContainerServiceClient]):
        # Shares the exact same ContainerServiceClient (and therefore the same AzureAuth
        # credential/subscription) AzureAKS already uses for cluster discovery and kubeconfig
        # retrieval - no separate authentication path is introduced.
        self._mgmt_client_factory = mgmt_client_factory
        self.stats = AKSRunCommandStats()
        self._stats_lock = threading.Lock()

    def run(self, resource_group: str, cluster_name: str, command: str) -> str:
        """Run one command via AKS Run Command. Raises AKSRunCommandError if the command isn't
        read-only, the invocation fails/times out, or kubectl itself exits non-zero."""
        assert_read_only_kubectl(command)

        client = self._mgmt_client_factory()
        started = time.monotonic()
        exit_code: Optional[int] = None
        try:
            with log_timing(logger, f"AKSRunCommandClient.run[{cluster_name}] {command}"):
                poller = client.managed_clusters.begin_run_command(
                    resource_group, cluster_name, RunCommandRequest(command=command)
                )
                result = poller.result(timeout=RUN_COMMAND_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.error(
                "AKS Run Command failed for %s/%s (%r): %s", resource_group, cluster_name, command, exc
            )
            status_code = getattr(exc, "status_code", None)
            self._record(command, time.monotonic() - started, None)
            raise AKSRunCommandError(str(exc), status_code=status_code) from exc

        props = result.properties
        exit_code = getattr(props, "exit_code", None) if props is not None else None
        logs = (getattr(props, "logs", None) if props is not None else None) or ""
        self._record(command, time.monotonic() - started, exit_code)

        if exit_code not in (0, None):
            raise AKSRunCommandError(f"kubectl exited with code {exit_code}: {logs[:2000]}")
        return logs

    def _record(self, command: str, elapsed: float, exit_code: Optional[int]) -> None:
        # Concurrent Run Command calls (see dashboard/aks.py's run_concurrent_fetches) can reach
        # this at the same time - without the lock, two threads' `call_count += 1` can race and
        # lose an increment (classic non-atomic read-modify-write on a shared counter).
        with self._stats_lock:
            self.stats.call_count += 1
            self.stats.total_seconds += elapsed
            self.stats.calls.append({"command": command, "seconds": round(elapsed, 2), "exit_code": exit_code})


# ----------------------------------------------------------------------
# Per-kind, size-aware cluster-data fetch.
#
# Design note (why not one batched call): an earlier version of this module issued a single
# `kubectl get namespaces,nodes,deployments,...,-A -o json` call covering every resource kind at
# once. Against real clusters this reliably exceeded the 512 KiB Run Command output cap - and
# testing showed even combining just 2-3 of the smaller kinds (services+ingresses+events) is
# *not* safely portable across cluster sizes: it fit on a 21-namespace cluster but truncated on
# a 75-namespace one. Rather than guess at groupings that happen to fit today's data, every kind
# below is fetched with its own Run Command call, scoped to the caller's `namespace` (server-side
# `-n <namespace>`, not client-side filtering) when one is given. This costs a few more Run
# Command calls in the common case (each ~30s, dominated by AKS scheduling its own pod) but is
# the only strategy verified safe on both a small and a large real cluster.
# ----------------------------------------------------------------------

def _looks_truncated(output: str) -> bool:
    """True if `output` is at or past the 512 KiB Run Command output cap."""
    return len(output.encode("utf-8")) >= _OUTPUT_CAP_BYTES


def _parse_list_or_raise(output: str, reason: str) -> List[Dict[str, Any]]:
    """Parse a `kubectl get ... -o json` response into its `items` list - or raise
    AKSRunCommandTruncatedError without ever attempting to parse/salvage output that looks
    truncated or fails to parse. A truncated response is never treated as valid, even partially."""
    if _looks_truncated(output):
        raise AKSRunCommandTruncatedError(
            f"AKS Run Command output reached the {_OUTPUT_CAP_BYTES} byte limit and was truncated",
            reason=reason,
        )
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        # Any parse failure is treated exactly like an explicit truncation - never guess at
        # repairing broken JSON or extracting a partial result from it.
        raise AKSRunCommandTruncatedError(
            f"AKS Run Command returned unparseable output: {exc}", reason=reason,
        ) from exc
    if isinstance(parsed, dict) and parsed.get("kind") == "List":
        return parsed.get("items") or []
    if isinstance(parsed, dict):
        return [parsed]
    return []


def fetch_namespaces(
    client: AKSRunCommandClient, resource_group: str, cluster_name: str, namespace: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List all namespaces - always fetched fresh from the live cluster (never a hardcoded or
    assumed list), and used by callers to decide safe per-namespace scoping for larger kinds.
    `namespace` is accepted (and ignored) only so this has the same call signature as every
    other fetch_*() function here - namespaces are inherently cluster-scoped."""
    command = f"kubectl get namespaces -o json {_NO_MANAGED_FIELDS}"
    output = client.run(resource_group, cluster_name, command)
    items = _parse_list_or_raise(output, reason="namespaces_unavailable")
    return [_ns_from_json(item) for item in items]


_NODE_COLUMNS_COMMAND = (
    "kubectl get nodes -o custom-columns="
    "NAME:.metadata.name,CREATED:.metadata.creationTimestamp,UNSCHED:.spec.unschedulable,"
    "KUBELET:.status.nodeInfo.kubeletVersion,OSIMAGE:.status.nodeInfo.osImage,"
    "CPU:.status.capacity.cpu,MEM:.status.capacity.memory,"
    "CONDTYPES:.status.conditions[*].type,CONDSTATUSES:.status.conditions[*].status "
    "--no-headers"
)
_NODE_COLUMN_SPLIT_RE = re.compile(r"\s{2,}")


def _node_from_columns(line: str) -> Dict[str, Any]:
    """Parse one line of `_NODE_COLUMNS_COMMAND` output into the same dict shape `_node_from_json`
    produces. Columns are separated by kubectl's own multi-space padding; no column value ever
    contains internal whitespace (names/versions/timestamps/comma-joined lists), so splitting on
    runs of 2+ spaces is unambiguous."""
    parts = _NODE_COLUMN_SPLIT_RE.split(line.strip())
    parts += ["<none>"] * (9 - len(parts))
    name, created, unsched, kubelet, osimage, cpu, mem, cond_types, cond_statuses = parts[:9]

    types = cond_types.split(",") if cond_types != "<none>" else []
    statuses = cond_statuses.split(",") if cond_statuses != "<none>" else []
    ready_status = next((s for t, s in zip(types, statuses) if t == "Ready"), None)
    is_ready = ready_status == "True"

    return {
        "name": name,
        "status": "Ready" if is_ready else "NotReady",
        "is_healthy": is_ready,
        "kubelet_version": kubelet if kubelet != "<none>" else "Unknown",
        "os_image": osimage if osimage != "<none>" else "Unknown",
        "capacity_cpu": cpu if cpu != "<none>" else None,
        "capacity_memory": mem if mem != "<none>" else None,
        "unschedulable": unsched == "true",
        "created_at": created if created != "<none>" else None,
    }


def fetch_nodes(
    client: AKSRunCommandClient, resource_group: str, cluster_name: str, namespace: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List all nodes. `namespace` is accepted (and ignored) only for call-signature parity with
    the other fetch_*() functions - nodes are cluster-scoped, no namespace to narrow to, so a
    truncated full-JSON response falls back to a reduced-field `-o custom-columns` query - verified
    against a 28-node cluster where full JSON truncated at 512 KiB but the reduced form came in
    at ~14 KiB, still containing every field the dashboard/investigation code reads (readiness,
    kubelet version, OS image, capacity, schedulability, created time) - only large unused fields
    like the per-node cached-image list are dropped."""
    command = f"kubectl get nodes -o json {_NO_MANAGED_FIELDS}"
    output = client.run(resource_group, cluster_name, command)
    try:
        items = _parse_list_or_raise(output, reason="nodes_unavailable")
        return [_node_from_json(item) for item in items]
    except AKSRunCommandTruncatedError:
        logger.info("Node list too large for full JSON via Run Command on %s - retrying with reduced fields", cluster_name)

    output = client.run(resource_group, cluster_name, _NODE_COLUMNS_COMMAND)
    if _looks_truncated(output):
        raise AKSRunCommandTruncatedError(
            "Node list too large even in reduced custom-columns form", reason="nodes_unavailable",
        )
    lines = [line for line in output.splitlines() if line.strip()]
    return [_node_from_columns(line) for line in lines]


def _fetch_kind(
    client: AKSRunCommandClient,
    resource_group: str,
    cluster_name: str,
    kubectl_resource: str,
    parser,
    namespace: Optional[str],
) -> List[Dict[str, Any]]:
    """Fetch exactly one namespaced resource kind, scoped to `namespace` if given (a single,
    small, fast Run Command call) or every namespace otherwise. Raises
    AKSRunCommandTruncatedError(reason="namespace_too_large") if even the single given namespace
    is too large, or reason="scope_too_broad" if the all-namespaces fetch is too large - callers
    (providers/azure/aks.py) surface the latter as a prompt to pick a specific namespace rather
    than silently attempting one Run Command call per namespace (verified impractical: a real
    75-namespace cluster would mean 75+ ~30s calls for a single resource kind)."""
    scope = f"-n {namespace}" if namespace else "--all-namespaces"
    command = f"kubectl get {kubectl_resource} {scope} -o json {_NO_MANAGED_FIELDS}"
    output = client.run(resource_group, cluster_name, command)
    reason = "namespace_too_large" if namespace else "scope_too_broad"
    items = _parse_list_or_raise(output, reason=reason)
    return [parser(item) for item in items]


def fetch_deployments(client, resource_group, cluster_name, namespace=None):
    return _fetch_kind(client, resource_group, cluster_name, "deployments.apps", _deployment_from_json, namespace)


def fetch_replicasets(client, resource_group, cluster_name, namespace=None):
    return _fetch_kind(client, resource_group, cluster_name, "replicasets.apps", _replicaset_from_json, namespace)


def fetch_pods(client, resource_group, cluster_name, namespace=None):
    return _fetch_kind(client, resource_group, cluster_name, "pods", _pod_from_json, namespace)


def fetch_services(client, resource_group, cluster_name, namespace=None):
    return _fetch_kind(client, resource_group, cluster_name, "services", _service_from_json, namespace)


def fetch_ingresses(client, resource_group, cluster_name, namespace=None):
    return _fetch_kind(client, resource_group, cluster_name, "ingresses.networking.k8s.io", _ingress_from_json, namespace)


def fetch_events(client, resource_group, cluster_name, namespace=None):
    events = _fetch_kind(client, resource_group, cluster_name, "events", _event_from_json, namespace)
    events.sort(key=lambda e: e.get("last_seen") or "", reverse=True)
    return events


def fetch_pod_logs(
    client: AKSRunCommandClient,
    resource_group: str,
    cluster_name: str,
    *,
    namespace: str,
    pod_name: str,
    container: Optional[str] = None,
    tail_lines: int = 200,
) -> str:
    """Fetch the latest log lines for one pod via `kubectl logs` (only ever issued on an
    explicit user action, never as part of the bulk list fetch above)."""
    for value, label in ((namespace, "namespace"), (pod_name, "pod name")):
        if not value or not _K8S_NAME_RE.match(value):
            raise AKSRunCommandError(f"Invalid {label} for AKS Run Command: {value!r}")
    if container is not None and not _K8S_NAME_RE.match(container):
        raise AKSRunCommandError(f"Invalid container name for AKS Run Command: {container!r}")

    command = f"kubectl logs {pod_name} -n {namespace} --tail={int(tail_lines)} --timestamps=true"
    if container:
        command += f" -c {container}"
    return client.run(resource_group, cluster_name, command)


# ----------------------------------------------------------------------
# Raw Kubernetes API JSON (as printed by `kubectl ... -o json`) -> the same dict shapes the
# direct Kubernetes-client path in providers/azure/aks.py produces. Field names below are the
# actual Kubernetes API's camelCase JSON keys, not the Python client's snake_case attributes.
# ----------------------------------------------------------------------

def _ns_from_json(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item.get("metadata") or {}
    status = item.get("status") or {}
    return {
        "name": metadata.get("name"),
        "status": status.get("phase") or "Unknown",
        "created_at": metadata.get("creationTimestamp"),
        "labels": metadata.get("labels") or {},
    }


def _node_from_json(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    conditions = status.get("conditions") or []
    ready_condition = next((c for c in conditions if c.get("type") == "Ready"), None)
    is_ready = bool(ready_condition and ready_condition.get("status") == "True")
    node_info = status.get("nodeInfo") or {}
    capacity = status.get("capacity") or {}
    return {
        "name": metadata.get("name"),
        "status": "Ready" if is_ready else "NotReady",
        "is_healthy": is_ready,
        "kubelet_version": node_info.get("kubeletVersion") or "Unknown",
        "os_image": node_info.get("osImage") or "Unknown",
        "capacity_cpu": capacity.get("cpu"),
        "capacity_memory": capacity.get("memory"),
        "unschedulable": bool(spec.get("unschedulable") or False),
        "created_at": metadata.get("creationTimestamp"),
    }


def _container_summary_from_json(container: Dict[str, Any]) -> Dict[str, Any]:
    resources = container.get("resources") or {}
    return {
        "name": container.get("name"),
        "image": container.get("image"),
        "requests": dict(resources.get("requests") or {}),
        "limits": dict(resources.get("limits") or {}),
    }


def _deployment_from_json(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    desired = spec.get("replicas") or 0
    available = status.get("availableReplicas") or 0
    ready = status.get("readyReplicas") or 0
    is_healthy = desired > 0 and available >= desired
    containers = (((spec.get("template") or {}).get("spec") or {}).get("containers")) or []
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "replicas": desired,
        "available_replicas": available,
        "ready_replicas": ready,
        "updated_replicas": status.get("updatedReplicas") or 0,
        "image": containers[0].get("image") if containers else "Unknown",
        "status": "Healthy" if is_healthy else "Degraded",
        "is_healthy": is_healthy,
        "created_at": metadata.get("creationTimestamp"),
        "containers": [_container_summary_from_json(c) for c in containers],
    }


def _replicaset_from_json(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    desired = spec.get("replicas") or 0
    available = status.get("availableReplicas") or 0
    owner_refs = metadata.get("ownerReferences") or []
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "replicas": desired,
        "available_replicas": available,
        "is_healthy": desired == 0 or available >= desired,
        "owner": owner_refs[0].get("name") if owner_refs else None,
        "created_at": metadata.get("creationTimestamp"),
    }


def _pod_from_json(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    container_statuses = status.get("containerStatuses") or []
    restart_count = sum(cs.get("restartCount") or 0 for cs in container_statuses)
    ready_count = sum(1 for cs in container_statuses if cs.get("ready"))
    phase = status.get("phase") or "Unknown"
    owner_refs = metadata.get("ownerReferences") or []
    is_healthy = phase == "Running" and ready_count == len(container_statuses) and restart_count == 0
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "phase": phase,
        "status": phase,
        "node": spec.get("nodeName"),
        "restart_count": restart_count,
        "ready": f"{ready_count}/{len(container_statuses)}",
        "is_healthy": is_healthy,
        "pod_ip": status.get("podIP"),
        "owner": owner_refs[0].get("name") if owner_refs else None,
        "containers": [c.get("name") for c in (spec.get("containers") or [])],
        "created_at": metadata.get("creationTimestamp"),
    }


def _service_from_json(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    lb_ingress = ((status.get("loadBalancer") or {}).get("ingress")) or []
    external_ips = [p.get("ip") or p.get("hostname") for p in lb_ingress if (p.get("ip") or p.get("hostname"))]
    ports = spec.get("ports") or []
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "type": spec.get("type"),
        "cluster_ip": spec.get("clusterIP"),
        "external_ip": ",".join(external_ips) if external_ips else None,
        "ports": [f"{p.get('port')}:{p.get('targetPort')}/{p.get('protocol')}" for p in ports],
    }


def _ingress_from_json(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    lb_ingress = ((status.get("loadBalancer") or {}).get("ingress")) or []
    addresses = [p.get("ip") or p.get("hostname") for p in lb_ingress if (p.get("ip") or p.get("hostname"))]
    rules = spec.get("rules") or []
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "class_name": spec.get("ingressClassName"),
        "hosts": [r.get("host") for r in rules],
        "address": ",".join(addresses) if addresses else None,
    }


def _event_from_json(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = item.get("metadata") or {}
    involved = item.get("involvedObject") or {}
    return {
        "namespace": metadata.get("namespace"),
        "type": item.get("type"),
        "reason": item.get("reason"),
        "message": item.get("message"),
        "involved_object": f"{involved.get('kind')}/{involved.get('name')}" if involved else "Unknown",
        "count": item.get("count") or 1,
        "last_seen": item.get("lastTimestamp") or item.get("eventTime"),
    }
