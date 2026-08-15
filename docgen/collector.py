"""Evidence collection for the documentation generator (Task 24).

Every fact gathered here comes from a method the app already exposes on
ResourceService/GitLabClient (live Azure/GitLab/AKS/Monitoring/Cost data) - this module
only shapes that data into the domain-keyed evidence bundle
(`gitlab` / `azure_infrastructure` / `aks` / `observability` / `finops`) that the
*unmodified* CrewAI InvestigationCrew (agents/crew/manager.py) already knows how to
consume, exactly the same shape agents/orchestrator.py produces for a normal
investigation. No new investigation/analysis logic is added here - only data shaping.
"""
from typing import Any, Callable, Dict, List, Optional

from docgen.discovery import ProjectContext
from services.resource_service import ResourceService

_AKS_TYPE = "microsoft.containerservice/managedclusters"
_APP_SERVICE_TYPE = "microsoft.web/sites"
_LOG_ANALYTICS_TYPE = "microsoft.operationalinsights/workspaces"
_NETWORKING_TYPES = {
    "microsoft.network/virtualnetworks",
    "microsoft.network/networksecuritygroups",
    "microsoft.network/loadbalancers",
    "microsoft.network/publicipaddresses",
}
_MAX_OBSERVED_RESOURCES = 5


def _sku_name(properties: Dict[str, Any]) -> Optional[str]:
    sku = properties.get("sku") or {}
    return sku.get("name") if isinstance(sku, dict) else None


_TYPE_DETAIL_EXTRACTORS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "microsoft.web/serverfarms": lambda p: {"sku": _sku_name(p), "kind": p.get("kind")},
    "microsoft.web/sites": lambda p: {
        "runtime": (p.get("siteConfig") or {}).get("linuxFxVersion") or (p.get("siteConfig") or {}).get("windowsFxVersion"),
        "https_only": p.get("httpsOnly"),
        "default_host_name": p.get("defaultHostName"),
        "state": p.get("state"),
    },
    "microsoft.containerregistry/registries": lambda p: {
        "sku": _sku_name(p), "login_server": p.get("loginServer"), "admin_user_enabled": p.get("adminUserEnabled"),
    },
    "microsoft.keyvault/vaults": lambda p: {
        "sku": (p.get("sku") or {}).get("name"), "access_policy_count": len(p.get("accessPolicies") or []),
    },
    "microsoft.storage/storageaccounts": lambda p: {
        "sku": _sku_name(p), "kind": p.get("kind"), "access_tier": p.get("accessTier"),
    },
    "microsoft.sql/servers": lambda p: {"admin_login": p.get("administratorLogin"), "version": p.get("version")},
    "microsoft.sql/servers/databases": lambda p: {
        "sku": _sku_name(p),
        "status": p.get("status"),
        "max_size_gb": round((p.get("maxSizeBytes") or 0) / (1024 ** 3), 1) if p.get("maxSizeBytes") else None,
    },
    "microsoft.cache/redis": lambda p: {
        "sku": (p.get("sku") or {}).get("name"), "capacity": (p.get("sku") or {}).get("capacity"),
    },
    "microsoft.insights/components": lambda p: {
        "application_type": p.get("Application_Type"), "instrumentation_configured": bool(p.get("InstrumentationKey")),
    },
    "microsoft.operationalinsights/workspaces": lambda p: {
        "retention_days": p.get("retentionInDays"), "sku": (p.get("sku") or {}).get("name"),
    },
    "microsoft.network/virtualnetworks": lambda p: {"address_space": (p.get("addressSpace") or {}).get("addressPrefixes")},
    "microsoft.network/networksecuritygroups": lambda p: {"security_rule_count": len(p.get("securityRules") or [])},
    "microsoft.network/loadbalancers": lambda p: {
        "frontend_count": len(p.get("frontendIPConfigurations") or []),
        "backend_pool_count": len(p.get("backendAddressPools") or []),
    },
    "microsoft.network/publicipaddresses": lambda p: {
        "ip_address": p.get("ipAddress"), "allocation_method": p.get("publicIPAllocationMethod"),
    },
    "microsoft.containerservice/managedclusters": lambda p: {
        "kubernetes_version": p.get("kubernetesVersion"), "dns_prefix": p.get("dnsPrefix"),
    },
}


