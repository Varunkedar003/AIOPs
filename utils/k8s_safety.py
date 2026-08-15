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
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Callable

try:
    from kubernetes.client.rest import ApiException
except ImportError:  # pragma: no cover - kubernetes SDK is always installed alongside providers
    ApiException = None

try:
    from urllib3.exceptions import HTTPError as Urllib3HTTPError
except ImportError:  # pragma: no cover
    Urllib3HTTPError = None

_CALL_TIMEOUT_SECONDS = 12
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="aks-k8s-call")


class AKSUnreachableError(Exception):
    """The cluster's Kubernetes API server could not be reached in time."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason  # "private_cluster" | "dns" | "timeout" | "auth" | "network" | "unknown"
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


def is_private_cluster(cluster: Any) -> bool:
    """Best-effort, network-free detection of a private AKS API server from data ARM already
    returned (no extra call): private clusters get a `privatelink.<region>.azmk8s.io` FQDN."""
    fqdn = (cluster or {}).get("fqdn") or ""
    return "privatelink" in fqdn.lower()


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


def call_with_timeout(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """Run a Kubernetes-API-dependent call on a worker thread with a hard timeout.

    Raises AKSUnreachableError (never the original exception) on any failure - the underlying
    blocking call may keep running in the background thread until it finishes on its own, but
    the caller is unblocked at `_CALL_TIMEOUT_SECONDS` either way.
    """
    future = _executor.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=_CALL_TIMEOUT_SECONDS)
    except Exception as exc:
        raise AKSUnreachableError(_classify(exc), str(exc)) from exc
