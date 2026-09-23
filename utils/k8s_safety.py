"""Safety net for Kubernetes-API-dependent AKS calls (namespaces/nodes/pods/etc.).

Cluster discovery and ARM metadata (providers/azure/aks.py::get_clusters/get_cluster) go
through Azure Resource Manager and always succeed independently of whether the cluster's own
API server is reachable. Everything else (namespaces, nodes, pods, services, ingress, events,
logs) opens a socket directly to the cluster's Kubernetes API server, which fails outright for
a private cluster (privatelink.<region>.azmk8s.io) when this machine isn't on the cluster's
VNet/VPN/Bastion/ExpressRoute. Those failures (DNS, connect timeout, TLS, auth) aren't
`ApiException` and would otherwise propagate to Streamlit as a raw traceback.

This module bounds every such call with a hard timeout and translates whatever it raises into
one `AKSUnreachableError` with a short, UI-friendly reason - callers (dashboard/pages/
aks_workspace.py) decide what message to show; nothing here touches providers/ or changes what
data is fetched.
"""
import re
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Callable, Optional

try:
    from kubernetes.client.rest import ApiException
except ImportError:  # pragma: no cover - kubernetes SDK is always installed alongside providers
    ApiException = None

try:
    from urllib3.exceptions import HTTPError as Urllib3HTTPError
except ImportError:  # pragma: no cover
    Urllib3HTTPError = None

_CALL_TIMEOUT_SECONDS = 12

# Bounds a call whose underlying provider may fall back from a direct Kubernetes API connection
# to AKS Run Command (see providers/azure/aks.py / aks_run_command.py) - large enough to cover
# both the direct-connection probe (bounded by _CALL_TIMEOUT_SECONDS above) and a full Run
# Command round trip (AKS scheduling/tearing down its own "aks-command" pod - bounded by
# aks_run_command.RUN_COMMAND_TIMEOUT_SECONDS=90), with headroom. Deliberately not the same
# constant as _CALL_TIMEOUT_SECONDS: blindly applying the 12s direct-connection budget here would
# kill a valid, still-running Run Command invocation.
AKS_RUN_COMMAND_AWARE_TIMEOUT_SECONDS = 120

# 8, not 4: providers/azure/aks.py's direct-vs-Run-Command dispatch calls call_with_timeout for
# its own direct-connection probe from *inside* a call already running on this same pool (the
# outer call_with_timeout in services/resource_service.py/agents/aks_agent.py) - one outer task
# briefly occupies a second worker while it waits on that inner probe. That nesting only happens
# on a cluster not yet known to be private/unreachable (see AzureAKS._use_run_command), and only
# once per cluster, but sizing this pool for at least 2 workers per concurrent AKS call avoids
# needlessly serializing unrelated sessions/pages behind it.
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="aks-k8s-call")


class AKSUnreachableError(Exception):
    """The cluster's Kubernetes API server could not be reached in time."""

    def __init__(self, reason: str, detail: str = ""):
        # "private_cluster" | "dns" | "timeout" | "auth" | "network" | "unknown" |
        # "run_command_forbidden" | "run_command_failed" | "blocked_command"
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


class ReadOnlyCommandViolation(Exception):
    """A command was rejected before reaching AKS Run Command because it isn't a recognized
    read-only kubectl invocation."""


_ALLOWED_KUBECTL_VERBS = {
    "get", "describe", "logs", "top", "version", "cluster-info", "explain",
    "api-resources", "api-versions",
}
_FORBIDDEN_KUBECTL_VERBS = {
    "apply", "delete", "create", "patch", "edit", "scale", "rollout", "exec", "cp",
    "port-forward", "proxy", "drain", "cordon", "uncordon", "taint", "label", "annotate",
    "replace", "attach", "debug", "auth", "run", "set", "expose", "autoscale", "certificate",
    "plugin", "config", "wait", "diff",
}
# Blocks shell chaining/redirection/substitution so a command can never smuggle a second,
# unvalidated command past the verb check above (e.g. "kubectl get pods; kubectl delete ...").
_SHELL_METACHARACTERS = re.compile(r"[;&|`$(){}<>\n\\]")


