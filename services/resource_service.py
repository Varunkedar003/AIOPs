import logging
from typing import Any, Dict, List, Optional
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from providers import MockAzureProvider, MockAKSProvider, MockGitLabProvider, MockObservabilityProvider, MockCostProvider
from providers.azure.relationships import AzureRelationshipDiscovery
from utils.resource_id import normalize_resource_id
from utils.k8s_safety import call_with_timeout
from utils.timing import log_timing

logger = logging.getLogger(__name__)

# ARM resource-type strings (as returned by Azure Resource Graph, case varies) for each sidebar/
# explorer category. MockAzureProvider's own get_app_services()/get_sql_databases()/etc. are
# unimplemented placeholders that always return [] - rather than touch the provider layer, every
# category below is derived here from the one real get_all_resources() call, matched
# case-insensitively (Resource Graph doesn't guarantee a casing for "type").
_CATEGORY_ARM_TYPES = {
    "app_services": "microsoft.web/sites",
    "sql_databases": "microsoft.sql/servers/databases",
    "redis_caches": "microsoft.cache/redis",
    "storage_accounts": "microsoft.storage/storageaccounts",
    "key_vaults": "microsoft.keyvault/vaults",
    "application_insights": "microsoft.insights/components",
    "log_analytics": "microsoft.operationalinsights/workspaces",
    "postgresql_servers": "microsoft.dbforpostgresql/flexibleservers",
    "virtual_machines": "microsoft.compute/virtualmachines",
    "vm_scale_sets": "microsoft.compute/virtualmachinescalesets",
    "managed_disks": "microsoft.compute/disks",
    "container_registries": "microsoft.containerregistry/registries",
    "container_apps": "microsoft.app/containerapps",
    "container_instances": "microsoft.containerinstance/containergroups",
    "cosmos_db_accounts": "microsoft.documentdb/databaseaccounts",
    "cognitive_services": "microsoft.cognitiveservices/accounts",
    "virtual_networks": "microsoft.network/virtualnetworks",
    "network_security_groups": "microsoft.network/networksecuritygroups",
    "load_balancers": "microsoft.network/loadbalancers",
    "application_gateways": "microsoft.network/applicationgateways",
    "public_ip_addresses": "microsoft.network/publicipaddresses",
    "app_service_plans": "microsoft.web/serverfarms",
}

# ARM types deliberately left out of "other_resources" below - these are child/system/plumbing
# objects Azure creates alongside a real resource (an alert rule, a VM extension, a TLS cert,
# a DNS zone's vnet link, ...), not something anyone browses, selects, or checks cost/health on
# in their own right. Everything else undiscovered by _CATEGORY_ARM_TYPES above still shows up,
# just grouped under "Other Resources" instead of being silently dropped.
_NOISE_ARM_TYPES = {
    "microsoft.alertsmanagement/smartdetectoralertrules",
    "microsoft.alertsmanagement/prometheusrulegroups",
    "microsoft.web/sites/slots",
    "microsoft.web/certificates",
    "microsoft.compute/virtualmachines/extensions",
    "microsoft.compute/sshpublickeys",
    "microsoft.network/privatednszones/virtualnetworklinks",
    "microsoft.managedidentity/userassignedidentities",
    "microsoft.communication/emailservices/domains",
    "microsoft.network/networkwatchers",
    "microsoft.web/connections",
    "microsoft.insights/autoscalesettings",
    "microsoft.insights/datacollectionrules",
    "microsoft.insights/datacollectionendpoints",
    "microsoft.insights/actiongroups",
    "microsoft.app/managedenvironments/certificates",
    "microsoft.cognitiveservices/accounts/projects",
    "microsoft.portal/dashboards",
    "microsoft.notificationhubs/namespaces/notificationhubs",
    "microsoft.eventgrid/systemtopics",
    # Already surfaced via the dedicated "AKS Clusters" expander (aks_provider.get_clusters(),
    # which carries richer AKS management-plane data) - would otherwise show up a second time
    # here as the bare Resource Graph entry for the same cluster.
    "microsoft.containerservice/managedclusters",
}


