import streamlit as st
from typing import Any, Dict, List, Optional
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


# (sidebar label, ResourceService.get_azure_resources() category key, resource_type tag used
# for the selection button key / AI side panel). Resource Groups and AKS Clusters are rendered
# separately above this list since they come from different provider calls, not
# _CATEGORY_ARM_TYPES. "Other Resources" is the catch-all for every real ARM type that isn't
# one of the named categories below (see _CATEGORY_ARM_TYPES/_NOISE_ARM_TYPES in resource_service.py).
_SIDEBAR_CATEGORIES = [
    ("App Services", "app_services", "app_service"),
    ("SQL Databases", "sql_databases", "sql_database"),
    ("PostgreSQL Servers", "postgresql_servers", "postgresql_server"),
    ("Redis", "redis_caches", "redis"),
    ("Storage Accounts", "storage_accounts", "storage_account"),
    ("Key Vaults", "key_vaults", "key_vault"),
    ("Application Insights", "application_insights", "application_insights"),
    ("Log Analytics", "log_analytics", "log_analytics"),
    ("Virtual Machines", "virtual_machines", "virtual_machine"),
    ("VM Scale Sets", "vm_scale_sets", "vm_scale_set"),
    ("Managed Disks", "managed_disks", "managed_disk"),
    ("Container Registries", "container_registries", "container_registry"),
    ("Container Apps", "container_apps", "container_app"),
    ("Container Instances", "container_instances", "container_instance"),
    ("Cosmos DB Accounts", "cosmos_db_accounts", "cosmos_db_account"),
    ("Cognitive Services", "cognitive_services", "cognitive_service"),
    ("App Service Plans", "app_service_plans", "app_service_plan"),
    ("Virtual Networks", "virtual_networks", "vnet"),
    ("Network Security Groups", "network_security_groups", "nsg"),
    ("Load Balancers", "load_balancers", "load_balancer"),
    ("Application Gateways", "application_gateways", "application_gateway"),
    ("Public IP Addresses", "public_ip_addresses", "public_ip"),
    ("Other Resources", "other_resources", "other_resource"),
]


def render_sidebar() -> Optional[str]:
    """Render the Azure Subscription Explorer sidebar content

    This function should be called within a st.sidebar context.

    Returns:
        ID of the selected resource, or None if no selection
    """
    st.markdown("### Azure Subscription Explorer")

    # Import resource service
    from services.resource_service import ResourceService

    # Initialize resource service
    if 'resource_service' not in st.session_state:
        st.session_state.resource_service = ResourceService()

    resource_service = st.session_state.resource_service

    # Get all Azure resources
    try:
        azure_resources = resource_service.get_azure_resources()
    except Exception as e:
        st.error(f"Error loading resources: {str(e)}")
        return None

    # Search functionality
    search_query = st.text_input("🔍 Search resources...", placeholder="Type to search...")

    # Resource Groups
    with st.expander("Resource Groups", expanded=True):
        _render_category(azure_resources.get("resource_groups", []), "resource_group", search_query)

    # AKS Clusters
    with st.expander("AKS Clusters"):
        _render_category(azure_resources.get("aks_clusters", []), "aks_cluster", search_query)

    for label, category_key, resource_type in _SIDEBAR_CATEGORIES:
        with st.expander(label):
            _render_category(azure_resources.get(category_key, []), resource_type, search_query)

    # Return currently selected resource ID
    return st.session_state.get('selected_resource_id')


def _render_category(items: List[Dict[str, Any]], resource_type: str, search_query: str) -> None:
    """Filter a category's resources by the search box, then render each as a selectable button."""
    if search_query:
        items = [item for item in items if search_query.lower() in item.get("name", "").lower()]

    for item in items:
        _render_selectable_resource(item, resource_type)


def _render_selectable_resource(resource: Dict[str, Any], resource_type: str) -> None:
    """Render a selectable resource with health indicator

    Args:
        resource: Resource data dictionary
        resource_type: Type of the resource
    """
    resource_id = resource.get("id", resource.get("resource_id", resource.get("name", "")))
    resource_name = resource.get("name", "Unknown")
    health_status = resource.get("health_status", "Unknown")

    # Get health indicator
    health_indicator = _get_health_indicator(health_status)

    # Check if this is the selected resource
    is_selected = st.session_state.get('selected_resource_id') == resource_id

    # Create button label
    label = f"{health_indicator} {resource_name}"
    if is_selected:
        label = f"👉 {label}"

    # Create unique key for button
    button_key = f"select_{resource_type}_{resource_id}"

    # Render button
    if st.button(label, key=button_key, use_container_width=True):
        # Store previous resource for navigation
        current_selection = st.session_state.get('selected_resource_id')
        if current_selection and current_selection != resource_id:
            st.session_state.previous_resource_id = current_selection

        st.session_state.selected_resource_id = resource_id
        st.session_state.navigation_history = st.session_state.get('navigation_history', [])
        st.session_state.navigation_history.append(resource_id)
        st.session_state.history_index = len(st.session_state.navigation_history) - 1
        st.rerun()


def _get_health_indicator(health_status: str) -> str:
    """Get emoji indicator for health status"""
    status_lower = health_status.lower()
    if status_lower in ['healthy', 'running', 'active', 'online']:
        return "🟢"
    elif status_lower in ['warning', 'degraded', 'unhealthy']:
        return "🟡"
    elif status_lower in ['critical', 'error', 'failed', 'stopped', 'offline']:
        return "🔴"
    else:
        return "⚪"