def assert_read_only_kubectl(command: str) -> None:
    """Only a fixed allow-list of read-only kubectl subcommands may ever reach AKS Run Command -
    kubectl get/describe/logs/top/version/cluster-info/explain/api-resources/api-versions.
    Raises ReadOnlyCommandViolation for anything else, including every mutating verb (apply,
    delete, create, patch, edit, scale, rollout, exec, ...), helm, or any other command, and for
    shell metacharacters that could chain in an unvalidated second command. This is the only
    gate a command string passes through before being sent to Azure; nothing upstream may accept
    a free-form/user-supplied command and hand it to AKSRunCommandClient.run() directly."""
    command = (command or "").strip()
    if not command.startswith("kubectl "):
        raise ReadOnlyCommandViolation(f"Only kubectl commands are allowed via AKS Run Command, got: {command!r}")
    if _SHELL_METACHARACTERS.search(command):
        raise ReadOnlyCommandViolation(f"Command contains disallowed shell metacharacters: {command!r}")

    tokens = command.split()
    verb = tokens[1].lower() if len(tokens) > 1 else ""
    if verb in _FORBIDDEN_KUBECTL_VERBS:
        raise ReadOnlyCommandViolation(f"kubectl verb '{verb}' is not read-only and is blocked: {command!r}")
    if verb not in _ALLOWED_KUBECTL_VERBS:
        raise ReadOnlyCommandViolation(f"kubectl verb '{verb}' is not on the read-only allow-list: {command!r}")


def is_private_cluster(cluster: Any) -> bool:
    """Best-effort, network-free detection of a private AKS API server from data ARM already
    returned (no extra call).

    Prefers the authoritative `enable_private_cluster` field
    (`apiServerAccessProfile.enablePrivateCluster`, added to the cluster dict in
    providers/azure/aks.py's `_to_cluster_dict`) over sniffing the FQDN string: a private
    cluster with `enablePrivateClusterPublicFQDN` set (common - it's AKS's own recommended
    default) still gets a public-looking `fqdn` like any public cluster, while the actual
    private endpoint only shows up in a separate `private_fqdn` field. Falls back to the old
    FQDN-only heuristic for any cluster dict that doesn't carry the newer field.
    """
    cluster = cluster or {}
    enable_private_cluster = cluster.get("enable_private_cluster")
    if enable_private_cluster is not None:
        return bool(enable_private_cluster)
    fqdn = cluster.get("fqdn") or ""
    private_fqdn = cluster.get("private_fqdn") or ""
    return "privatelink" in fqdn.lower() or bool(private_fqdn)


def _classify(exc: Exception) -> str:
    if isinstance(exc, FutureTimeoutError):
        return "timeout"
    if isinstance(exc, socket.gaierror):
        return "dns"
    if ApiException is not None and isinstance(exc, ApiException):
        return "auth" if exc.status in (401, 403) else "unknown"
    if Urllib3HTTPError is not None and isinstance(exc, Urllib3HTTPError):
        # Covers MaxRetryError/NewConnectionError, which is how a DNS failure or a
        # firewalled/black-holed private endpoint actually surfaces from urllib3.
        message = str(exc).lower()
        if "nodename nor servname" in message or "name or service not known" in message or "getaddrinfo failed" in message:
            return "dns"
        return "network"
    if isinstance(exc, OSError):
        return "network"
    return "unknown"


def call_with_timeout(fn: Callable, *args: Any, timeout: Optional[float] = None, **kwargs: Any) -> Any:
    """Run a Kubernetes-API-dependent call on a worker thread with a hard timeout.

    Raises AKSUnreachableError (never the original exception) on any failure - the underlying
    blocking call may keep running in the background thread until it finishes on its own, but
    the caller is unblocked at `timeout` (default `_CALL_TIMEOUT_SECONDS`, 12s) either way.

    Pass `timeout=AKS_RUN_COMMAND_AWARE_TIMEOUT_SECONDS` for a call whose provider may fall back
    to AKS Run Command - the default 12s budget is sized for a direct Kubernetes API connection
    only and would otherwise cut off a valid, still-running Run Command invocation.
    """
    future = _executor.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout if timeout is not None else _CALL_TIMEOUT_SECONDS)
    except AKSUnreachableError:
        # Already raised (and classified) by the wrapped call itself - e.g. providers/azure/
        # aks.py's own direct-vs-Run-Command dispatch raises this directly with a specific
        # reason ("run_command_forbidden", etc.) once it has exhausted both paths. Re-classifying
        # it here through _classify() would only ever downgrade that reason to "unknown".
        raise
    except Exception as exc:
        raise AKSUnreachableError(_classify(exc), str(exc)) from exc
