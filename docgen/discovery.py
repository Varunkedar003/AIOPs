"""Project discovery for the documentation generator (Task 24).

Given a project name/hint, finds the GitLab project it refers to and correlates it with
Azure/AKS resources - live data only, fetched through the SAME ResourceService the rest of
the app already uses. This module only locates *which* resources describe a project;
evidence collection happens in docgen/collector.py and analysis happens in the existing,
unmodified CrewAI/Claude layers. No investigation logic is duplicated here.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from services.resource_service import ResourceService

_AKS_TYPE = "microsoft.containerservice/managedclusters"
_MIN_SLUG_LEN = 3
_SYSTEM_NAMESPACES = ("kube-system", "kube-public", "kube-node-lease", "default")


def _slugify(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


@dataclass
class ProjectContext:
    """Everything discovered about a documentation request's target project."""
    requested_name: str
    found: bool = False
    project: Optional[Dict[str, Any]] = None
    azure_resources: List[Dict[str, Any]] = field(default_factory=list)
    azure_match_method: Optional[str] = None  # "tag" | "name" | None
    aks_cluster: Optional[Dict[str, Any]] = None
    aks_namespace: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return (self.project or {}).get("name") or self.requested_name

    @property
    def slug(self) -> str:
        return _slugify(self.display_name)


class ProjectDiscovery:
    """Resolves a project name to its GitLab project and correlated Azure/AKS resources."""

    def __init__(self, resource_service: Optional[ResourceService] = None):
        self.resource_service = resource_service or ResourceService()

    def resolve_hint_from_resource(self, resource_id: Optional[str]) -> Optional[str]:
        """Best-effort project name for a currently-selected resource, used when the chat
        message names no project (e.g. "Generate deployment documentation"). Returns None if
        no resource is selected or nothing can be correlated - the caller should then ask the
        user which project they mean, never guess silently."""
        if not resource_id:
            return None

        resource = self.resource_service.get_resource_by_id(resource_id)
        if not resource:
            return None
        if (resource.get("type") or "").lower() == "gitlab_project":
            return resource.get("name")

        resource_slug = _slugify(f"{resource.get('name', '')} {resource.get('resource_group', '')}")
        if len(resource_slug) < _MIN_SLUG_LEN:
            return None

        for project in self.resource_service.get_gitlab_projects():
            project_slug = _slugify(project.get("name"))
            if len(project_slug) >= _MIN_SLUG_LEN and project_slug in resource_slug:
                return project.get("name")

        return None

    def _find_gitlab_project(self, hint: str) -> Optional[Dict[str, Any]]:
        projects = self.resource_service.get_gitlab_projects()
        hint_lower = hint.strip().lower()
        hint_slug = _slugify(hint)

        for project in projects:
            name = (project.get("name") or "").lower()
            path_segment = (project.get("path") or "").split("/")[-1].lower()
            if hint_lower in (name, path_segment):
                return project

        if len(hint_slug) >= _MIN_SLUG_LEN:
            for project in projects:
                name_slug = _slugify(project.get("name"))
                path_slug = _slugify((project.get("path") or "").split("/")[-1])
                if hint_slug in (name_slug, path_slug) or (name_slug and name_slug in hint_slug):
                    return project

        return None

    def _match_azure_resources(
        self, slug: str, project: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        if len(slug) < _MIN_SLUG_LEN:
            return [], None

        all_resources = self.resource_service.get_all_azure_resources_raw()
        project_name_lower = (project.get("name") or "").lower()

        tag_matches, name_matches = [], []
        for resource in all_resources:
            tags = resource.get("tags") or {}
            if any(str(v).strip().lower() == project_name_lower for v in tags.values() if v):
                tag_matches.append(resource)
                continue
            haystack = _slugify(f"{resource.get('name', '')} {resource.get('resource_group', '')}")
            if slug in haystack:
                name_matches.append(resource)

        if tag_matches:
            return tag_matches, "tag"
        if name_matches:
            return name_matches, "name"
        return [], None

    def _match_aks_namespace(self, cluster: Dict[str, Any], slug: str) -> Optional[str]:
        cluster_id = cluster.get("id") or cluster.get("resource_id")
        namespaces = self.resource_service.get_cluster_namespaces(cluster_id)
        candidates = [ns.get("name") for ns in namespaces if slug in _slugify(ns.get("name"))]
        if candidates:
            return candidates[0]

        non_system = [ns.get("name") for ns in namespaces if ns.get("name") not in _SYSTEM_NAMESPACES]
        return non_system[0] if len(non_system) == 1 else None

    def resolve(self, project_hint: str) -> ProjectContext:
        """Resolve a project name/hint to a ProjectContext. Never raises: an unresolvable
        hint or empty correlation degrades to `found=False`/empty lists plus an explicit
        note, consistent with how every other data-layer module in this app fails closed."""
        context = ProjectContext(requested_name=project_hint or "")
        if not project_hint:
            context.notes.append("No project name was given.")
            return context

        project = self._find_gitlab_project(project_hint)
        if not project:
            context.notes.append(
                f"No GitLab project matching '{project_hint}' was found among accessible projects."
            )
            return context

        context.found = True
        context.project = project

        azure_resources, method = self._match_azure_resources(context.slug, project)
        context.azure_resources = azure_resources
        context.azure_match_method = method
        if not azure_resources:
            context.notes.append(
                f"No Azure resources could be automatically correlated with project "
                f"'{project.get('name')}' (matched by neither resource tags nor "
                f"name/resource-group substring)."
            )

        aks_cluster = next(
            (r for r in azure_resources if (r.get("type") or "").lower() == _AKS_TYPE), None
        )
        if aks_cluster:
            context.aks_cluster = aks_cluster
            context.aks_namespace = self._match_aks_namespace(aks_cluster, context.slug)
            if not context.aks_namespace:
                context.notes.append(
                    "Could not confidently identify a single AKS namespace for this project; "
                    "Kubernetes details cover the whole cluster instead."
                )

        return context
