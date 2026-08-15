from typing import Any, Dict, List, Optional
from .base_provider import BaseProvider
from .gitlab.client import GitLabClient


class MockGitLabProvider(BaseProvider):
    """GitLab provider for CI/CD and repository management, backed by live GitLab API data."""

    def __init__(self, gitlab_client: Optional[GitLabClient] = None):
        self._gitlab = gitlab_client or GitLabClient()
        self._projects_cache: Optional[List[Dict[str, Any]]] = None

    def get_projects(self) -> List[Dict[str, Any]]:
        """Get all GitLab projects.

        Cached on this instance after the first call - project discovery is a live GitLab API
        call (`projects.list(membership=True, get_all=True)`) and this method is called
        unconditionally on every Streamlit rerun of the GitLab Workspace page (the project
        selector), so without caching every widget interaction on that page re-fetched the full
        project list. Matches the caching MockAzureProvider.get_all_resources() and
        MockAKSProvider.get_clusters() already do for their equivalent discovery calls.
        """
        if self._projects_cache is None:
            self._projects_cache = self._gitlab.get_projects()
        return self._projects_cache

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific project by ID"""
        return self._gitlab.get_project(project_id)

    def get_project_by_name(self, project_name: str) -> Optional[Dict[str, Any]]:
        """Get a specific project by name"""
        return self._gitlab.get_project_by_name(project_name)

    def get_branches(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all branches for a specific project"""
        return self._gitlab.get_branches(project_id)

    def get_pipelines(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all pipelines for a specific project"""
        return self._gitlab.get_pipelines(project_id)

    def get_pipeline(self, project_id: str, pipeline_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific pipeline by ID"""
        return self._gitlab.get_pipeline(project_id, pipeline_id)

    def get_all_pipelines(self) -> List[Dict[str, Any]]:
        """Get all pipelines across all projects"""
        return self._gitlab.get_all_pipelines()

    def get_pipeline_stages(self, project_id: str, pipeline_id: str) -> List[Dict[str, Any]]:
        """Get stage status rollups for a specific pipeline"""
        return self._gitlab.get_pipeline_stages(project_id, pipeline_id)

    def get_jobs(self, project_id: str, pipeline_id: str) -> List[Dict[str, Any]]:
        """Get all jobs for a specific pipeline"""
        return self._gitlab.get_jobs(project_id, pipeline_id)

    def get_job(self, project_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific job by ID"""
        return self._gitlab.get_job(project_id, job_id)

    def get_job_logs(self, project_id: str, job_id: str) -> Dict[str, Any]:
        """Get logs for a specific job"""
        return self._gitlab.get_job_logs(project_id, job_id)

    def get_latest_commit(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest commit for a project"""
        return self._gitlab.get_latest_commit(project_id)

    def get_merge_requests(self, project_id: str) -> List[Dict[str, Any]]:
        """Get merge requests for a project"""
        return self._gitlab.get_merge_requests(project_id)

    def get_pipeline_status(self, project_id: str) -> Dict[str, Any]:
        """Get overall pipeline status for a project"""
        return self._gitlab.get_pipeline_status(project_id)

    def get_commit_diff(self, project_id: str, sha: str) -> List[Dict[str, Any]]:
        """Get the changed-files diff for a single commit"""
        return self._gitlab.get_commit_diff(project_id, sha)

    def get_pipeline_artifacts(self, project_id: str, pipeline_id: str) -> List[Dict[str, Any]]:
        """Get artifact metadata for every job in a pipeline"""
        return self._gitlab.get_pipeline_artifacts(project_id, pipeline_id)

    def get_merge_request(self, project_id: str, mr_iid: str) -> Optional[Dict[str, Any]]:
        """Get full details for a single merge request"""
        return self._gitlab.get_merge_request(project_id, mr_iid)

    def get_merge_request_for_branch(self, project_id: str, branch_name: str) -> Optional[Dict[str, Any]]:
        """Get the merge request associated with a branch, if any"""
        return self._gitlab.get_merge_request_for_branch(project_id, branch_name)

    def investigate_pipeline_failure(self, project_id: str, pipeline_id: Optional[str] = None) -> Dict[str, Any]:
        """Build a structured investigation report comparing a failed pipeline to the last successful one"""
        return self._gitlab.investigate_pipeline_failure(project_id, pipeline_id)

    def investigate_pipeline(self, project_id: str, pipeline_id: Optional[str] = None) -> Dict[str, Any]:
        """Live, structured evidence for a failed pipeline: jobs, logs, failure point, error
        message, stack trace, artifacts, and a same-job comparison with the last successful
        pipeline. Facts only - no root cause, no recommendations."""
        return self._gitlab.investigate_pipeline(project_id, pipeline_id)

    def investigate_git_changes(self, project_id: str, pipeline_id: Optional[str] = None) -> Dict[str, Any]:
        """Live, structured Git change evidence for a pipeline: the triggering commit, the last
        successful commit, changed files and diff, the linked merge request (title, description,
        approvals, comments, reviewers, merged changes), and changed files correlated with the
        failed stage. Facts only - no root cause, no recommendations."""
        return self._gitlab.investigate_git_changes(project_id, pipeline_id)

    # ------------------------------------------------------------------
    # Repository structure (Task 24: documentation generator)
    # ------------------------------------------------------------------

    def get_repository_tree(self, project_id: str, ref: Optional[str] = None) -> List[str]:
        """List every file path in the repository (capped)."""
        return self._gitlab.get_repository_tree(project_id, ref=ref)

    def get_file_content(self, project_id: str, file_path: str, ref: Optional[str] = None) -> Optional[str]:
        """Fetch and decode a single repository file's text content."""
        return self._gitlab.get_file_content(project_id, file_path, ref=ref)

    def get_languages(self, project_id: str) -> Dict[str, float]:
        """Language-percentage breakdown for a project."""
        return self._gitlab.get_languages(project_id)

    def get_environments(self, project_id: str) -> List[Dict[str, Any]]:
        """List deployment environments for a project."""
        return self._gitlab.get_environments(project_id)

    def get_recent_commits(self, project_id: str, ref: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """List the most recent commits on a branch."""
        return self._gitlab.get_recent_commits(project_id, ref=ref, limit=limit)

    def get_repository_profile(self, project_id: str, ref: Optional[str] = None) -> Dict[str, Any]:
        """One-shot repository structure profile: tech stack, README, CI config, Dockerfile(s),
        Helm chart(s), and Kubernetes manifest(s)."""
        return self._gitlab.get_repository_profile(project_id, ref=ref)
