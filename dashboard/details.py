import streamlit as st
from typing import Any, Dict, Optional
from datetime import datetime


def render_resource_details(resource_data: Optional[Dict[str, Any]] = None) -> None:
    """Render resource details panel
    
    Args:
        resource_data: Dictionary containing resource information
    """
    st.markdown("### Resource Details")
    
    if not resource_data:
        st.markdown("*No resource selected.*")
        st.markdown("Select a resource from the sidebar or click on a node in the topology graph.")
        return
    
    # Basic Information
    with st.expander("Basic Information", expanded=True):
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Name:**")
            st.markdown(f"_{resource_data.get('name', 'Unknown')}_")
            
            st.markdown("**Resource Type:**")
            st.markdown(f"_{resource_data.get('resource_type', resource_data.get('type', 'Unknown'))}_")
            
            st.markdown("**Resource ID:**")
            st.markdown(f"`{resource_data.get('id', resource_data.get('resource_id', 'Unknown'))}`")
        
        with col2:
            st.markdown("**Subscription:**")
            st.markdown(f"_{resource_data.get('subscription', 'Unknown')}_")
            
            st.markdown("**Resource Group:**")
            st.markdown(f"_{resource_data.get('resource_group', 'Unknown')}_")
            
            st.markdown("**Region:**")
            st.markdown(f"_{resource_data.get('region', 'Unknown')}_")
    
    # Status and Health
    with st.expander("Status and Health", expanded=True):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            status = resource_data.get('state', resource_data.get('health_status', 'Unknown'))
            status_color = _get_status_color(status)
            st.markdown(f"**Status:**")
            st.markdown(f":{status_color}[{status}]")
        
        with col2:
            environment = resource_data.get('environment', 'Unknown')
            st.markdown(f"**Environment:**")
            st.markdown(f"_{environment}_")
        
        with col3:
            owner = resource_data.get('owner', 'Unknown')
            st.markdown(f"**Owner:**")
            st.markdown(f"_{owner}_")
    
    # Tags
    tags = resource_data.get('tags', {})
    if tags:
        with st.expander("Tags"):
            for key, value in tags.items():
                st.markdown(f"**{key}:** {value}")
    
    # Connected Resources
    connected_resources = resource_data.get('connected_resources', [])
    if connected_resources:
        with st.expander(f"Connected Resources ({len(connected_resources)})"):
            for i, resource in enumerate(connected_resources, 1):
                resource_name = resource.get('name', 'Unknown')
                resource_type = resource.get('resource_type', resource.get('type', 'Unknown'))
                resource_id = resource.get('id', resource.get('resource_id', ''))
                
                st.markdown(f"{i}. **{resource_name}** ({resource_type})")
                if resource_id:
                    st.markdown(f"   `{resource_id}`")
    
    # Additional Information based on resource type
    _render_resource_specific_details(resource_data)
    
    # Timestamps
    with st.expander("Timestamps"):
        created_at = resource_data.get('created_at', 'Unknown')
        if created_at != 'Unknown':
            try:
                # Try to parse and format the date
                if isinstance(created_at, str):
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    formatted_date = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                    st.markdown(f"**Created:** {formatted_date}")
                else:
                    st.markdown(f"**Created:** {created_at}")
            except:
                st.markdown(f"**Created:** {created_at}")
        else:
            st.markdown("**Created:** Unknown")
        
        last_updated = resource_data.get('last_updated', resource_data.get('last_modified', 'Unknown'))
        if last_updated != 'Unknown':
            try:
                if isinstance(last_updated, str):
                    dt = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
                    formatted_date = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                    st.markdown(f"**Last Updated:** {formatted_date}")
                else:
                    st.markdown(f"**Last Updated:** {last_updated}")
            except:
                st.markdown(f"**Last Updated:** {last_updated}")
        else:
            st.markdown("**Last Updated:** Unknown")


def _get_status_color(status: str) -> str:
    """Get color for status display"""
    status_lower = status.lower()
    if status_lower in ['healthy', 'running', 'active', 'online', 'success']:
        return "green"
    elif status_lower in ['warning', 'degraded', 'unhealthy']:
        return "orange"
    elif status_lower in ['critical', 'error', 'failed', 'stopped', 'offline']:
        return "red"
    else:
        return "blue"


