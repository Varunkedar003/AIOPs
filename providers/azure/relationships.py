"""Azure resource relationship discovery.

Derives dependency edges between resources already discovered via Azure
Resource Graph, using each resource's `properties`, `tags`, and `identity`
metadata. Resource Graph returns all three for every resource in the same
discovery query, so no extra ARM calls are needed here.

Supported relationships:
    - Resource Group -> Resource (every discovered resource)
    - App Service -> App Service Plan (serverFarmId)
    - App Service -> VNet, if regionally integrated (virtualNetworkSubnetId)
    - App Service -> Application Insights, if linked (hidden-link tag)
    - AKS -> Node Resource Group (nodeResourceGroup)
    - Any resource with a managed identity -> Key Vault, if granted an
      access policy (identity.principalId found in the vault's access
      policies) - this is what "App Service -> Key Vault" resolves to
    - Any resource -> Private Endpoint, if present (privateEndpointConnections),
      which is how "SQL -> Private Endpoint" is covered

App Service -> Storage Account isn't included: the only reliable signal
for that link is the app's connection-string settings, which live behind
a separate ARM "list app settings" action rather than in Resource Graph
or any field returned by it - not implemented here.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_HIDDEN_LINK_PREFIX = "hidden-link:"
_APP_INSIGHTS_TYPE_FRAGMENT = "/providers/microsoft.insights/components/"
_APP_SERVICE_TYPE = "microsoft.web/sites"
_AKS_TYPE = "microsoft.containerservice/managedclusters"
_KEY_VAULT_TYPE = "microsoft.keyvault/vaults"


def _resource_group_node_id(subscription_id: str, resource_group: str) -> str:
    return f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"


def _subnet_to_vnet_id(subnet_id: str) -> Optional[str]:
    """Given a subnet ARM resource ID, return its parent virtual network's ARM ID."""
    marker = "/subnets/"
    idx = subnet_id.lower().find(marker)
    return subnet_id[:idx] if idx != -1 else None


class AzureRelationshipDiscovery:
    """Builds resource-group nodes and dependency edges for discovered Azure resources."""

    def resource_group_nodes(self, resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Synthesize one node per unique (subscription, resource group) pair seen among resources.

        Resource Graph's "Resources" table only returns actual resources, not the resource
        group containers themselves, so those nodes are built here instead of queried.
        """
        seen: Dict[str, Dict[str, Any]] = {}
        for resource in resources:
            resource_group = resource.get("resource_group")
            subscription_id = resource.get("subscription_id")
            if not (resource_group and subscription_id):
                continue

            rg_id = _resource_group_node_id(subscription_id, resource_group)
            if rg_id in seen:
                continue

            seen[rg_id] = {
                "id": rg_id,
                "resource_id": rg_id,
                "name": resource_group,
                "type": "microsoft.resources/resourcegroups",
                "resource_type": "microsoft.resources/resourcegroups",
                "resource_group": resource_group,
                "region": resource.get("location", ""),
                "location": resource.get("location", ""),
                "subscription_id": subscription_id,
                "subscription": subscription_id,
                "tags": {},
            }
        return list(seen.values())

    def build_edges(self, resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Derive every supported relationship edge for a set of discovered resources."""
        edges: List[Dict[str, Any]] = list(self._resource_group_edges(resources))
        key_vaults = [r for r in resources if (r.get("type") or "").lower() == _KEY_VAULT_TYPE]

        for resource in resources:
            resource_type = (resource.get("type") or "").lower()
            properties = resource.get("_properties") or {}

            if resource_type == _APP_SERVICE_TYPE:
                edges.extend(self._app_service_plan_edge(resource, properties))
                edges.extend(self._app_service_vnet_edge(resource, properties))
                edges.extend(self._app_insights_edge(resource))

            if resource_type == _AKS_TYPE:
                edges.extend(self._aks_node_resource_group_edge(resource, properties))

            edges.extend(self._private_endpoint_edges(resource, properties))
            edges.extend(self._identity_key_vault_edges(resource, key_vaults))

        return self._dedupe(edges)

    @staticmethod
    def _edge(source: str, target: str, relationship: str) -> Dict[str, Any]:
        return {"source": source, "target": target, "relationship": relationship}

    def _resource_group_edges(self, resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        edges = []
        for resource in resources:
            resource_group = resource.get("resource_group")
            subscription_id = resource.get("subscription_id")
            resource_id = resource.get("id")
            if not (resource_group and subscription_id and resource_id):
                continue
            rg_id = _resource_group_node_id(subscription_id, resource_group)
            edges.append(self._edge(rg_id, resource_id, "contains"))
        return edges

    def _app_service_plan_edge(self, resource: Dict[str, Any], properties: Dict[str, Any]) -> List[Dict[str, Any]]:
        plan_id = properties.get("serverFarmId")
        if not (plan_id and resource.get("id")):
            return []
        return [self._edge(resource["id"], plan_id, "hosted by")]

    def _app_service_vnet_edge(self, resource: Dict[str, Any], properties: Dict[str, Any]) -> List[Dict[str, Any]]:
        subnet_id = properties.get("virtualNetworkSubnetId")
        if not (subnet_id and resource.get("id")):
            return []
        vnet_id = _subnet_to_vnet_id(subnet_id)
        if not vnet_id:
            return []
        return [self._edge(resource["id"], vnet_id, "vnet integration")]

    def _app_insights_edge(self, resource: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not resource.get("id"):
            return []
        tags = resource.get("tags") or {}
        for tag_key in tags:
            if not tag_key.lower().startswith(_HIDDEN_LINK_PREFIX):
                continue
            linked_id = tag_key[len(_HIDDEN_LINK_PREFIX):]
            if _APP_INSIGHTS_TYPE_FRAGMENT in linked_id.lower():
                return [self._edge(resource["id"], linked_id, "monitored by")]
        return []

    def _aks_node_resource_group_edge(self, resource: Dict[str, Any], properties: Dict[str, Any]) -> List[Dict[str, Any]]:
        node_rg = properties.get("nodeResourceGroup")
        subscription_id = resource.get("subscription_id")
        if not (node_rg and subscription_id and resource.get("id")):
            return []
        node_rg_id = _resource_group_node_id(subscription_id, node_rg)
        return [self._edge(resource["id"], node_rg_id, "node resource group")]

    def _private_endpoint_edges(self, resource: Dict[str, Any], properties: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not resource.get("id"):
            return []
        connections = properties.get("privateEndpointConnections") or []
        edges = []
        for connection in connections:
            pe_id = ((connection or {}).get("properties") or {}).get("privateEndpoint", {}).get("id")
            if pe_id:
                edges.append(self._edge(resource["id"], pe_id, "private endpoint"))
        return edges

    def _identity_key_vault_edges(
        self, resource: Dict[str, Any], key_vaults: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not resource.get("id"):
            return []
        identity = resource.get("_identity") or {}
        principal_id = identity.get("principalId")
        if not principal_id:
            return []

        edges = []
        for vault in key_vaults:
            if vault.get("id") == resource.get("id"):
                continue
            vault_properties = vault.get("_properties") or {}
            access_policies = vault_properties.get("accessPolicies") or []
            has_access = any((policy or {}).get("objectId") == principal_id for policy in access_policies)
            if has_access and vault.get("id"):
                edges.append(self._edge(resource["id"], vault["id"], "reads secrets from"))
        return edges

    @staticmethod
    def _dedupe(edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        deduped = []
        for edge in edges:
            key = (edge["source"], edge["target"], edge["relationship"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(edge)
        return deduped