class ResourceService:
    """Service layer for resource management operations"""

    def __init__(self):
        """Initialize resource service with providers"""
        self.azure_provider = MockAzureProvider()
        self.aks_provider = MockAKSProvider()
        self.gitlab_provider = MockGitLabProvider()
        self.observability_provider = MockObservabilityProvider()
        self.cost_provider = MockCostProvider()
        self.relationship_discovery = AzureRelationshipDiscovery()
        self._per_resource_cache: Dict[str, Dict[str, Any]] = {}
        # Memoized get_cost_analysis() results, keyed by (from, to) - see that method's
        # docstring. Cleared by refresh_cost_data() on an actual (non-cooldown-blocked) refresh.
        self._cost_analysis_cache: Dict[Any, Dict[str, Any]] = {}

    def _cached(self, resource_id: str, cache_key: str, fetch_fn):
        """Memoize a per-resource live call (metrics/health/alerts/cost/logs) for the life of
        this (session-scoped) ResourceService instance. Without this, every unrelated Streamlit
        rerun while a resource stays selected re-issues the same live Azure Monitor/Cost
        Management/Log Analytics calls - besides the latency, Cost Management in particular is
        aggressively rate-limited (observed 429s even under light, single-resource testing)."""
        bucket = self._per_resource_cache.setdefault(resource_id, {})
        if cache_key not in bucket:
            bucket[cache_key] = fetch_fn()
        return bucket[cache_key]

    def invalidate_resource_cache(self, resource_id: Optional[str] = None) -> None:
        """Drop cached metrics/health/alerts/cost/logs for one resource, or all resources."""
        if resource_id is None:
            self._per_resource_cache.clear()
        else:
            self._per_resource_cache.pop(resource_id, None)

    def get_subscription(self) -> Dict[str, Any]:
        """Get subscription information"""
        return self.azure_provider.get_subscription()

    def get_resource_groups(self) -> List[Dict[str, Any]]:
        """Get all resource groups.

        Resource Graph's Resources table never returns the resource-group containers
        themselves, so they're derived from the resource_group/subscription_id already
        present on every discovered resource (same derivation the topology graph uses).
        """
        return self.relationship_discovery.resource_group_nodes(self.azure_provider.get_all_resources())

    def get_azure_resources(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get all Azure resources organized by category, for the sidebar/explorer."""
        all_resources = self.azure_provider.get_all_resources()
        categorized: Dict[str, List[Dict[str, Any]]] = {category: [] for category in _CATEGORY_ARM_TYPES}
        other_resources: List[Dict[str, Any]] = []
        for resource in all_resources:
            resource_type = (resource.get("type") or "").lower()
            matched = False
            for category, arm_type in _CATEGORY_ARM_TYPES.items():
                if resource_type == arm_type:
                    categorized[category].append(resource)
                    matched = True
                    break
            if not matched and resource_type not in _NOISE_ARM_TYPES:
                other_resources.append(resource)

        return {
            "resource_groups": self.relationship_discovery.resource_group_nodes(all_resources),
            "aks_clusters": self.aks_provider.get_clusters(),
            **categorized,
            "other_resources": other_resources,
        }
    
    def _matches_resource_id(self, resource: Dict[str, Any], resource_id: str) -> bool:
        """Check if a resource matches the given ID in any supported format."""
        normalized = normalize_resource_id(resource_id)
        candidates = {
            resource.get("id"),
            resource.get("resource_id"),
            resource.get("name"),
            normalize_resource_id(resource.get("id")),
            normalize_resource_id(resource.get("resource_id")),
        }
        return normalized in {normalize_resource_id(value) for value in candidates if value}

    def get_resource_by_id(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific resource by ID"""
        resource_groups = self.get_resource_groups()
        for rg in resource_groups:
            if self._matches_resource_id(rg, resource_id):
                return rg

        # Check the already-fetched (and cached) full discovery list before falling back to
        # a live single-resource Resource Graph query - the resource we're looking for is
        # almost always already in it, so this turns what used to be a guaranteed extra
        # network round-trip per lookup into a cheap in-memory scan.
        for resource in self.azure_provider.get_all_resources():
            if self._matches_resource_id(resource, resource_id):
                return resource

        resource = self.azure_provider.get_resource(resource_id)
        if resource:
            return resource

        # Deployments/pods live inside a cluster's own Kubernetes API, not Azure Resource
        # Manager, so - unlike everything else here - they aren't addressable by a bare ID
        # without already knowing which cluster to ask.
        for provider_method in (
            self.aks_provider.get_cluster,
            self.gitlab_provider.get_project,
        ):
            resource = provider_method(resource_id)
            if resource:
                return resource

        normalized = normalize_resource_id(resource_id)
        if normalized:
            for provider_method in (
                self.aks_provider.get_cluster,
                self.gitlab_provider.get_project,
            ):
                resource = provider_method(normalized)
                if resource:
                    return resource

        return None

    def _resolve_data_resource_id(self, resource_id: str) -> str:
        """Resolve resource ID for mock data lookups (metrics, alerts, costs)."""
        resource = self.get_resource_by_id(resource_id)
        if resource:
            return resource.get("name") or normalize_resource_id(resource.get("id")) or resource_id
        return normalize_resource_id(resource_id) or resource_id
    
    def get_resource_details(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Get comprehensive details for a resource"""
        resource = self.get_resource_by_id(resource_id)
        if not resource:
            return None
        
        # Get connected resources
        connected_resources = self.get_connected_resources(resource_id)

        # Add health status if available (routed through get_resource_health so it's cached
        # and uses the same resolved ARM id as every other per-resource call, instead of a
        # second, uncached, differently-resolved call to the same Resource Health API)
        health = self.get_resource_health(resource_id)
        
        return {
            **resource,
            "connected_resources": connected_resources,
            "health": health
        }
    
    def get_connected_resources(self, resource_id: str) -> List[Dict[str, Any]]:
        """Get resources connected to the specified resource"""
        resource = self.get_resource_by_id(resource_id)
        if not resource:
            return []
        
        connected = []
        dependencies = resource.get("dependencies", [])
        
        for dep_id in dependencies:
            dep_resource = self.get_resource_by_id(dep_id)
            if dep_resource:
                connected.append(dep_resource)
        
        return connected
    
    def get_resources_by_type(self, resource_type: str) -> List[Dict[str, Any]]:
        """Get resources filtered by type"""
        return self.azure_provider.get_resources_by_type(resource_type)
    
    def get_aks_clusters(self) -> List[Dict[str, Any]]:
        """Get all AKS clusters"""
        # Get from AKS provider
        return self.aks_provider.get_clusters()

    def get_cluster_namespaces(self, cluster_id: str) -> List[Dict[str, Any]]:
        """Get namespaces for a specific cluster. Raises AKSUnreachableError (see
        utils/k8s_safety.py) instead of crashing if the cluster's API server can't be reached -
        e.g. a private cluster off-VNet."""
        return call_with_timeout(self.aks_provider.get_namespaces, cluster_id)

    def get_cluster_nodes(self, cluster_id: str) -> List[Dict[str, Any]]:
        """Get nodes for a specific cluster"""
        return call_with_timeout(self.aks_provider.get_nodes, cluster_id)

    def get_cluster_deployments(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get deployments for a cluster, optionally filtered to one namespace"""
        return call_with_timeout(self.aks_provider.get_deployments, cluster_id, namespace)

    def get_namespace_deployments(self, cluster_id: str, namespace: str) -> List[Dict[str, Any]]:
        """Get deployments for a specific namespace"""
        return call_with_timeout(self.aks_provider.get_namespace_deployments, cluster_id, namespace)

    def get_cluster_replicasets(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get ReplicaSets for a cluster, optionally filtered to one namespace"""
        return call_with_timeout(self.aks_provider.get_replicasets, cluster_id, namespace)

    def get_cluster_pods(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get pods for a cluster, optionally filtered to one namespace"""
        return call_with_timeout(self.aks_provider.get_pods, cluster_id, namespace)

    def get_namespace_pods(self, cluster_id: str, namespace: str) -> List[Dict[str, Any]]:
        """Get pods for a specific namespace"""
        return call_with_timeout(self.aks_provider.get_pods, cluster_id, namespace)

    def get_cluster_services(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get services for a cluster, optionally filtered to one namespace"""
        return call_with_timeout(self.aks_provider.get_services, cluster_id, namespace)

    def get_cluster_ingress(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get ingress resources for a cluster, optionally filtered to one namespace"""
        return call_with_timeout(self.aks_provider.get_ingress, cluster_id, namespace)

    def get_cluster_events(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get recent events for a cluster, optionally filtered to one namespace"""
        return call_with_timeout(self.aks_provider.get_events, cluster_id, namespace)

    def get_pod_logs(
        self, cluster_id: str, namespace: str, pod_name: str, container: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get the latest logs for a specific pod"""
        return call_with_timeout(self.aks_provider.get_pod_logs, cluster_id, namespace, pod_name, container=container)

    def get_pod_events(self, cluster_id: str, namespace: str, pod_name: str) -> List[Dict[str, Any]]:
        """Get events for a specific pod"""
        return call_with_timeout(self.aks_provider.get_pod_events, cluster_id, namespace, pod_name)

    def get_cluster_configmaps(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get ConfigMaps for a cluster (optionally filtered to one namespace) - names and data keys only."""
        return call_with_timeout(self.aks_provider.get_configmaps, cluster_id, namespace)

    def get_cluster_secrets(self, cluster_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get Secrets for a cluster (optionally filtered to one namespace) - names and type only, never values."""
        return call_with_timeout(self.aks_provider.get_secrets, cluster_id, namespace)

    def search_resources(self, query: str) -> List[Dict[str, Any]]:
        """Search resources by name or ID"""
        results = []
        all_resources = self.get_azure_resources()
        
        for resource_type, resources in all_resources.items():
            for resource in resources:
                name = resource.get("name", "").lower()
                resource_id = resource.get("id", "").lower()
                if query.lower() in name or query.lower() in resource_id:
                    results.append(resource)
        
        return results
    
    def get_resource_hierarchy(self) -> Dict[str, Any]:
        """Get the complete resource hierarchy for the subscription"""
        subscription = self.get_subscription()
        resource_groups = self.get_resource_groups()
        
        hierarchy = {
            "subscription": subscription,
            "resource_groups": []
        }
        
        for rg in resource_groups:
            rg_data = {
                "resource_group": rg,
                "resources": {
                    "app_services": [],
                    "aks_clusters": [],
                    "sql_databases": [],
                    "redis_caches": [],
                    "storage_accounts": [],
                    "key_vaults": [],
                    "application_insights": [],
                    "log_analytics": []
                }
            }
            
            # Filter resources by resource group
            all_resources = self.get_azure_resources()
            for resource_type, resources in all_resources.items():
                if resource_type == "resource_groups":
                    continue
                for resource in resources:
                    if resource.get("resource_group") == rg.get("name"):
                        resource_type_key = resource_type.replace("_", "_")
                        if resource_type_key in rg_data["resources"]:
                            rg_data["resources"][resource_type_key].append(resource)
            
            hierarchy["resource_groups"].append(rg_data)
        
        return hierarchy

    def _resolve_arm_resource_id(self, resource_id: str) -> str:
        """Resolve to the full ARM resource ID, as required by Azure Monitor.

        Every current caller (the Infrastructure Explorer graph, Cost Analysis's resource
        table, docgen, ...) already passes the full ARM id straight from Resource Graph or
        Cost Management, so it's already exactly what this needs to return. Short-circuiting
        on that common case skips get_resource_by_id()'s linear scan over the entire discovery
        inventory (O(N) per call, and this runs once per resource in health/alerts/metrics
        sweeps, so O(N^2) overall for a full-subscription sweep) - only genuinely short/
        non-ARM ids (rare) fall through to the full lookup.
        """
        if resource_id and resource_id.startswith("/subscriptions/"):
            return resource_id
        resource = self.get_resource_by_id(resource_id)
        if resource:
            return resource.get("id") or resource_id
        return resource_id

    def get_resource_metrics(self, resource_id: str) -> List[Dict[str, Any]]:
        """Get live Azure Monitor metrics for a resource (cached per resource per session)."""
        return self._cached(resource_id, "metrics", lambda: self.observability_provider.get_metrics(
            self._resolve_arm_resource_id(resource_id)
        ))

    def get_resource_metrics_summary(self, resource_id: str) -> Dict[str, Any]:
        """Get a live Azure Monitor metrics summary for a resource (cached per resource per
        session). Reuses get_resource_metrics()'s own (also cached) result instead of letting
        the observability provider make a second, duplicate live Azure Monitor call for the
        same resource's metrics - previously every single-resource Utilization view fired the
        metric-definitions + metric-values calls twice over (once for "metrics", once again
        here for "metrics_summary"), doubling that view's Azure Monitor latency for no reason.
        """
        return self._cached(resource_id, "metrics_summary", lambda: self.observability_provider.get_resource_metrics_summary(
            self._resolve_arm_resource_id(resource_id),
            metrics=self.get_resource_metrics(resource_id),
        ))

    def get_resource_alerts(self, resource_id: str) -> List[Dict[str, Any]]:
        """Get live Azure Monitor alerts for a resource (cached per resource per session)."""
        return self._cached(resource_id, "alerts", lambda: self.observability_provider.get_alerts(
            self._resolve_arm_resource_id(resource_id)
        ))

    def get_resource_health(self, resource_id: str) -> Dict[str, Any]:
        """Get live Azure Resource Health status for a resource (cached per resource per session)."""
        return self._cached(resource_id, "health", lambda: self.observability_provider.get_health(
            self._resolve_arm_resource_id(resource_id)
        ))

    def get_resource_logs(self, resource_id: str) -> List[Dict[str, Any]]:
        """Get live Log Analytics logs for a resource (cached per resource per session)."""
        return self._cached(resource_id, "logs", lambda: self.observability_provider.get_logs(
            self._resolve_arm_resource_id(resource_id)
        ))

    def get_resource_cost(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Get live current/last month cost for a resource. Backed by the application-wide,
        24h-cached, single-flight Cost Management dataset shared by every session in this
        process (see AzureCostManagement.get_cost_by_resource) - not cached again here, since a
        second per-session cache on top of that would just risk going stale independently of it
        (e.g. surviving past a Refresh Cost Data click or the 24h TTL expiring)."""
        return self.cost_provider.get_monthly_cost(self._resolve_arm_resource_id(resource_id))

    def get_resource_cost_breakdown(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Get live cost breakdown for a resource (meter-category, a dedicated per-resource
        query - see AzureCostManagement.get_cost_breakdown - only ever fired for a single,
        already-selected resource, never looped across every discovered resource)."""
        return self.cost_provider.get_cost_breakdown(self._resolve_arm_resource_id(resource_id))

    def get_resource_daily_cost_trend(self, resource_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """Get live daily cost trend for a resource (dedicated per-resource query - see
        get_resource_cost_breakdown)."""
        return self.cost_provider.get_daily_cost_trend(self._resolve_arm_resource_id(resource_id), days=days)

    def get_subscription_cost_summary(self) -> Dict[str, Any]:
        """Get total subscription cost summary."""
        return self.cost_provider.get_total_subscription_cost()

    def get_top_cost_resources(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get top costly resources, enriched with each resource's region from the discovery
        inventory (matched by full Resource ID only - see get_cost_analysis's docstring on why
        a name-based fallback is never used here)."""
        items = self.cost_provider.get_top_cost_resources(limit=limit)
        inventory_by_full_id = {
            resource["id"].lower(): resource
            for resource in self.get_all_azure_resources_topology_only()
            if resource.get("id")
        }
        for item in items:
            inventory_resource = inventory_by_full_id.get((item.get("resource_id") or "").lower())
            item["region"] = (inventory_resource or {}).get("region") or "Unknown"
        return items

    def get_cost_analysis(self, time_period: Dict[str, str], caller: str = "get_cost_analysis") -> Dict[str, Any]:
        """Azure Portal-style Cost Analysis dataset for an arbitrary date range: total cost and
        one row per resource, enriched with the resource's real name/resource_group/type from
        the already-cached discovery inventory where a match exists (falling back to the
        ARM-id-derived values otherwise). Backed by a single Cost Management call grouped by
        ResourceId (see AzureCostManagement.get_cost_by_resource) - never one call per resource,
        cached application-wide for 24h so switching filters/search/sort in the UI, revisiting a
        historical range, or looking up any individual resource's cost elsewhere, never
        re-queries; concurrent callers for the same range collapse into one live call.

        Memoized per (from, to) on this ResourceService instance (session-scoped) - the
        enrichment below (matching every cost row against the resource inventory) previously
        re-ran from scratch on *every* Streamlit rerun this page causes, including ones that
        only changed a filter/search/sort widget and never touched the date range, even though
        the underlying Cost Management/Resource Graph data hadn't changed at all. Cleared by
        refresh_cost_data() so an explicit refresh still re-derives from the freshly-cleared
        upstream caches.
        """
        cache_key = (time_period["from"], time_period["to"])
        cached = self._cost_analysis_cache.get(cache_key)
        if cached is not None:
            return cached

        # The cost dataset (Cost Management, possibly a live call on a cold cache) and the
        # resource inventory (Resource Graph, likewise) are entirely independent - profiling a
        # cold load found the inventory fetch was previously running *after* the cost fetch
        # finished (adding its own ~3s on top of the cost call's own several-second-plus-retries
        # latency) purely because it was written as two sequential lines, not because either one
        # depends on the other's result. Fetching them concurrently removes that dead time.
        with log_timing(logger, f"ResourceService.get_cost_analysis[{caller}].fetch"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                dataset_future = pool.submit(self.cost_provider.get_cost_by_resource, time_period, caller=caller)
                inventory_future = pool.submit(self.get_all_azure_resources_topology_only)
                dataset = dataset_future.result()
                inventory = inventory_future.result()

        with log_timing(logger, f"ResourceService.get_cost_analysis[{caller}].enrich"):
            # Keyed by full ARM resource ID (case-insensitive) - the only identity that's ever
            # safe to enrich a Cost Management row from. A previous version of this also fell
            # back to matching by bare resource *name* when a cost row's resource_id wasn't in
            # the current inventory (e.g. the resource was deleted/moved since Cost Management
            # recorded that spend) - but names are not unique (verified on this subscription:
            # two different "optimusx-backend-plan" App Service Plans exist in different resource
            # groups, billed at very different amounts). That fallback silently relabeled a
            # Cost-Management-only row's resource_group/name/type with an unrelated *live*
            # resource's data just because the names matched - making two genuinely different
            # Resource IDs render as an identical "duplicate" row in the FinOps Cost Analysis
            # table. There is no name-based fallback that's actually safe here: when the full ID
            # doesn't match, the Cost-Management-derived fields already computed for this row
            # (parsed straight from its own resource_id - see
            # _resource_name_from_id/_resource_group_from_id/_resource_type_from_id) are used
            # as-is instead of guessing at a different resource.
            inventory_by_full_id: Dict[str, Dict[str, Any]] = {
                resource["id"].lower(): resource for resource in inventory if resource.get("id")
            }

            rows: List[Dict[str, Any]] = []
            for row in dataset.get("rows", []):
                # Cost Management's ResourceId dimension also enumerates zero-cost pseudo-resources
                # that were never actually billed (observed on this subscription: ~1,200+
                # Defender-for-Cloud per-container-image scan entries under
                # microsoft.security/pricings, every one at exactly 0 cost) - real Azure Portal Cost
                # Analysis doesn't surface these either, and listing them here would bury the
                # handful of resources that actually cost money under a wall of $0 noise.
                if not row["cost"]:
                    continue
                resource_id = row["resource_id"]
                inventory_resource = inventory_by_full_id.get(resource_id.lower())
                rows.append({
                    "resource_id": resource_id,
                    "resource_name": (inventory_resource or {}).get("name") or row["resource_name"],
                    "resource_group": (inventory_resource or {}).get("resource_group") or row["resource_group"],
                    "resource_type": (inventory_resource or {}).get("type") or row["resource_type"],
                    "region": (inventory_resource or {}).get("region") or "Unknown",
                    "cost": row["cost"],
                    "currency": row["currency"],
                })

        result = {
            "time_period": dataset.get("time_period", time_period),
            "rows": rows,
            "currency": dataset.get("currency", "USD"),
            "total_cost": dataset.get("total_cost", 0.0),
            "available": dataset.get("available", False),
        }
        # Only memoize a genuine, successful result. `dataset["available"]` is also False when
        # the underlying Cost Management call was transiently throttled (429) rather than the
        # period genuinely having no cost - AzureCostManagement.get_cost_by_resource no longer
        # caches that failure either (see its _cached_fetch docstring), so skipping it here too
        # means the very next call - any normal page rerun, not just the cooldown-gated "Refresh
        # Cost Data" button - retries live instead of replaying the same "unavailable" result for
        # the rest of this Streamlit session.
        if result["available"]:
            self._cost_analysis_cache[cache_key] = result
        return result

    def get_cost_trend(
        self, time_period: Dict[str, str], granularity: str = "Daily", caller: str = "get_cost_trend"
    ) -> List[Dict[str, Any]]:
        """Subscription-wide cost trend for an arbitrary date range/granularity - one ungrouped
        Cost Management call, cached application-wide for 24h by (period, granularity)."""
        with log_timing(logger, f"ResourceService.get_cost_trend[{caller}]"):
            return self.cost_provider.get_cost_trend(time_period, granularity=granularity, caller=caller)

    def refresh_cost_data(self, caller: str = "refresh_cost_data") -> Dict[str, Any]:
        """Explicit, cooldown-limited cache-bypassing cost refresh - wired to the FinOps
        "Refresh Cost Data" button. Application-wide (the cooldown and cache it clears are
        shared by every session in this process), so rapid repeated clicks - from one user or
        several - still only ever trigger one fresh Cost Management call. Returns
        {"refreshed": bool, "retry_after_seconds": float} so the UI can tell the difference
        between "refreshed for real" and "blocked by cooldown, try again in Ns"."""
        result = self.cost_provider.refresh_cost_cache(caller=caller)
        if result.get("refreshed"):
            # The underlying Azure-side cost cache was actually cleared - drop this instance's
            # own memoized get_cost_analysis() results too, or the next call would keep
            # returning the pre-refresh enrichment even though fresh data is now available.
            self._cost_analysis_cache.clear()
        return result

    def get_subscription_health_overview(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """Aggregate health/alerts across the discovered resources, for FinOps/Monitoring to
        show something real (not a blank page) when nothing is selected.

        `limit=None` (the default) covers every discovered resource - there's no
        subscription-wide health/alerts endpoint like there is for cost, so this is a real
        live Azure Monitor/Resource Health call per resource (2 calls each), run
        concurrently. On a cold cache (first load this session) that's genuinely a couple of
        minutes for a subscription this size; every subsequent load reuses the per-resource
        cache and is instant. Pass an explicit limit to cap it back down if that first-load
        cost isn't worth it for a given caller.

        Sampled from the full resource inventory (`get_all_azure_resources_topology_only`),
        not `get_azure_resources()` - the latter only recognizes the 7 ARM types the
        sidebar/explorer categorizes by (App Service, SQL, Redis, Storage, Key Vault, App
        Insights, Log Analytics) and silently drops everything else (VMs, AKS, Databricks,
        Container Registry, etc.), which made this overview look like only a handful of
        resources existed in a subscription that actually has hundreds.
        """
        flat: List[Dict[str, Any]] = self.get_all_azure_resources_topology_only()
        sample = flat if limit is None else flat[:limit]

        def _assess(resource: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            resource_id = resource.get("id") or resource.get("resource_id") or resource.get("name")
            if not resource_id:
                return None
            try:
                return {
                    "resource": resource,
                    "health": self.get_resource_health(resource_id) or {},
                    "alerts": self.get_resource_alerts(resource_id) or [],
                }
            except Exception as exc:
                # get_resource_health/get_resource_alerts already catch their own
                # request-level errors and return a safe "Unknown"/[] fallback - this is a
                # last-resort guard against anything else (a genuinely unexpected bug for one
                # resource) so pool.map() below can't have its whole result poisoned by a
                # single bad resource; that one resource just contributes "unknown" health and
                # no alerts to the aggregate instead of blocking the entire page.
                logger.warning("get_subscription_health_overview: assessment failed for %s: %s", resource_id, exc)
                return {"resource": resource, "health": {}, "alerts": []}

        # Each resource needs 2 independent live calls (health + alerts) - fetched
        # concurrently across the whole sample instead of one resource at a time, which is
        # what made this overview take minutes even at a small sample size. max_workers=25
        # keeps a full 771-resource sweep (~1,500 calls) to a couple of minutes instead of
        # tens of minutes, without firing so many at once that Azure Monitor throttles it
        # the way Cost Management did. Callers that don't want to wait that long for the
        # default/automatic view (see dashboard/pages/finops.py, monitoring.py) pass an
        # explicit `limit` instead of leaving this uncapped.
        with log_timing(logger, f"ResourceService.get_subscription_health_overview[sample={len(sample)}]"):
            with ThreadPoolExecutor(max_workers=25) as pool:
                assessments = [item for item in pool.map(_assess, sample) if item]

        health_counts = {"healthy": 0, "warning": 0, "critical": 0, "unknown": 0}
        total_active_alerts = 0
        attention: List[Dict[str, Any]] = []

        for item in assessments:
            resource = item["resource"]
            resource_id = resource.get("id") or resource.get("resource_id") or resource.get("name")
            status = (item["health"].get("health_status") or "unknown").lower()
            if status in ("healthy", "available"):
                bucket = "healthy"
            elif status in ("warning", "degraded"):
                bucket = "warning"
            elif status in ("critical", "unavailable", "error", "failed"):
                bucket = "critical"
            else:
                bucket = "unknown"
            health_counts[bucket] += 1

            active = [a for a in item["alerts"] if (a.get("status") or "").lower() == "active"]
            total_active_alerts += len(active)
            if active:
                severities = [a.get("severity", "Info") for a in active]
                worst = "Critical" if "Critical" in severities else ("Warning" if "Warning" in severities else "Info")
                attention.append({
                    "name": resource.get("name", resource_id),
                    "type": resource.get("type", "unknown"),
                    "active_alerts": len(active),
                    "worst_severity": worst,
                })

        attention.sort(key=lambda item: item["active_alerts"], reverse=True)
        return {
            "sampled_count": len(sample),
            "total_resource_count": len(flat),
            "health_counts": health_counts,
            "total_active_alerts": total_active_alerts,
            "attention": attention[:10],
        }

    def get_gitlab_projects(self) -> List[Dict[str, Any]]:
        """Get all GitLab projects the configured token can access."""
        return self.gitlab_provider.get_projects()

    def get_project_branches(self, project_id: str) -> List[Dict[str, Any]]:
        """Get branches for a GitLab project."""
        return self.gitlab_provider.get_branches(project_id)

    def get_project_pipelines(self, project_id: str) -> List[Dict[str, Any]]:
        """Get pipelines for a GitLab project."""
        return self.gitlab_provider.get_pipelines(project_id)

    def get_pipeline_stages(self, project_id: str, pipeline_id: str) -> List[Dict[str, Any]]:
        """Get stage status rollups for a GitLab pipeline."""
        return self.gitlab_provider.get_pipeline_stages(project_id, pipeline_id)

    def get_pipeline_jobs(self, project_id: str, pipeline_id: str) -> List[Dict[str, Any]]:
        """Get jobs for a GitLab pipeline."""
        return self.gitlab_provider.get_jobs(project_id, pipeline_id)

    def get_project_latest_commit(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest commit for a GitLab project."""
        return self.gitlab_provider.get_latest_commit(project_id)

    def get_commit_diff(self, project_id: str, sha: str) -> List[Dict[str, Any]]:
        """Get the changed-files diff for a single commit (cached per project+commit per
        session - the Commits tab re-renders on every unrelated widget interaction while a
        commit stays selected for investigation)."""
        return self._cached(project_id, f"gitlab_commit_diff:{sha}", lambda: self.gitlab_provider.get_commit_diff(project_id, sha))

    def get_pipeline_artifacts(self, project_id: str, pipeline_id: str) -> List[Dict[str, Any]]:
        """Get artifact metadata for every job in a pipeline."""
        return self.gitlab_provider.get_pipeline_artifacts(project_id, pipeline_id)

    def get_merge_request_for_branch(self, project_id: str, branch_name: str) -> Optional[Dict[str, Any]]:
        """Get the merge request associated with a branch, if any."""
        return self.gitlab_provider.get_merge_request_for_branch(project_id, branch_name)

    def investigate_pipeline_failure(self, project_id: str, pipeline_id: Optional[str] = None) -> Dict[str, Any]:
        """Build a structured investigation report comparing the latest failed pipeline to the last successful one."""
        return self.gitlab_provider.investigate_pipeline_failure(project_id, pipeline_id)

    def investigate_pipeline(self, project_id: str, pipeline_id: Optional[str] = None) -> Dict[str, Any]:
        """Deep, live evidence for a failed pipeline: failure point, error message, stack
        trace, artifacts, and a same-job comparison with the last successful pipeline
        (Task 15 - facts only, no root cause). Not cached: only ever triggered by an explicit
        "Run Advanced RCA" click, never on a page rerun."""
        return self.gitlab_provider.investigate_pipeline(project_id, pipeline_id)

    def investigate_git_changes(self, project_id: str, pipeline_id: Optional[str] = None) -> Dict[str, Any]:
        """Live Git change evidence for a pipeline: triggering commit, previous successful
        commit, diff, and the linked merge request's full detail - approvals, comments,
        reviewers (Task 16 - facts only, no root cause). Not cached, for the same reason as
        investigate_pipeline() above."""
        return self.gitlab_provider.investigate_git_changes(project_id, pipeline_id)

    def get_project_merge_requests(self, project_id: str) -> List[Dict[str, Any]]:
        """Get open merge requests for a GitLab project (cached per project per session - the
        MR tab re-renders on every unrelated widget interaction on the GitLab Workspace page)."""
        return self._cached(project_id, "gitlab_merge_requests", lambda: self.gitlab_provider.get_merge_requests(project_id))

    def get_repository_profile(self, project_id: str, ref: Optional[str] = None) -> Dict[str, Any]:
        """One-shot repository structure profile: tech stack, README, CI config, Dockerfile(s),
        Helm chart(s), and Kubernetes manifest(s) (Task 24: documentation generator)."""
        return self.gitlab_provider.get_repository_profile(project_id, ref=ref)

    def get_project_environments(self, project_id: str) -> List[Dict[str, Any]]:
        """Get deployment environments for a GitLab project."""
        return self.gitlab_provider.get_environments(project_id)

    def get_project_recent_commits(self, project_id: str, ref: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """Get the most recent commits on a GitLab project's branch (cached per project per
        session, keyed by ref/limit - the Commits tab re-renders on every unrelated widget
        interaction on the GitLab Workspace page, e.g. picking a commit to investigate)."""
        return self._cached(
            project_id, f"gitlab_recent_commits:{ref}:{limit}",
            lambda: self.gitlab_provider.get_recent_commits(project_id, ref=ref, limit=limit),
        )

    def get_all_azure_resources_raw(self) -> List[Dict[str, Any]]:
        """Get every Azure resource in the subscription, unfiltered, straight from Resource
        Graph (Task 24: documentation generator project discovery/inventory)."""
        return self.azure_provider.get_all_resources()

    def get_all_azure_resources_topology_only(self) -> List[Dict[str, Any]]:
        """Get every Azure resource's topology fields only (id/name/type/resource_group/...,
        no ARM `properties`/`identity` metadata) - for the Infrastructure Explorer's G6 tree,
        which only ever groups/labels by those fields and never reads the ARM metadata blobs.
        Much cheaper to fetch/refresh than get_all_azure_resources_raw() at 800-1000+ resources."""
        return self.azure_provider.get_all_resources_lightweight()
