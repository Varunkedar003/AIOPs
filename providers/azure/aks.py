"""Azure Kubernetes Service (AKS) discovery and live cluster data.

Cluster discovery and metadata come from Azure Resource Manager
(`ContainerServiceClient`). Namespace/node/workload/event/log data comes from the
Kubernetes API itself, reached with a short-lived kubeconfig obtained via
`list_cluster_user_credentials` - the *user* (non-admin) credential, since every
method in this module only ever reads cluster state. Nothing here creates,
updates, deletes, execs into, or port-forwards to anything in the cluster.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

import yaml
from azure.mgmt.containerservice import ContainerServiceClient
from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException
from kubernetes.config.kube_config import new_client_from_config_dict

from .auth import AzureAuth
from utils.timing import log_timing

logger = logging.getLogger(__name__)

_CLUSTER_TYPE = "microsoft.containerservice/managedclusters"


def _parse_cluster_resource_id(resource_id: str) -> Optional[Tuple[str, str]]:
    """Extract (resource_group, cluster_name) from an AKS ARM resource ID, else None."""
    if not resource_id:
        return None
    parts = resource_id.strip("/").split("/")
    lowered = [p.lower() for p in parts]
    try:
        rg_index = lowered.index("resourcegroups")
        type_index = lowered.index("managedclusters")
        return parts[rg_index + 1], parts[type_index + 1]
    except (ValueError, IndexError):
        return None


def _to_container_summary(container: Any) -> Dict[str, Any]:
    """Image + resource requests/limits for one container spec - documentation-facing, no secrets."""
    resources = getattr(container, "resources", None)
    requests = (getattr(resources, "requests", None) or {}) if resources else {}
    limits = (getattr(resources, "limits", None) or {}) if resources else {}
    return {
        "name": container.name,
        "image": container.image,
        "requests": dict(requests) if requests else {},
        "limits": dict(limits) if limits else {},
    }


class AzureAKS:
    """Discovers AKS clusters and fetches live cluster/Kubernetes data. All operations are read-only."""

    def __init__(self, azure_auth: Optional[AzureAuth] = None):
        self.azure_auth = azure_auth or AzureAuth()
        self._mgmt_client: Optional[ContainerServiceClient] = None
        self._k8s_clients: Dict[str, k8s_client.ApiClient] = {}
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Cluster discovery (Azure Resource Manager)
    # ------------------------------------------------------------------

    def _get_mgmt_client(self) -> ContainerServiceClient:
        if self._mgmt_client is None:
            credential = self.azure_auth.get_credential()
            self._mgmt_client = ContainerServiceClient(credential, self.azure_auth.subscription_id)
        return self._mgmt_client

    @staticmethod
    def _to_cluster_dict(cluster: Any, resource_group: str) -> Dict[str, Any]:
        agent_pools = cluster.agent_pool_profiles or []
        node_pools = [
            {"name": pool.name, "count": pool.count or 0, "vm_size": pool.vm_size}
            for pool in agent_pools
        ]
        provisioning_state = cluster.provisioning_state or "Unknown"
        return {
            "id": cluster.id,
            "resource_id": cluster.id,
            "name": cluster.name,
            "type": _CLUSTER_TYPE,
            "resource_type": _CLUSTER_TYPE,
            "resource_group": resource_group,
            "location": cluster.location,
            "region": cluster.location,
            "kubernetes_version": cluster.kubernetes_version,
            "provisioning_state": provisioning_state,
            "state": provisioning_state,
            "health_status": "Healthy" if provisioning_state == "Succeeded" else "Unknown",
            "node_count": sum(pool.count or 0 for pool in agent_pools),
            "node_pools": node_pools,
            "fqdn": getattr(cluster, "fqdn", None),
            "node_resource_group": getattr(cluster, "node_resource_group", None),
        }

    def get_clusters(self) -> List[Dict[str, Any]]:
        """Discover all AKS clusters in the configured subscription."""
        try:
            with log_timing(logger, "AzureAKS.get_clusters"):
                clusters = list(self._get_mgmt_client().managed_clusters.list())
            self.last_error = None
        except Exception as exc:
            logger.error("AKS cluster discovery failed: %s", exc)
            self.last_error = str(exc)
            return []

        results = []
        for cluster in clusters:
            parsed = _parse_cluster_resource_id(cluster.id)
            resource_group = parsed[0] if parsed else ""
            results.append(self._to_cluster_dict(cluster, resource_group))
        return results

    def get_cluster(self, cluster_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single cluster by ARM resource ID, or by short name (falls back to a full scan)."""
        if not cluster_id:
            return None

        parsed = _parse_cluster_resource_id(cluster_id)
        if parsed:
            resource_group, name = parsed
            try:
                with log_timing(logger, "AzureAKS.get_cluster"):
                    cluster = self._get_mgmt_client().managed_clusters.get(resource_group, name)
                self.last_error = None
                return self._to_cluster_dict(cluster, resource_group)
            except Exception as exc:
                logger.error("AKS cluster lookup failed for %s: %s", cluster_id, exc)
                self.last_error = str(exc)
                return None

        return next((c for c in self.get_clusters() if c.get("name") == cluster_id), None)

    # ------------------------------------------------------------------
    # Kubernetes API access
    # ------------------------------------------------------------------

    def _get_k8s_client(self, cluster_id: str) -> Optional[k8s_client.ApiClient]:
        """Build (and cache) a Kubernetes ApiClient for a cluster from its user kubeconfig."""
        if cluster_id in self._k8s_clients:
            return self._k8s_clients[cluster_id]

        parsed = _parse_cluster_resource_id(cluster_id)
        if not parsed:
            cluster = self.get_cluster(cluster_id)
            parsed = _parse_cluster_resource_id(cluster["id"]) if cluster else None
        if not parsed:
            self.last_error = f"Unknown AKS cluster: {cluster_id}"
            return None
        resource_group, name = parsed

        try:
            with log_timing(logger, "AzureAKS._get_k8s_client[kubeconfig]"):
                credentials = self._get_mgmt_client().managed_clusters.list_cluster_user_credentials(resource_group, name)
            kubeconfigs = credentials.kubeconfigs or []
            if not kubeconfigs:
                raise ValueError("No kubeconfig returned for cluster")
            kubeconfig_dict = yaml.safe_load(kubeconfigs[0].value)
            api_client = new_client_from_config_dict(kubeconfig_dict, persist_config=False)
            self._k8s_clients[cluster_id] = api_client
            self.last_error = None
            return api_client
        except Exception as exc:
            logger.error("Fetching Kubernetes credentials failed for %s: %s", cluster_id, exc)
            self.last_error = str(exc)
            return None

    # ------------------------------------------------------------------
    # Cluster inventory (Task 10)
    # ------------------------------------------------------------------

    def get_namespaces(self, cluster_id: str) -> List[Dict[str, Any]]:
        """List all namespaces in a cluster."""
        api_client = self._get_k8s_client(cluster_id)
        if not api_client:
            return []
        try:
            namespaces = k8s_client.CoreV1Api(api_client).list_namespace().items
            self.last_error = None
        except ApiException as exc:
            logger.error("Listing namespaces failed for %s: %s", cluster_id, exc)
            self.last_error = str(exc)
            return []

        return [
            {
                "name": ns.metadata.name,
                "status": ns.status.phase if ns.status else "Unknown",
                "created_at": _isoformat(ns.metadata.creation_timestamp),
                "labels": ns.metadata.labels or {},
            }
            for ns in namespaces
        ]

    def get_nodes(self, cluster_id: str) -> List[Dict[str, Any]]:
        """List all nodes in a cluster, with readiness/health status."""
        api_client = self._get_k8s_client(cluster_id)
        if not api_client:
            return []
        try:
            nodes = k8s_client.CoreV1Api(api_client).list_node().items
            self.last_error = None
        except ApiException as exc:
            logger.error("Listing nodes failed for %s: %s", cluster_id, exc)
            self.last_error = str(exc)
            return []

        return [self._to_node_dict(node) for node in nodes]

    @staticmethod
    def _to_node_dict(node: Any) -> Dict[str, Any]:
        conditions = node.status.conditions or [] if node.status else []
        ready_condition = next((c for c in conditions if c.type == "Ready"), None)
        is_ready = bool(ready_condition and ready_condition.status == "True")
        node_info = node.status.node_info if node.status else None
        capacity = node.status.capacity or {} if node.status else {}

        return {
            "name": node.metadata.name,
            "status": "Ready" if is_ready else "NotReady",
            "is_healthy": is_ready,
            "kubelet_version": node_info.kubelet_version if node_info else "Unknown",
            "os_image": node_info.os_image if node_info else "Unknown",
            "capacity_cpu": capacity.get("cpu"),
            "capacity_memory": capacity.get("memory"),
            "unschedulable": bool(node.spec.unschedulable) if node.spec else False,
            "created_at": _isoformat(node.metadata.creation_timestamp),
        }

    def get_deployments(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """List deployments in a cluster (all namespaces, or a single namespace), with rollout status."""
        api_client = self._get_k8s_client(cluster_id)
        if not api_client:
            return []
        try:
            apps = k8s_client.AppsV1Api(api_client)
            deployments = (
                apps.list_namespaced_deployment(namespace).items
                if namespace
                else apps.list_deployment_for_all_namespaces().items
            )
            self.last_error = None
        except ApiException as exc:
            logger.error("Listing deployments failed for %s: %s", cluster_id, exc)
            self.last_error = str(exc)
            return []

        return [self._to_deployment_dict(deployment) for deployment in deployments]

    @staticmethod
    def _to_deployment_dict(deployment: Any) -> Dict[str, Any]:
        status = deployment.status
        desired = deployment.spec.replicas or 0
        available = (status.available_replicas or 0) if status else 0
        ready = (status.ready_replicas or 0) if status else 0
        is_healthy = desired > 0 and available >= desired
        containers = deployment.spec.template.spec.containers or []

        return {
            "name": deployment.metadata.name,
            "namespace": deployment.metadata.namespace,
            "replicas": desired,
            "available_replicas": available,
            "ready_replicas": ready,
            "updated_replicas": (status.updated_replicas or 0) if status else 0,
            "image": containers[0].image if containers else "Unknown",
            "status": "Healthy" if is_healthy else "Degraded",
            "is_healthy": is_healthy,
            "created_at": _isoformat(deployment.metadata.creation_timestamp),
            "containers": [_to_container_summary(c) for c in containers],
        }

    def get_replicasets(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """List ReplicaSets in a cluster (all namespaces, or a single namespace)."""
        api_client = self._get_k8s_client(cluster_id)
        if not api_client:
            return []
        try:
            apps = k8s_client.AppsV1Api(api_client)
            replicasets = (
                apps.list_namespaced_replica_set(namespace).items
                if namespace
                else apps.list_replica_set_for_all_namespaces().items
            )
            self.last_error = None
        except ApiException as exc:
            logger.error("Listing replicasets failed for %s: %s", cluster_id, exc)
            self.last_error = str(exc)
            return []

        results = []
        for rs in replicasets:
            desired = rs.spec.replicas or 0
            available = (rs.status.available_replicas or 0) if rs.status else 0
            owner_refs = rs.metadata.owner_references or []
            results.append({
                "name": rs.metadata.name,
                "namespace": rs.metadata.namespace,
                "replicas": desired,
                "available_replicas": available,
                "is_healthy": desired == 0 or available >= desired,
                "owner": owner_refs[0].name if owner_refs else None,
                "created_at": _isoformat(rs.metadata.creation_timestamp),
            })
        return results

    def get_pods(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """List pods in a cluster (all namespaces, or a single namespace), with health/restart status."""
        api_client = self._get_k8s_client(cluster_id)
        if not api_client:
            return []
        try:
            core = k8s_client.CoreV1Api(api_client)
            pods = (
                core.list_namespaced_pod(namespace).items
                if namespace
                else core.list_pod_for_all_namespaces().items
            )
            self.last_error = None
        except ApiException as exc:
            logger.error("Listing pods failed for %s: %s", cluster_id, exc)
            self.last_error = str(exc)
            return []

        return [self._to_pod_dict(pod) for pod in pods]

    @staticmethod
    def _to_pod_dict(pod: Any) -> Dict[str, Any]:
        container_statuses = pod.status.container_statuses or [] if pod.status else []
        restart_count = sum(cs.restart_count or 0 for cs in container_statuses)
        ready_count = sum(1 for cs in container_statuses if cs.ready)
        phase = pod.status.phase if pod.status else "Unknown"
        owner_refs = pod.metadata.owner_references or []
        is_healthy = phase == "Running" and ready_count == len(container_statuses) and restart_count == 0

        return {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "phase": phase,
            "status": phase,
            "node": pod.spec.node_name if pod.spec else None,
            "restart_count": restart_count,
            "ready": f"{ready_count}/{len(container_statuses)}",
            "is_healthy": is_healthy,
            "pod_ip": pod.status.pod_ip if pod.status else None,
            "owner": owner_refs[0].name if owner_refs else None,
            "containers": [c.name for c in (pod.spec.containers or [])] if pod.spec else [],
            "created_at": _isoformat(pod.metadata.creation_timestamp),
        }

    def get_services(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """List services in a cluster (all namespaces, or a single namespace)."""
        api_client = self._get_k8s_client(cluster_id)
        if not api_client:
            return []
        try:
            core = k8s_client.CoreV1Api(api_client)
            services = (
                core.list_namespaced_service(namespace).items
                if namespace
                else core.list_service_for_all_namespaces().items
            )
            self.last_error = None
        except ApiException as exc:
            logger.error("Listing services failed for %s: %s", cluster_id, exc)
            self.last_error = str(exc)
            return []

        results = []
        for svc in services:
            ingress_points = ((svc.status.load_balancer.ingress or []) if svc.status and svc.status.load_balancer else [])
            external_ips = [point.ip or point.hostname for point in ingress_points if (point.ip or point.hostname)]
            results.append({
                "name": svc.metadata.name,
                "namespace": svc.metadata.namespace,
                "type": svc.spec.type,
                "cluster_ip": svc.spec.cluster_ip,
                "external_ip": ",".join(external_ips) if external_ips else None,
                "ports": [f"{p.port}:{p.target_port}/{p.protocol}" for p in (svc.spec.ports or [])],
            })
        return results

    def get_ingresses(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """List ingress resources in a cluster (all namespaces, or a single namespace)."""
        api_client = self._get_k8s_client(cluster_id)
        if not api_client:
            return []
        try:
            networking = k8s_client.NetworkingV1Api(api_client)
            ingresses = (
                networking.list_namespaced_ingress(namespace).items
                if namespace
                else networking.list_ingress_for_all_namespaces().items
            )
            self.last_error = None
        except ApiException as exc:
            logger.error("Listing ingresses failed for %s: %s", cluster_id, exc)
            self.last_error = str(exc)
            return []

        results = []
        for ing in ingresses:
            lb_ingress = (ing.status.load_balancer.ingress or []) if ing.status and ing.status.load_balancer else []
            addresses = [point.ip or point.hostname for point in lb_ingress if (point.ip or point.hostname)]
            results.append({
                "name": ing.metadata.name,
                "namespace": ing.metadata.namespace,
                "class_name": ing.spec.ingress_class_name if ing.spec else None,
                "hosts": [rule.host for rule in (ing.spec.rules or [])] if ing.spec else [],
                "address": ",".join(addresses) if addresses else None,
            })
        return results

    # ------------------------------------------------------------------
    # Configuration inventory (Task 24: documentation generator)
    # ------------------------------------------------------------------

    def get_configmaps(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """List ConfigMaps in a cluster (all namespaces, or a single namespace) - names and data
        keys only, never values (config values can still carry environment-specific detail)."""
        api_client = self._get_k8s_client(cluster_id)
        if not api_client:
            return []
        try:
            core = k8s_client.CoreV1Api(api_client)
            configmaps = (
                core.list_namespaced_config_map(namespace).items
                if namespace
                else core.list_config_map_for_all_namespaces().items
            )
            self.last_error = None
        except ApiException as exc:
            logger.error("Listing configmaps failed for %s: %s", cluster_id, exc)
            self.last_error = str(exc)
            return []

        return [
            {
                "name": cm.metadata.name,
                "namespace": cm.metadata.namespace,
                "keys": sorted((cm.data or {}).keys()),
                "created_at": _isoformat(cm.metadata.creation_timestamp),
            }
            for cm in configmaps
        ]

    def get_secrets(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """List Secrets in a cluster (all namespaces, or a single namespace) - names and type
        only. Never fetches or returns secret data/values or key names."""
        api_client = self._get_k8s_client(cluster_id)
        if not api_client:
            return []
        try:
            core = k8s_client.CoreV1Api(api_client)
            secrets = (
                core.list_namespaced_secret(namespace).items
                if namespace
                else core.list_secret_for_all_namespaces().items
            )
            self.last_error = None
        except ApiException as exc:
            logger.error("Listing secrets failed for %s: %s", cluster_id, exc)
            self.last_error = str(exc)
            return []

        return [
            {
                "name": secret.metadata.name,
                "namespace": secret.metadata.namespace,
                "type": secret.type,
                "created_at": _isoformat(secret.metadata.creation_timestamp),
            }
            for secret in secrets
        ]

    # ------------------------------------------------------------------
    # Cluster monitoring (Task 11): events and pod logs
    # ------------------------------------------------------------------

    def get_events(
        self, cluster_id: str, namespace: Optional[str] = None, field_selector: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List recent events in a cluster (all namespaces, or a single namespace), newest first."""
        api_client = self._get_k8s_client(cluster_id)
        if not api_client:
            return []
        try:
            core = k8s_client.CoreV1Api(api_client)
            kwargs = {"field_selector": field_selector} if field_selector else {}
            events = (
                core.list_namespaced_event(namespace, **kwargs).items
                if namespace
                else core.list_event_for_all_namespaces(**kwargs).items
            )
            self.last_error = None
        except ApiException as exc:
            logger.error("Listing events failed for %s: %s", cluster_id, exc)
            self.last_error = str(exc)
            return []

        results = [
            {
                "namespace": event.metadata.namespace,
                "type": event.type,
                "reason": event.reason,
                "message": event.message,
                "involved_object": f"{event.involved_object.kind}/{event.involved_object.name}" if event.involved_object else "Unknown",
                "count": event.count or 1,
                "last_seen": _isoformat(event.last_timestamp or event.event_time),
            }
            for event in events
        ]
        results.sort(key=lambda item: item["last_seen"] or "", reverse=True)
        return results

    def get_pod_events(self, cluster_id: str, namespace: str, pod_name: str) -> List[Dict[str, Any]]:
        """List events for a single pod."""
        return self.get_events(cluster_id, namespace=namespace, field_selector=f"involvedObject.name={pod_name}")

    def get_pod_logs(
        self,
        cluster_id: str,
        namespace: str,
        pod_name: str,
        container: Optional[str] = None,
        tail_lines: int = 200,
    ) -> Dict[str, Any]:
        """Fetch the latest log lines for a pod (read-only `kubectl logs` equivalent)."""
        api_client = self._get_k8s_client(cluster_id)
        if not api_client:
            return {"pod_name": pod_name, "namespace": namespace, "logs": "", "error": self.last_error or "Unable to connect to cluster"}

        try:
            core = k8s_client.CoreV1Api(api_client)
            logs = core.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
                timestamps=True,
            )
            self.last_error = None
            return {"pod_name": pod_name, "namespace": namespace, "logs": logs}
        except ApiException as exc:
            logger.error("Fetching pod logs failed for %s/%s: %s", namespace, pod_name, exc)
            self.last_error = str(exc)
            return {"pod_name": pod_name, "namespace": namespace, "logs": "", "error": str(exc)}


def _isoformat(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else value
