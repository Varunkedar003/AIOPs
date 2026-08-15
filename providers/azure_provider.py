from typing import Any, Dict, List, Optional
from .base_provider import BaseProvider
from .azure.resource_graph import AzureResourceGraph


class MockAzureProvider(BaseProvider):
    """Azure resource provider.

    Resource discovery (get_all_resources/get_resource/get_resources_by_type) is
    backed by live Azure Resource Graph queries. Everything else remains an
    empty placeholder pending later phases.
    """

    def __init__(self, resource_graph: Optional[AzureResourceGraph] = None):
        self._resource_graph = resource_graph or AzureResourceGraph()
        self._resources_cache: Optional[List[Dict[str, Any]]] = None
        self._resources_cache_lightweight: Optional[List[Dict[str, Any]]] = None

    def get_subscription(self) -> Dict[str, Any]:
        """Get subscription information"""
        return {}

    def get_resource_groups(self) -> List[Dict[str, Any]]:
        """Get all resource groups"""
        return []

    def get_all_resources(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Get all Azure resources across all types, via Azure Resource Graph.

        Cached on this instance after the first call - as long as the app keeps this
        provider alive for the session (see ResourceService/GraphService), every page's
        Streamlit rerun reuses the same discovery result instead of re-querying Azure
        (and re-authenticating) on every interaction. Pass force_refresh=True to bypass
        the cache and re-fetch.
        """
        if force_refresh or self._resources_cache is None:
            self._resources_cache = self._resource_graph.list_resources()
        return self._resources_cache

    def get_all_resources_lightweight(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Get every resource's topology-relevant fields only (no `identity`/`properties` ARM
        metadata blobs) - for views that only need id/name/type/resource_group (e.g. the
        Infrastructure Explorer's G6 topology tree), not full relationship/edge derivation.

        Cached separately from get_all_resources() so this stays cheap regardless of whether
        the heavier discovery has ever run, and vice versa.
        """
        if force_refresh or self._resources_cache_lightweight is None:
            self._resources_cache_lightweight = self._resource_graph.list_resources_lightweight()
        return self._resources_cache_lightweight

    def refresh(self) -> None:
        """Drop the cached discovery results so the next get_all_resources*() call re-fetches."""
        self._resources_cache = None
        self._resources_cache_lightweight = None

    def get_resource(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific resource by ID, via Azure Resource Graph"""
        return self._resource_graph.get_resource(resource_id)

    def get_resources_by_type(self, resource_type: str) -> List[Dict[str, Any]]:
        """Get resources filtered by type"""
        return self._filter_by_field(self.get_all_resources(), "type", resource_type)

    def get_connection_error(self) -> Optional[str]:
        """User-facing message if the last Resource Graph call failed to authenticate/connect, else None."""
        return "Unable to connect to Azure" if self._resource_graph.last_error else None

    def get_connected_resources(self, resource_id: str) -> List[Dict[str, Any]]:
        """Get resources that are connected to the specified resource"""
        return []

    def get_app_services(self) -> List[Dict[str, Any]]:
        """Get all App Services"""
        return []

    def get_sql_databases(self) -> List[Dict[str, Any]]:
        """Get all SQL databases"""
        return []

    def get_redis_caches(self) -> List[Dict[str, Any]]:
        """Get all Redis caches"""
        return []

    def get_storage_accounts(self) -> List[Dict[str, Any]]:
        """Get all storage accounts"""
        return []

    def get_key_vaults(self) -> List[Dict[str, Any]]:
        """Get all Key Vaults"""
        return []

    def get_application_insights(self) -> List[Dict[str, Any]]:
        """Get all Application Insights instances"""
        return []

    def get_log_analytics(self) -> List[Dict[str, Any]]:
        """Get all Log Analytics workspaces"""
        return []
