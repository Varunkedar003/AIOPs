import logging
import math
from typing import Any, Dict, List, Optional, Tuple
from providers import MockAzureProvider, MockAKSProvider
from providers.azure.relationships import AzureRelationshipDiscovery
from utils.resource_id import normalize_resource_id

logger = logging.getLogger(__name__)


def _node_key(node: Dict[str, Any]) -> str:
    """Case-insensitive lookup key for a node - Azure ARM resource IDs are case-insensitive
    by convention, but come back with inconsistent casing depending on whether they're a
    resource's own `id` or referenced from another resource's `properties` (e.g.
    `serverFarmId`), which is exactly what previously let a case-mismatched edge slip past a
    naive `in` check and reach Cytoscape referencing a node that was never added."""
    return (node.get("id") or node.get("resource_id") or "").strip().lower()


class GraphService:
    """Service layer for infrastructure topology graph generation with context-based navigation"""

    def __init__(self, azure_provider: Optional[MockAzureProvider] = None):
        """Initialize graph service with providers.

        Args:
            azure_provider: an existing provider to reuse (e.g. the session-scoped one
                on ResourceService) so its cached discovery result/credential survive
                across reruns instead of re-querying Azure every time. Defaults to a
                fresh provider for backward compatibility.
        """
        self.azure_provider = azure_provider or MockAzureProvider()
        self.aks_provider = MockAKSProvider()
        self.relationship_discovery = AzureRelationshipDiscovery()

    def load_topology_data(self) -> Dict[str, Any]:
        """Load topology data from live Azure Resource Graph resource discovery plus
        automatically derived relationships (see providers/azure/relationships.py).

        Nodes are built first, then every edge is validated against that node set before
        being added - an edge referencing an Azure resource Resource Graph never returned
        (e.g. an App Service Plan, Key Vault, or Storage Account outside discovery scope)
        gets a placeholder node instead of being silently dropped, so the graph keeps
        rendering and the missing relationship stays visible instead of disappearing.
        Cytoscape.js is never handed an edge whose source/target isn't a real node id.
        """
        resources = self.azure_provider.get_all_resources()
        resource_group_nodes = self.relationship_discovery.resource_group_nodes(resources)
        raw_edges = self.relationship_discovery.build_edges(resources)

        # 1. Build all nodes first.
        nodes = resource_group_nodes + resources
        nodes_by_key: Dict[str, Dict[str, Any]] = {_node_key(node): node for node in nodes if _node_key(node)}

        # 2. Build all edges second, resolving each endpoint against the node set built above -
        # creating a placeholder node for any Azure resource that was referenced but never
        # discovered, rather than ever emitting an edge with a missing endpoint.
        placeholders_by_key: Dict[str, Dict[str, Any]] = {}
        validated_edges: List[Dict[str, Any]] = []
        skipped_edges: List[Dict[str, Any]] = []

        for edge in raw_edges:
            source_id = (edge.get("source") or "").strip()
            target_id = (edge.get("target") or "").strip()
            if not source_id or not target_id:
                skipped_edges.append(edge)
                continue

            resolved_source = self._resolve_or_placeholder(source_id, nodes_by_key, placeholders_by_key)
            resolved_target = self._resolve_or_placeholder(target_id, nodes_by_key, placeholders_by_key)
            validated_edges.append({**edge, "source": resolved_source, "target": resolved_target})

        if skipped_edges:
            logger.warning(
                "Topology builder: skipped %d edge(s) with an empty source/target: %s",
                len(skipped_edges),
                [(e.get("source"), e.get("target"), e.get("relationship")) for e in skipped_edges],
            )

        all_nodes = list(nodes_by_key.values()) + list(placeholders_by_key.values())
        if placeholders_by_key:
            logger.warning(
                "Topology builder: added %d placeholder node(s) for Azure resources referenced "
                "by a relationship but never returned by Resource Graph: %s",
                len(placeholders_by_key),
                [n.get("name") for n in placeholders_by_key.values()],
            )

        # 3. Final validation pass before this topology is ever handed to Cytoscape - never
        # let an edge through whose source/target isn't in the final node set.
        return self._validate_edges_against_nodes(all_nodes, validated_edges)

    @staticmethod
    def _resolve_or_placeholder(
        raw_id: str, nodes_by_key: Dict[str, Dict[str, Any]], placeholders_by_key: Dict[str, Dict[str, Any]]
    ) -> str:
        """Return the canonical node id matching `raw_id` (case-insensitive); if Resource Graph
        never returned that resource, create (or reuse) a placeholder node for it so the edge
        still has a real node to point at."""
        key = raw_id.lower()
        existing = nodes_by_key.get(key) or placeholders_by_key.get(key)
        if existing:
            return existing.get("id") or existing.get("resource_id") or raw_id

        placeholder = GraphService._make_placeholder_node(raw_id)
        placeholders_by_key[key] = placeholder
        return placeholder["id"]

    @staticmethod
    def _make_placeholder_node(raw_id: str) -> Dict[str, Any]:
        """A stand-in node for an Azure resource a relationship refers to (App Service Plan,
        Key Vault, Storage Account, VNet, etc.) that Resource Graph did not return - e.g. it's
        outside the query's scope, in another subscription, or was deleted. Keeps the
        relationship visible on the graph instead of the edge (and the resources it connects)
        disappearing entirely."""
        name = raw_id.rstrip("/").split("/")[-1] or raw_id
        return {
            "id": raw_id,
            "resource_id": raw_id,
            "name": name,
            "type": "unknown",
            "resource_type": "unknown",
            "resource_group": "",
            "region": "",
            "location": "",
            "subscription_id": "",
            "subscription": "",
            "tags": {},
            "provisioning_state": "Unavailable",
            "state": "Unavailable",
            "health_status": "Unknown",
            "placeholder": True,
        }

    @staticmethod
    def _validate_edges_against_nodes(
        nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Last line of defense before rendering: drop any edge whose source or target isn't
        actually present in `nodes`, logging what was skipped so missing Azure relationships
        can be investigated. Cytoscape.js must never receive an edge referencing a
        non-existent node."""
        existing_node_ids = {_node_key(node) for node in nodes if _node_key(node)}

        safe_edges = []
        skipped = []
        for edge in edges:
            source_key = (edge.get("source") or "").strip().lower()
            target_key = (edge.get("target") or "").strip().lower()
            if source_key in existing_node_ids and target_key in existing_node_ids:
                safe_edges.append(edge)
            else:
                skipped.append(edge)

        if skipped:
            logger.warning(
                "Topology builder: dropped %d edge(s) referencing a node missing from the final "
                "graph (this should be rare - placeholders should have covered it): %s",
                len(skipped),
                [(e.get("source"), e.get("target"), e.get("relationship")) for e in skipped],
            )

        return {"nodes": nodes, "edges": safe_edges}

    def get_connection_error(self) -> Optional[str]:
        """User-friendly message if Azure couldn't be reached, else None.

        Only meaningful after a topology-loading call (e.g. get_full_graph)
        has actually attempted to fetch live data.
        """
        return self.azure_provider.get_connection_error()
    
    def resolve_topology_id(self, resource_id: str) -> Optional[str]:
        """Resolve any resource ID format to the topology node ID."""
        normalized = normalize_resource_id(resource_id)
        if not normalized:
            return None

        topology = self.load_topology_data()
        for node in topology.get("nodes", []):
            node_id = node.get("id") or node.get("resource_id", "")
            if node_id == normalized or node.get("name") == normalized:
                return node_id
        return normalized

    def compute_node_positions(
        self, center_id: str, node_ids: List[str], radius: float = 280
    ) -> Dict[str, Tuple[float, float]]:
        """Place the center node at origin and connected nodes in a circle."""
        positions: Dict[str, Tuple[float, float]] = {center_id: (0, 0)}
        others = [node_id for node_id in node_ids if node_id != center_id]
        count = len(others)

        for index, node_id in enumerate(others):
            angle = (2 * math.pi * index / count) - (math.pi / 2) if count else 0
            positions[node_id] = (
                radius * math.cos(angle),
                radius * math.sin(angle),
            )

        return positions

    def build_resource_graph(self, resource_id: str) -> Dict[str, Any]:
        """Build a context-based graph centered on a specific resource with depth 1
        
        Args:
            resource_id: ID of the resource to center the graph on
            
        Returns:
            Dictionary containing nodes, edges, and center_id for the context graph
        """
        topology = self.load_topology_data()
        all_nodes = topology.get("nodes", [])
        all_edges = topology.get("edges", [])

        center_id = self.resolve_topology_id(resource_id)
        if not center_id:
            return {"nodes": [], "edges": [], "center_id": None, "positions": {}}

        center_node = None
        for node in all_nodes:
            node_id = node.get("id") or node.get("resource_id", "")
            if node_id == center_id:
                center_node = node
                break

        if not center_node:
            return {"nodes": [], "edges": [], "center_id": None, "positions": {}}

        connected_node_ids = self.get_connected_nodes(center_id)
        included_node_ids = {center_id, *connected_node_ids}
        included_node_keys = {node_id.lower() for node_id in included_node_ids if node_id}
        included_edges = self.build_edges(center_id)
        graph_nodes = [node for node in all_nodes if _node_key(node) in included_node_keys]
        positions = self.compute_node_positions(center_id, list(included_node_ids))

        # Validation before rendering: never hand Cytoscape an edge whose source/target isn't
        # one of the nodes actually included in this depth-1 subgraph.
        existing_nodes = {_node_key(node) for node in graph_nodes}
        safe_edges = [
            edge for edge in included_edges
            if (edge.get("source") or "").strip().lower() in existing_nodes
            and (edge.get("target") or "").strip().lower() in existing_nodes
        ]
        if len(safe_edges) != len(included_edges):
            dropped = [e for e in included_edges if e not in safe_edges]
            logger.warning(
                "Resource graph for %s: dropped %d edge(s) referencing a node outside this "
                "depth-1 subgraph: %s",
                center_id, len(dropped),
                [(e.get("source"), e.get("target"), e.get("relationship")) for e in dropped],
            )

        return {
            "nodes": graph_nodes,
            "edges": safe_edges,
            "center_id": center_id,
            "positions": positions,
        }
    
    def get_connected_nodes(self, resource_id: str) -> List[str]:
        """Get direct dependencies (parents and children) for a resource."""
        topology_id = self.resolve_topology_id(resource_id) or resource_id
        topology = self.load_topology_data()
        all_edges = topology.get("edges", [])

        connected_ids = set()
        for edge in all_edges:
            source = edge.get("source")
            target = edge.get("target")
            if source == topology_id:
                connected_ids.add(target)
            elif target == topology_id:
                connected_ids.add(source)

        return list(connected_ids)
    
    def build_edges(self, resource_id: str) -> List[Dict[str, Any]]:
        """Build edges for direct relationships of a resource."""
        topology_id = self.resolve_topology_id(resource_id) or resource_id
        topology = self.load_topology_data()
        all_edges = topology.get("edges", [])

        return [
            edge for edge in all_edges
            if edge.get("source") == topology_id or edge.get("target") == topology_id
        ]
    
    def get_full_graph(self) -> Dict[str, Any]:
        """Get the complete infrastructure graph (legacy method, not recommended for context navigation)"""
        topology = self.load_topology_data()
        return {
            "nodes": topology.get("nodes", []),
            "edges": topology.get("edges", []),
            "center_id": None
        }
    
    def format_node_for_display(self, node: Dict[str, Any], is_selected: bool = False) -> Dict[str, Any]:
        """Format a node for display in the graph
        
        Args:
            node: Node data dictionary
            is_selected: Whether this node is the currently selected resource
            
        Returns:
            Formatted node dictionary for display
        """
        node_id = node.get("id") or node.get("resource_id", "")
        node_type = node.get("type", "unknown")
        node_name = node.get("name", "Unknown")
        health_status = node.get("health_status", "Unknown")

        # Determine color based on resource type and selection
        if is_selected:
            color = "#FF6B6B"  # Red for selected/centered resource
            size = self._get_node_size(node_type) + 5
        else:
            color = self._get_node_color(node_type)
            size = self._get_node_size(node_type)

        # Determine icon based on resource type
        icon = self._get_node_icon(node_type)
        health_indicator = self._get_health_indicator(health_status)

        # Icon always shown; health indicator only added when something needs attention,
        # so healthy nodes stay clean and problem nodes stand out.
        label = f"{icon} {node_name}"
        if health_indicator:
            label = f"{label} {health_indicator}"

        return {
            "id": node_id,
            "label": label,
            "type": node_type,
            "health_status": health_status,
            "health_indicator": health_indicator,
            "color": color,
            "icon": icon,
            "size": size,
            "is_selected": is_selected,
            "data": node
        }
    
    def format_edge_for_display(self, edge: Dict[str, Any]) -> Dict[str, Any]:
        """Format an edge for display in the graph
        
        Args:
            edge: Edge data dictionary
            
        Returns:
            Formatted edge dictionary for display
        """
        source = edge.get("source", "")
        target = edge.get("target", "")
        relationship = edge.get("relationship", "connected")

        # Determine line style based on relationship type
        line_style = self._get_edge_style(relationship)

        return {
            "source": source,
            "target": target,
            "label": self._get_edge_label(relationship),
            "type": relationship,
            "style": line_style,
            "data": edge
        }
    
    def _get_node_color(self, node_type: str) -> str:
        """Get color for a node based on its type"""
        color_map = {
            "subscription": "#2E86AB",
            "resource_group": "#A23B72",
            "aks_cluster": "#F18F01",
            "app_service": "#C73E1D",
            "deployment": "#3B1F2B",
            "sql_database": "#06A77D",
            "redis": "#59C3C3",
            "storage_account": "#6C757D",
            "key_vault": "#991B1B",
            "application_insights": "#7B2D8E",
            "log_analytics": "#1F77B4",
            "gitlab_project": "#FC6D26",
            "pod": "#28A745",
            "service": "#17A2B8",
            "ingress": "#6610F2"
        }
        return color_map.get(node_type, "#95A5A6")
    
    def _get_node_icon(self, node_type: str) -> str:
        """Get icon for a node based on its type"""
        icon_map = {
            "subscription": "🏢",
            "resource_group": "📁",
            "aks_cluster": "☸️",
            "app_service": "🌐",
            "deployment": "🚀",
            "sql_database": "🗄️",
            "redis": "⚡",
            "storage_account": "💾",
            "key_vault": "🔐",
            "application_insights": "📊",
            "log_analytics": "📋",
            "gitlab_project": "🦊",
            "pod": "📦",
            "service": "🔗",
            "ingress": "🌍"
        }
        return icon_map.get(node_type, "📄")
    
    def _get_node_size(self, node_type: str) -> int:
        """Get size for a node based on its type"""
        size_map = {
            "subscription": 30,
            "resource_group": 25,
            "aks_cluster": 25,
            "app_service": 20,
            "deployment": 18,
            "sql_database": 20,
            "redis": 18,
            "storage_account": 20,
            "key_vault": 18,
            "application_insights": 18,
            "log_analytics": 18,
            "gitlab_project": 18,
            "pod": 15,
            "service": 15,
            "ingress": 15
        }
        return size_map.get(node_type, 15)
    
    def _get_edge_style(self, relationship: str) -> str:
        """Get line style for an edge based on relationship type"""
        style_map = {
            "contains": "solid",
            "hosts": "solid",
            "connects_to": "dashed",
            "monitored_by": "dotted",
            "deploys_to": "solid",
            "code_from": "dashed",
            "reads_secrets_from": "dotted",
            "uses": "dashed"
        }
        return style_map.get(relationship, "solid")

    def _get_health_indicator(self, health_status: str) -> str:
        """Get an emoji flag for a node's health, empty when everything's fine
        so only resources that need attention stand out on the graph"""
        status_lower = (health_status or "").lower()
        if status_lower in ["critical", "error", "failed", "stopped", "offline"]:
            return "🔴"
        elif status_lower in ["warning", "degraded", "unhealthy"]:
            return "🟡"
        return ""

    def _get_edge_label(self, relationship: str) -> str:
        """Get a human-readable label for a technical relationship key"""
        label_map = {
            "contains": "Contains",
            "hosts": "Hosts",
            "connects_to": "Connects to",
            "monitored_by": "Monitored by",
            "logs_to": "Sends logs to",
            "deploys_to": "Deploys to",
            "code_from": "Built from",
            "reads_secrets_from": "Reads secrets from",
            "uses": "Uses"
        }
        return label_map.get(relationship, relationship.replace("_", " ").capitalize())
    
    def get_neighbors(self, resource_id: str) -> List[Dict[str, Any]]:
        """Get neighboring resources for a given resource (legacy method)"""
        topology = self.load_topology_data()
        all_edges = topology.get("edges", [])
        
        neighbors = set()
        for edge in all_edges:
            source = edge.get("source")
            target = edge.get("target")
            
            if source == resource_id:
                neighbors.add(target)
            elif target == resource_id:
                neighbors.add(source)
        
        # Get neighbor node data
        all_nodes = topology.get("nodes", [])
        neighbor_nodes = []
        for node in all_nodes:
            node_id = node.get("id") or node.get("resource_id")
            if node_id in neighbors:
                neighbor_nodes.append(node)
        
        return neighbor_nodes
    
    def get_shortest_path(self, source_id: str, target_id: str) -> List[str]:
        """Get the shortest path between two resources using BFS (legacy method)"""
        topology = self.load_topology_data()
        all_edges = topology.get("edges", [])
        
        # Build adjacency list
        adjacency = {}
        for edge in all_edges:
            source = edge.get("source")
            target = edge.get("target")
            
            if source not in adjacency:
                adjacency[source] = []
            if target not in adjacency:
                adjacency[target] = []
            
            adjacency[source].append(target)
            adjacency[target].append(source)
        
        # BFS to find shortest path
        from collections import deque
        
        queue = deque([(source_id, [source_id])])
        visited = {source_id}
        
        while queue:
            current, path = queue.popleft()
            
            if current == target_id:
                return path
            
            if current in adjacency:
                for neighbor in adjacency[current]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, path + [neighbor]))
        
        return []  # No path found
