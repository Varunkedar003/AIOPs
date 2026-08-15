"""Azure resource discovery via Azure Resource Graph.

Uses the Resource Graph "Resources" table (a single cross-resource-type
query) instead of calling each ARM service's individual list API.
"""
import logging
from typing import Any, Dict, List, Optional

from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions

from .auth import AzureAuth
from utils.timing import log_timing

logger = logging.getLogger(__name__)

# Resource Graph caps a single response at 1000 rows (`result_truncated`/`skip_token` on the
# response signal there's more) - a subscription with more resources than that would previously
# have been silently truncated to its first page with no continuation. Mirrors the same
# `skip_token` pagination pattern already used for Cost Management (see
# AzureCostManagement._follow_pagination).
_PAGE_SIZE = 1000
_MAX_PAGINATION_PAGES = 20

_DISCOVERY_PROJECTION = (
    "project id, name, type, resourceGroup, location, subscriptionId, tags, "
    "identity, properties, "
    "provisioningState = tostring(properties.provisioningState)"
)

# Same shape as _DISCOVERY_PROJECTION minus `identity`/`properties` - those two fields carry the
# full raw ARM metadata blob (can be tens of KB per resource for VMs/NSGs/App Services) and are
# only ever read for relationship-edge derivation (see relationships.py), never by a plain
# resource list/topology view. Trimming them out of that query is what actually cuts the Resource
# Graph query/serialize/transfer size down for callers that just need id/name/type/resource_group.
_TOPOLOGY_PROJECTION = (
    "project id, name, type, resourceGroup, location, subscriptionId, tags, "
    "provisioningState = tostring(properties.provisioningState)"
)


def _escape_kql_string(value: str) -> str:
    """Escape a value for safe interpolation into a single-quoted KQL string literal."""
    return value.replace("'", "''")


class AzureResourceGraph:
    """Queries Azure Resource Graph for resource discovery."""

    def __init__(self, azure_auth: Optional[AzureAuth] = None):
        self.azure_auth = azure_auth or AzureAuth()
        self._client: Optional[ResourceGraphClient] = None
        self.last_error: Optional[str] = None

    def is_connected(self) -> bool:
        """Whether the most recent query succeeded without an authentication/connection error."""
        return self.last_error is None

    def _get_client(self) -> ResourceGraphClient:
        if self._client is None:
            credential = self.azure_auth.get_credential()
            self._client = ResourceGraphClient(credential)
        return self._client

    def _run_query(self, query: str) -> List[Dict[str, Any]]:
        """Run a Resource Graph query, following `skip_token` continuation pages until the
        response reports nothing left (`skip_token` empty) or `_MAX_PAGINATION_PAGES` is hit.

        A single page tops out at `_PAGE_SIZE` rows - for any subscription with more resources
        than that, the previous single-page call silently returned only the first `_PAGE_SIZE`
        of them with no indication anything was missing.
        """
        client = self._get_client()
        rows: List[Dict[str, Any]] = []
        skip_token: Optional[str] = None
        pages = 0
        with log_timing(logger, "AzureResourceGraph query"):
            while True:
                pages += 1
                request = QueryRequest(
                    query=query,
                    subscriptions=[self.azure_auth.subscription_id],
                    options=QueryRequestOptions(top=_PAGE_SIZE, skip_token=skip_token),
                )
                response = client.resources(request)
                rows.extend(response.data or [])
                skip_token = response.skip_token
                if not skip_token or pages >= _MAX_PAGINATION_PAGES:
                    break
        return rows

    @staticmethod
    def _dedupe_by_id(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Keep exactly one row per Resource ID (case-insensitive - ARM IDs are
        case-insensitive by convention), first occurrence wins.

        Resource Graph is expected to return one row per resource, but its docs note query
        results are only eventually consistent - a resource can in principle be re-returned
        across two continuation pages if it's touched between page fetches. This is the single
        choke point every discovery call goes through, so every page/component reading resource
        lists (Infrastructure Explorer, sidebar, search, FinOps, Resource Workspace, ...) gets
        this guarantee for free instead of each needing its own duplicate filtering.
        """
        seen: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            resource_id = (row.get("id") or "").lower()
            if not resource_id:
                continue
            seen.setdefault(resource_id, row)
        if len(seen) != len(rows):
            logger.warning(
                "Azure Resource Graph returned %d row(s) sharing a Resource ID already seen "
                "this query - deduped %d rows down to %d unique resources.",
                len(rows) - len(seen), len(rows), len(seen),
            )
        return list(seen.values())

    @staticmethod
    def _to_resource_dict(row: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a Resource Graph row into the resource shape the rest of the app expects.

        Includes the field names the existing (unchanged) UI already reads
        (region/subscription/state) as aliases, alongside the canonical names.
        `_properties`/`_identity` carry the raw ARM metadata used for relationship
        discovery (see relationships.py) - not meant for display.
        """
        resource_id = row.get("id", "") or ""
        resource_type = row.get("type", "unknown") or "unknown"
        location = row.get("location", "") or ""
        subscription_id = row.get("subscriptionId", "") or ""
        provisioning_state = row.get("provisioningState") or "Unknown"

        return {
            "id": resource_id,
            "resource_id": resource_id,
            "name": row.get("name", "Unknown") or "Unknown",
            "type": resource_type,
            "resource_type": resource_type,
            "resource_group": row.get("resourceGroup", "") or "",
            "location": location,
            "region": location,
            "subscription_id": subscription_id,
            "subscription": subscription_id,
            "tags": row.get("tags") or {},
            "provisioning_state": provisioning_state,
            "state": provisioning_state,
            "_properties": row.get("properties") or {},
            "_identity": row.get("identity") or {},
        }

    def list_resources(self) -> List[Dict[str, Any]]:
        """Discover all resources in the configured subscription, one entry per unique Resource
        ID (see _dedupe_by_id)."""
        try:
            rows = self._dedupe_by_id(self._run_query(f"Resources | {_DISCOVERY_PROJECTION}"))
            self.last_error = None
            return [self._to_resource_dict(row) for row in rows]
        except Exception as exc:
            logger.error("Azure Resource Graph discovery query failed: %s", exc)
            self.last_error = str(exc)
            return []

    def list_resources_lightweight(self) -> List[Dict[str, Any]]:
        """Discover all resources, without the `identity`/`properties` ARM metadata blobs, one
        entry per unique Resource ID (see _dedupe_by_id).

        For a plain topology view (Subscription -> Resource Group -> Type -> Resource, no
        relationship edges) this is the only data actually needed - `_properties`/`_identity`
        end up `{}` on every returned dict rather than the full ARM payload. Separate from
        list_resources() so callers that DO need relationship data (see relationships.py) are
        unaffected.
        """
        try:
            rows = self._dedupe_by_id(self._run_query(f"Resources | {_TOPOLOGY_PROJECTION}"))
            self.last_error = None
            return [self._to_resource_dict(row) for row in rows]
        except Exception as exc:
            logger.error("Azure Resource Graph lightweight discovery query failed: %s", exc)
            self.last_error = str(exc)
            return []

    def get_resource(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single resource by its full ARM resource ID."""
        if not resource_id:
            return None

        try:
            escaped_id = _escape_kql_string(resource_id)
            query = f"Resources | where id =~ '{escaped_id}' | {_DISCOVERY_PROJECTION}"
            rows = self._run_query(query)
            self.last_error = None
            return self._to_resource_dict(rows[0]) if rows else None
        except Exception as exc:
            logger.error("Azure Resource Graph lookup failed for %s: %s", resource_id, exc)
            self.last_error = str(exc)
            return None