def _summarize_azure_resource(resource: Dict[str, Any]) -> Dict[str, Any]:
    """A curated, display-safe summary of one Azure resource - never the raw `_properties`
    blob, and never anything from Key Vault beyond metadata (no secret values are held in
    Resource Graph properties in the first place)."""
    resource_type = (resource.get("type") or "").lower()
    properties = resource.get("_properties") or {}
    identity = resource.get("_identity") or {}
    extractor = _TYPE_DETAIL_EXTRACTORS.get(resource_type)

    return {
        "name": resource.get("name"),
        "type": resource.get("type"),
        "resource_group": resource.get("resource_group"),
        "region": resource.get("region"),
        "tags": resource.get("tags") or {},
        "provisioning_state": resource.get("provisioning_state"),
        "managed_identity": identity.get("type") if (identity or {}).get("type") not in (None, "None") else None,
        "details": extractor(properties) if extractor else {},
    }


def _mode(values: List[Optional[str]]) -> Optional[str]:
    present = [v for v in values if v]
    return max(set(present), key=present.count) if present else None


class DocumentationCollector:
    """Gathers all documentation evidence for a resolved project."""

    def __init__(self, resource_service: Optional[ResourceService] = None):
        self.resource_service = resource_service or ResourceService()

    def _collect_gitlab(self, context: ProjectContext) -> Dict[str, Any]:
        project = context.project
        if not project:
            return {"agent": "gitlab_devops_source", "found": False}

        rs = self.resource_service
        project_id = project.get("id")
        default_branch = project.get("default_branch")

        repository_profile = rs.get_repository_profile(project_id, ref=default_branch)
        pipelines = rs.get_project_pipelines(project_id) or []
        latest_pipeline = pipelines[0] if pipelines else None

        stages: List[Dict[str, Any]] = []
        jobs: List[Dict[str, Any]] = []
        if latest_pipeline and latest_pipeline.get("id"):
            stages = rs.get_pipeline_stages(project_id, latest_pipeline["id"])
            jobs = rs.get_pipeline_jobs(project_id, latest_pipeline["id"])

        environments = rs.get_project_environments(project_id)
        merge_requests = rs.get_project_merge_requests(project_id)
        recent_commits = rs.get_project_recent_commits(project_id, ref=default_branch)

        has_rollback_stage = any(
            "rollback" in (s.get("name") or "").lower() or "revert" in (s.get("name") or "").lower()
            for s in stages
        )
        deployment_flow = {
            "trigger": f"push/merge to `{default_branch or 'the default branch'}`",
            "latest_pipeline_status": (latest_pipeline or {}).get("status"),
            "stages": [s.get("name") for s in stages],
            "environments": [e.get("name") for e in environments],
            "rollback_process": (
                "A rollback/revert-named stage or job was found in the pipeline - see CI/CD Pipeline stages."
                if has_rollback_stage else
                "No rollback/revert stage or job was found in the pipeline; an automated rollback "
                "process could not be confirmed from live data."
            ),
        }

        return {
            "agent": "gitlab_devops_source",
            "found": True,
            "project": project,
            "repository_url": project.get("http_url_to_repo") or project.get("web_url"),
            "default_branch": default_branch,
            "repository_profile": repository_profile,
            "latest_pipeline": latest_pipeline,
            "pipeline_stages": stages,
            "pipeline_jobs": jobs,
            "environments": environments,
            "deployment_flow": deployment_flow,
            "merge_requests": merge_requests,
            "recent_commits": recent_commits,
        }

    def _collect_azure(self, context: ProjectContext) -> Dict[str, Any]:
        if not context.azure_resources:
            return {"agent": "azure_infrastructure_source", "found": False}

        summaries = [_summarize_azure_resource(r) for r in context.azure_resources]
        return {
            "agent": "azure_infrastructure_source",
            "found": True,
            "match_method": context.azure_match_method,
            "subscription": _mode([r.get("subscription") for r in context.azure_resources]),
            "resource_groups": sorted({r.get("resource_group") for r in context.azure_resources if r.get("resource_group")}),
            "regions": sorted({r.get("region") for r in context.azure_resources if r.get("region")}),
            "resources": summaries,
            "networking": [s for s in summaries if (s["type"] or "").lower() in _NETWORKING_TYPES],
            "managed_identities": [s for s in summaries if s.get("managed_identity")],
        }

    def _collect_aks(self, context: ProjectContext) -> Dict[str, Any]:
        cluster = context.aks_cluster
        if not cluster:
            return {"agent": "aks_source", "found": False}

        cluster_id = cluster.get("id") or cluster.get("resource_id")
        namespace = context.aks_namespace
        rs = self.resource_service

        return {
            "agent": "aks_source",
            "found": True,
            "cluster": cluster,
            "namespace": namespace,
            "namespace_scope_note": (
                f"Scoped to namespace '{namespace}'." if namespace
                else "Namespace could not be confidently identified; data below is cluster-wide."
            ),
            "node_pools": cluster.get("node_pools", []),
            "deployments": rs.get_cluster_deployments(cluster_id, namespace),
            "services": rs.get_cluster_services(cluster_id, namespace),
            "ingress": rs.get_cluster_ingress(cluster_id, namespace),
            "configmaps": rs.get_cluster_configmaps(cluster_id, namespace),
            "secrets": rs.get_cluster_secrets(cluster_id, namespace),
        }

    def _collect_observability(self, context: ProjectContext) -> Dict[str, Any]:
        targets = [r for r in context.azure_resources if (r.get("type") or "").lower() in (_APP_SERVICE_TYPE, _AKS_TYPE)]
        if context.aks_cluster and context.aks_cluster not in targets:
            targets.append(context.aks_cluster)
        if not targets:
            return {"agent": "observability_source", "found": False}

        rs = self.resource_service
        per_resource = []
        for resource in targets[:_MAX_OBSERVED_RESOURCES]:
            resource_id = resource.get("id") or resource.get("resource_id")
            per_resource.append({
                "resource_name": resource.get("name"),
                "alerts": rs.get_resource_alerts(resource_id),
                "health": rs.get_resource_health(resource_id),
            })

        log_analytics = next(
            (r for r in context.azure_resources if (r.get("type") or "").lower() == _LOG_ANALYTICS_TYPE), None
        )
        logging_configuration = None
        if log_analytics:
            properties = log_analytics.get("_properties") or {}
            logging_configuration = {
                "workspace_name": log_analytics.get("name"),
                "retention_days": properties.get("retentionInDays"),
            }

        return {
            "agent": "observability_source",
            "found": True,
            "per_resource": per_resource,
            "logging_configuration": logging_configuration,
        }

    def _collect_finops(self, context: ProjectContext) -> Dict[str, Any]:
        if not context.azure_resources:
            return {"agent": "finops_source", "found": False}

        rs = self.resource_service
        breakdown = []
        total = 0.0
        currency = None
        for resource in context.azure_resources:
            resource_id = resource.get("id") or resource.get("resource_id")
            cost = rs.get_resource_cost(resource_id)
            if not cost:
                continue
            amount = cost.get("monthly_cost")
            if amount is not None:
                total += float(amount)
                currency = currency or cost.get("currency")
            breakdown.append({"resource_name": resource.get("name"), **cost})

        if not breakdown:
            return {"agent": "finops_source", "found": False}

        return {
            "agent": "finops_source",
            "found": True,
            "total_monthly_cost": round(total, 2),
            "currency": currency,
            "breakdown_by_resource": breakdown,
        }

    def collect(self, context: ProjectContext) -> Dict[str, Any]:
        """Gather all documentation evidence, keyed exactly like the orchestrator's capability
        results (`gitlab`, `azure_infrastructure`, `aks`, `observability`, `finops`) so it can
        be handed straight to the unmodified InvestigationCrew (agents/crew/manager.py)."""
        return {
            "gitlab": self._collect_gitlab(context),
            "azure_infrastructure": self._collect_azure(context),
            "aks": self._collect_aks(context),
            "observability": self._collect_observability(context),
            "finops": self._collect_finops(context),
        }