def _render_resource_specific_details(resource_data: Dict[str, Any]) -> None:
    """Render details specific to certain resource types"""
    resource_type = resource_data.get('resource_type', resource_data.get('type', ''))
    
    # AKS Cluster specific details
    if 'aks' in resource_type.lower() or resource_type == 'aks_cluster':
        with st.expander("Cluster Details"):
            st.markdown(f"**Kubernetes Version:** {resource_data.get('kubernetes_version', 'Unknown')}")
            st.markdown(f"**Node Count:** {resource_data.get('node_count', 'Unknown')}")
            
            node_pools = resource_data.get('node_pools', [])
            if node_pools:
                st.markdown("**Node Pools:**")
                for pool in node_pools:
                    pool_name = pool.get('name', 'Unknown')
                    pool_count = pool.get('count', 0)
                    pool_size = pool.get('vm_size', 'Unknown')
                    st.markdown(f"- {pool_name}: {pool_count} nodes ({pool_size})")
    
    # Deployment specific details
    elif resource_type == 'deployment':
        with st.expander("Deployment Details"):
            st.markdown(f"**Replicas:** {resource_data.get('replicas', 0)}")
            st.markdown(f"**Available Replicas:** {resource_data.get('available_replicas', 0)}")
            st.markdown(f"**Image:** `{resource_data.get('image', 'Unknown')}`")
            st.markdown(f"**Namespace:** {resource_data.get('namespace', 'Unknown')}")
    
    # Pod specific details
    elif resource_type == 'pod':
        with st.expander("Pod Details"):
            st.markdown(f"**Phase:** {resource_data.get('phase', 'Unknown')}")
            st.markdown(f"**Restart Count:** {resource_data.get('restart_count', 0)}")
            st.markdown(f"**Node:** {resource_data.get('node', 'Unknown')}")
            st.markdown(f"**Namespace:** {resource_data.get('namespace', 'Unknown')}")
    
    # Database specific details
    elif 'sql' in resource_type.lower() or 'database' in resource_type.lower():
        with st.expander("Database Details"):
            st.markdown(f"**SKU:** {resource_data.get('sku', 'Unknown')}")
            st.markdown(f"**Server:** {resource_data.get('server', 'Unknown')}")
    
    # App Service specific details
    elif 'app service' in resource_type.lower() or resource_type == 'app_service':
        with st.expander("App Service Details"):
            st.markdown(f"**SKU:** {resource_data.get('sku', 'Unknown')}")
            st.markdown(f"**State:** {resource_data.get('state', 'Unknown')}")
            app_service_plan = resource_data.get('app_service_plan', 'Unknown')
            if app_service_plan != 'Unknown':
                st.markdown(f"**App Service Plan:** {app_service_plan}")
    
    # Storage Account specific details
    elif 'storage' in resource_type.lower():
        with st.expander("Storage Details"):
            st.markdown(f"**SKU:** {resource_data.get('sku', 'Unknown')}")
            st.markdown(f"**Access Tier:** {resource_data.get('access_tier', 'Unknown')}")
            st.markdown(f"**Kind:** {resource_data.get('kind', 'Unknown')}")
    
    # Redis specific details
    elif 'redis' in resource_type.lower():
        with st.expander("Redis Details"):
            st.markdown(f"**SKU:** {resource_data.get('sku', 'Unknown')}")
            st.markdown(f"**Capacity:** {resource_data.get('capacity', 'Unknown')} GB")
            st.markdown(f"**Port:** {resource_data.get('port', 'Unknown')}")
            st.markdown(f"**SSL Enabled:** {resource_data.get('ssl_enabled', False)}")
    
    # Key Vault specific details
    elif 'key vault' in resource_type.lower() or resource_type == 'key_vault':
        with st.expander("Key Vault Details"):
            st.markdown(f"**SKU:** {resource_data.get('sku', 'Unknown')}")
            st.markdown(f"**Soft Delete Enabled:** {resource_data.get('soft_delete_enabled', False)}")
            st.markdown(f"**Purge Protection Enabled:** {resource_data.get('purge_protection_enabled', False)}")
    
    # Application Insights specific details
    elif 'application insights' in resource_type.lower() or resource_type == 'application_insights':
        with st.expander("Application Insights Details"):
            st.markdown(f"**Application Type:** {resource_data.get('application_type', 'Unknown')}")
            st.markdown(f"**Instrumentation Key:** `{resource_data.get('instrumentation_key', 'Unknown')[:8]}...`")
    
    # Log Analytics specific details
    elif 'log analytics' in resource_type.lower() or resource_type == 'log_analytics':
        with st.expander("Log Analytics Details"):
            st.markdown(f"**SKU:** {resource_data.get('sku', 'Unknown')}")
            st.markdown(f"**Retention Days:** {resource_data.get('retention_days', 'Unknown')}")
            st.markdown(f"**Daily Data GB:** {resource_data.get('daily_data_gb', 'Unknown')}")
