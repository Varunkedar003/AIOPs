"""GitLab Agent.

Specialist agent that reports pipelines, jobs, deployment status, commit
information, and merge requests for a GitLab project. Built entirely on
MockGitLabProvider - no direct JSON access happens here.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from agents.base import SpecialistAgent
from providers.gitlab_provider import MockGitLabProvider


@dataclass
class GitLabProjectReport:
    """Structured output of a GitLab Agent investigation"""
    agent: str = "gitlab"
    project_id: str = ""
    found: bool = False
    project: Dict[str, Any] = field(default_factory=dict)
    branches: List[Dict[str, Any]] = field(default_factory=list)
    pipelines: List[Dict[str, Any]] = field(default_factory=list)
    jobs: List[Dict[str, Any]] = field(default_factory=list)
    deployment_status: Dict[str, Any] = field(default_factory=dict)
    commit: Dict[str, Any] = field(default_factory=dict)
    merge_requests: List[Dict[str, Any]] = field(default_factory=list)


class GitLabAgent(SpecialistAgent):
    """Specialist agent covering GitLab pipelines, jobs, deployment status, commits, and merge requests"""

    name = "gitlab"

    def __init__(self, gitlab_provider: Optional[MockGitLabProvider] = None):
        self.gitlab_provider = gitlab_provider or MockGitLabProvider()

    def _find_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Resolve a project by ID or by name"""
        return self.gitlab_provider.get_project(project_id) or self.gitlab_provider.get_project_by_name(project_id)

    def analyze_branches(self, project_id: str) -> List[Dict[str, Any]]:
        """Analyze branches for a project"""
        return self.gitlab_provider.get_branches(project_id)

    def analyze_pipelines(self, project_id: str) -> List[Dict[str, Any]]:
        """Analyze pipelines for a project"""
        return self.gitlab_provider.get_pipelines(project_id)

    def analyze_pipeline_stages(self, project_id: str, pipeline_id: str) -> List[Dict[str, Any]]:
        """Analyze stage status rollups for a specific pipeline"""
        return self.gitlab_provider.get_pipeline_stages(project_id, pipeline_id)

    def analyze_jobs(self, project_id: str) -> List[Dict[str, Any]]:
        """Analyze jobs across all of a project's pipelines"""
        jobs: List[Dict[str, Any]] = []
        for pipeline in self.analyze_pipelines(project_id):
            pipeline_id = pipeline.get("id")
            if pipeline_id:
                jobs.extend(self.gitlab_provider.get_jobs(project_id, pipeline_id))
        return jobs

    def analyze_deployment_status(self, project_id: str) -> Dict[str, Any]:
        """Analyze the latest pipeline/deployment status for a project"""
        return self.gitlab_provider.get_pipeline_status(project_id)

    def analyze_commit(self, project_id: str) -> Dict[str, Any]:
        """Analyze the latest commit for a project"""
        return self.gitlab_provider.get_latest_commit(project_id) or {}

    def analyze_merge_requests(self, project_id: str) -> List[Dict[str, Any]]:
        """Analyze open/recent merge requests for a project"""
        return self.gitlab_provider.get_merge_requests(project_id)

    def investigate(self, project_id: str) -> GitLabProjectReport:
        """Build a structured report covering pipelines, jobs, deployment status, commit, and merge requests"""
        project = self._find_project(project_id)
        if not project:
            return GitLabProjectReport(project_id=project_id, found=False)

        return GitLabProjectReport(
            project_id=project_id,
            found=True,
            project=project,
            branches=self.analyze_branches(project_id),
            pipelines=self.analyze_pipelines(project_id),
            jobs=self.analyze_jobs(project_id),
            deployment_status=self.analyze_deployment_status(project_id),
            commit=self.analyze_commit(project_id),
            merge_requests=self.analyze_merge_requests(project_id),
        )

    def analyze_pipeline_failure(self, project_id: str, pipeline_id: Optional[str] = None) -> Dict[str, Any]:
        """Deep investigation of a failed pipeline: failed job logs, artifacts, the failing commit,
        its merge request, and what changed since the last successful pipeline."""
        return self.gitlab_provider.investigate_pipeline_failure(project_id, pipeline_id)

    def run(self, resource_id: str) -> Dict[str, Any]:
        """SpecialistAgent interface: same investigation, as a plain dict for the orchestrator to merge"""
        return asdict(self.investigate(resource_id))


class GitLabInvestigationAgent(SpecialistAgent):
    """Specialist agent for deep pipeline-failure investigation (Task 14): compares the latest
    failed pipeline against the last successful one and assembles a structured, factual report -
    no AI reasoning. Separate from GitLabAgent so it can be routed to independently (e.g. "why did
    the pipeline fail?") without paying for a full project investigation on every GitLab question.
    """

    name = "gitlab_investigation"

    def __init__(self, gitlab_provider: Optional[MockGitLabProvider] = None):
        self.gitlab_provider = gitlab_provider or MockGitLabProvider()

    def run(self, resource_id: str) -> Dict[str, Any]:
        """SpecialistAgent interface: structured pipeline-failure investigation report as a plain dict"""
        report = self.gitlab_provider.investigate_pipeline_failure(resource_id)
        return {"agent": "gitlab_investigation", **report}
