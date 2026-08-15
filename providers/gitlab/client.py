"""GitLab API integration: authentication, projects, branches, pipelines, jobs, and commits.

Uses python-gitlab against the GitLab REST API, authenticated with a personal/project
access token from .env (see config.Config). Every method degrades to an empty/None
result on any auth or API failure, consistent with the Azure provider modules.
"""
import logging
from typing import Any, Dict, List, Optional

import gitlab

from config import Config
from providers.gitlab.change_intelligence import classify_diff_entry, correlate_related_files
from providers.gitlab.investigation import (
    analyze_artifact_archive,
    extract_error_message,
    extract_stack_trace,
    find_failure_point,
)
from providers.gitlab.repository import (
    detect_tech_stack_from_tree,
    find_dockerfiles,
    find_gitlab_ci_config,
    find_helm_charts,
    find_kubernetes_manifests,
    find_readme,
    parse_k8s_manifest_summary,
)

_MAX_FILE_CONTENT_CHARS = 8000  # keep large files from blowing up documentation prompts
_MAX_TREE_ENTRIES = 1000
_MAX_MANIFESTS_TO_PARSE = 8

logger = logging.getLogger(__name__)


def _combine_stage_status(current: str, new_status: str) -> str:
    """Roll up a stage's status across its jobs: failed beats running beats pending beats success."""
    priority = {"failed": 3, "running": 2, "pending": 1, "success": 0}
    return new_status if priority.get(new_status, 0) >= priority.get(current, 0) else current


class GitLabClient:
    """Authenticates with GitLab and fetches live projects, branches, pipelines, jobs, and commits."""

    def __init__(self, url: Optional[str] = None, token: Optional[str] = None):
        self.url = url or Config.GITLAB_URL
        self.token = token or Config.GITLAB_TOKEN
        self._client: Optional[gitlab.Gitlab] = None
        self.last_error: Optional[str] = None

    def is_configured(self) -> bool:
        """Whether a GitLab URL and access token are both present."""
        return bool(self.url and self.token)

    def get_client(self) -> gitlab.Gitlab:
        """Return a cached, authenticated gitlab.Gitlab client, building it on first use."""
        if not self.is_configured():
            raise ValueError("Missing GitLab credentials - set GITLAB_URL and GITLAB_TOKEN in .env")

        if self._client is None:
            client = gitlab.Gitlab(self.url, private_token=self.token)
            client.auth()
            self._client = client
        return self._client

    def test_connection(self) -> Dict[str, Any]:
        """Authenticate and identify the current user to verify connectivity."""
        try:
            client = self.get_client()
            return {
                "connected": True,
                "message": "GitLab Connected",
                "user": client.user.username if client.user else None,
                "url": self.url,
            }
        except Exception as exc:
            logger.error("GitLab authentication failed: %s", exc)
            return {"connected": False, "message": str(exc), "url": self.url}

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    @staticmethod
    def _to_project_dict(project: Any) -> Dict[str, Any]:
        return {
            "id": str(project.id),
            "resource_id": str(project.id),
            "name": project.name,
            "path": getattr(project, "path_with_namespace", project.name),
            "description": getattr(project, "description", "") or "",
            "default_branch": getattr(project, "default_branch", None),
            "web_url": getattr(project, "web_url", None),
            "http_url_to_repo": getattr(project, "http_url_to_repo", None),
            "ssh_url_to_repo": getattr(project, "ssh_url_to_repo", None),
            "visibility": getattr(project, "visibility", "private"),
            "last_activity_at": getattr(project, "last_activity_at", None),
            "resource_type": "gitlab_project",
            "type": "gitlab_project",
            "health_status": "Unknown",
        }

    def get_projects(self) -> List[Dict[str, Any]]:
        """Discover all GitLab projects the configured token has access to."""
        try:
            projects = self.get_client().projects.list(membership=True, get_all=True)
            self.last_error = None
            return [self._to_project_dict(p) for p in projects]
        except Exception as exc:
            logger.error("GitLab project discovery failed: %s", exc)
            self.last_error = str(exc)
            return []

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single project by its numeric ID or `namespace/path`."""
        if not project_id:
            return None
        try:
            project = self.get_client().projects.get(project_id)
            self.last_error = None
            return self._to_project_dict(project)
        except Exception as exc:
            logger.error("GitLab project lookup failed for %s: %s", project_id, exc)
            self.last_error = str(exc)
            return None

    def get_project_by_name(self, project_name: str) -> Optional[Dict[str, Any]]:
        """Fetch a single project by its display name (falls back to a full scan)."""
        if not project_name:
            return None
        return next(
            (p for p in self.get_projects() if p.get("name") == project_name or p.get("path", "").split("/")[-1] == project_name),
            None,
        )

    # ------------------------------------------------------------------
    # Repository structure (Task 24: documentation generator)
    # ------------------------------------------------------------------

    def get_repository_tree(self, project_id: str, ref: Optional[str] = None, recursive: bool = True) -> List[str]:
        """List every file path in the repository (capped), for structure/tech-stack detection."""
        if not project_id:
            return []
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            kwargs: Dict[str, Any] = {"recursive": recursive, "get_all": True}
            if ref:
                kwargs["ref"] = ref
            entries = project.repository_tree(**kwargs)
            self.last_error = None
        except Exception as exc:
            logger.error("GitLab repository tree listing failed for %s: %s", project_id, exc)
            self.last_error = str(exc)
            return []

        paths = [entry.get("path") for entry in entries if entry.get("type") == "blob" and entry.get("path")]
        return paths[:_MAX_TREE_ENTRIES]

    def get_file_content(self, project_id: str, file_path: str, ref: Optional[str] = None) -> Optional[str]:
        """Fetch and decode a single repository file's text content, truncated to a safe size.
        Returns None if the file doesn't exist, isn't decodable text, or the API call fails."""
        if not (project_id and file_path):
            return None
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            file_ref = ref or (self.get_project(project_id) or {}).get("default_branch") or "HEAD"
            file_obj = project.files.get(file_path=file_path, ref=file_ref)
            content = file_obj.decode()
            text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
            self.last_error = None
            return text[:_MAX_FILE_CONTENT_CHARS]
        except Exception as exc:
            logger.error("GitLab file fetch failed for %s/%s: %s", project_id, file_path, exc)
            self.last_error = str(exc)
            return None

    def get_languages(self, project_id: str) -> Dict[str, float]:
        """Language-percentage breakdown for a project, as computed by GitLab itself."""
        if not project_id:
            return {}
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            languages = project.languages()
            self.last_error = None
            return dict(languages or {})
        except Exception as exc:
            logger.error("GitLab language detection failed for %s: %s", project_id, exc)
            self.last_error = str(exc)
            return {}

    def get_environments(self, project_id: str) -> List[Dict[str, Any]]:
        """List deployment environments (e.g. production, staging) for a project."""
        if not project_id:
            return []
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            environments = project.environments.list(get_all=True)
            self.last_error = None
        except Exception as exc:
            logger.error("GitLab environment listing failed for %s: %s", project_id, exc)
            self.last_error = str(exc)
            return []

        results = []
        for env in environments:
            last_deployment = getattr(env, "last_deployment", None) or {}
            results.append({
                "name": env.name,
                "state": getattr(env, "state", None),
                "external_url": getattr(env, "external_url", None),
                "last_deployment_status": (last_deployment or {}).get("status"),
                "last_deployment_ref": ((last_deployment or {}).get("ref")),
                "last_deployment_created_at": (last_deployment or {}).get("created_at"),
            })
        return results

    def get_recent_commits(self, project_id: str, ref: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """List the most recent commits on a branch (default branch if `ref` is omitted), newest first."""
        if not project_id:
            return []
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            params: Dict[str, Any] = {"per_page": limit}
            if ref:
                params["ref_name"] = ref
            commits = project.commits.list(**params)
            self.last_error = None
            return [self._to_commit_dict(commit) for commit in commits[:limit]]
        except Exception as exc:
            logger.error("GitLab recent commit listing failed for %s: %s", project_id, exc)
            self.last_error = str(exc)
            return []

    def get_repository_profile(self, project_id: str, ref: Optional[str] = None) -> Dict[str, Any]:
        """One-shot repository structure profile for the documentation generator: tech stack,
        README, .gitlab-ci.yml, Dockerfile(s), Helm chart(s), and Kubernetes manifest(s).

        A single repository_tree call backs every detection below, so this costs one tree
        fetch plus a handful of small file fetches - not one API call per artifact type.
        """
        tree_paths = self.get_repository_tree(project_id, ref=ref)
        if not tree_paths:
            return {
                "found": False,
                "tree_available": False,
                "tech_stack": [],
                "readme": None,
                "gitlab_ci": None,
                "dockerfiles": [],
                "helm_charts": [],
                "kubernetes_manifests": [],
                "repository_structure": [],
            }

        languages = self.get_languages(project_id)
        tech_stack = sorted(set(list(languages.keys()) + detect_tech_stack_from_tree(tree_paths)))

        readme_path = find_readme(tree_paths)
        readme = None
        if readme_path:
            content = self.get_file_content(project_id, readme_path, ref=ref)
            readme = {"path": readme_path, "content": content} if content is not None else None

        ci_path = find_gitlab_ci_config(tree_paths)
        gitlab_ci = None
        if ci_path:
            content = self.get_file_content(project_id, ci_path, ref=ref)
            gitlab_ci = {"path": ci_path, "content": content} if content is not None else None

        dockerfile_paths = find_dockerfiles(tree_paths)
        dockerfiles = []
        for path in dockerfile_paths[:3]:
            content = self.get_file_content(project_id, path, ref=ref)
            dockerfiles.append({"path": path, "content": content})

        helm_chart_paths = find_helm_charts(tree_paths)
        helm_charts = []
        for path in helm_chart_paths[:3]:
            content = self.get_file_content(project_id, path, ref=ref)
            helm_charts.append({"path": path, "content": content})

        manifest_paths = find_kubernetes_manifests(tree_paths)
        kubernetes_manifests = []
        for path in manifest_paths[:_MAX_MANIFESTS_TO_PARSE]:
            content = self.get_file_content(project_id, path, ref=ref)
            objects = parse_k8s_manifest_summary(content) if content else []
            kubernetes_manifests.append({"path": path, "objects": objects})

        return {
            "found": True,
            "tree_available": True,
            "language_percentages": languages,
            "tech_stack": tech_stack,
            "readme": readme,
            "gitlab_ci": gitlab_ci,
            "dockerfiles": dockerfiles,
            "helm_charts": helm_charts,
            "kubernetes_manifests": kubernetes_manifests,
            "kubernetes_manifests_total_found": len(manifest_paths),
            "repository_structure": tree_paths,
        }

    # ------------------------------------------------------------------
    # Branches
    # ------------------------------------------------------------------

    def get_branches(self, project_id: str) -> List[Dict[str, Any]]:
        """List all branches for a project."""
        if not project_id:
            return []
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            branches = project.branches.list(get_all=True)
            self.last_error = None
        except Exception as exc:
            logger.error("GitLab branch listing failed for %s: %s", project_id, exc)
            self.last_error = str(exc)
            return []

        results = []
        for branch in branches:
            commit = getattr(branch, "commit", None) or {}
            results.append({
                "name": branch.name,
                "default": bool(getattr(branch, "default", False)),
                "protected": bool(getattr(branch, "protected", False)),
                "commit_id": commit.get("id"),
                "commit_message": commit.get("message"),
                "committed_date": commit.get("committed_date"),
            })
        return results

    # ------------------------------------------------------------------
    # Pipelines
    # ------------------------------------------------------------------

    @staticmethod
    def _to_pipeline_dict(pipeline: Any) -> Dict[str, Any]:
        return {
            "id": pipeline.id,
            "project_id": getattr(pipeline, "project_id", None),
            "status": pipeline.status,
            "ref": pipeline.ref,
            "sha": pipeline.sha,
            "web_url": getattr(pipeline, "web_url", None),
            "created_at": getattr(pipeline, "created_at", None),
            "updated_at": getattr(pipeline, "updated_at", None),
        }

    def get_pipelines(self, project_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """List the most recent pipelines for a project, newest first."""
        if not project_id:
            return []
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            pipelines = project.pipelines.list(order_by="id", sort="desc", per_page=limit)
            self.last_error = None
            return [self._to_pipeline_dict(pipeline) for pipeline in pipelines]
        except Exception as exc:
            logger.error("GitLab pipeline listing failed for %s: %s", project_id, exc)
            self.last_error = str(exc)
            return []

    def get_all_pipelines(self) -> List[Dict[str, Any]]:
        """List recent pipelines across every accessible project."""
        pipelines = []
        for project in self.get_projects():
            pipelines.extend(self.get_pipelines(project["id"]))
        return pipelines

    def get_pipeline(self, project_id: str, pipeline_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single pipeline by project and pipeline ID."""
        if not (project_id and pipeline_id):
            return None
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            pipeline = project.pipelines.get(pipeline_id)
            self.last_error = None
            return self._to_pipeline_dict(pipeline)
        except Exception as exc:
            logger.error("GitLab pipeline lookup failed for %s/%s: %s", project_id, pipeline_id, exc)
            self.last_error = str(exc)
            return None

    def get_pipeline_stages(self, project_id: str, pipeline_id: str) -> List[Dict[str, Any]]:
        """Derive per-stage status rollups for a pipeline from its jobs."""
        stages: Dict[str, Dict[str, Any]] = {}
        for job in self.get_jobs(project_id, pipeline_id):
            stage_name = job.get("stage") or "unknown"
            stage = stages.setdefault(stage_name, {"name": stage_name, "jobs": [], "status": "success"})
            stage["jobs"].append(job.get("name"))
            stage["status"] = _combine_stage_status(stage["status"], job.get("status") or "unknown")
        return list(stages.values())

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    @staticmethod
    def _to_job_dict(job: Any) -> Dict[str, Any]:
        return {
            "id": job.id,
            "name": job.name,
            "stage": getattr(job, "stage", None),
            "status": job.status,
            "duration": getattr(job, "duration", None),
            "created_at": getattr(job, "created_at", None),
            "started_at": getattr(job, "started_at", None),
            "finished_at": getattr(job, "finished_at", None),
            "web_url": getattr(job, "web_url", None),
        }

    def get_jobs(self, project_id: str, pipeline_id: str) -> List[Dict[str, Any]]:
        """List all jobs for a pipeline."""
        if not (project_id and pipeline_id):
            return []
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            pipeline = project.pipelines.get(pipeline_id, lazy=True)
            jobs = pipeline.jobs.list(get_all=True)
            self.last_error = None
            return [self._to_job_dict(job) for job in jobs]
        except Exception as exc:
            logger.error("GitLab job listing failed for %s/%s: %s", project_id, pipeline_id, exc)
            self.last_error = str(exc)
            return []

    def get_job(self, project_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single job by project and job ID."""
        if not (project_id and job_id):
            return None
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            job = project.jobs.get(job_id)
            self.last_error = None
            return self._to_job_dict(job)
        except Exception as exc:
            logger.error("GitLab job lookup failed for %s/%s: %s", project_id, job_id, exc)
            self.last_error = str(exc)
            return None

    def get_job_logs(self, project_id: str, job_id: str) -> Dict[str, Any]:
        """Fetch the trace/log output for a job."""
        if not (project_id and job_id):
            return {"error": "Missing project or job ID", "logs": ""}
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            job = project.jobs.get(job_id, lazy=True)
            trace = job.trace()
            self.last_error = None
            text = trace.decode("utf-8", errors="replace") if isinstance(trace, bytes) else str(trace)
            return {"job_id": job_id, "logs": text}
        except Exception as exc:
            logger.error("GitLab job log fetch failed for %s/%s: %s", project_id, job_id, exc)
            self.last_error = str(exc)
            return {"error": str(exc), "logs": ""}

    # ------------------------------------------------------------------
    # Commits
    # ------------------------------------------------------------------

    @staticmethod
    def _to_commit_dict(commit: Any) -> Dict[str, Any]:
        return {
            "commit_id": commit.id,
            "short_id": getattr(commit, "short_id", commit.id[:8]),
            "title": getattr(commit, "title", None),
            "message": getattr(commit, "message", None),
            "author_name": getattr(commit, "author_name", None),
            "authored_date": getattr(commit, "authored_date", None),
            "committed_date": getattr(commit, "committed_date", None),
            "web_url": getattr(commit, "web_url", None),
        }

    def get_latest_commit(self, project_id: str, ref: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch the most recent commit on a project's default branch (or a given ref)."""
        if not project_id:
            return None
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            params: Dict[str, Any] = {"per_page": 1}
            if ref:
                params["ref_name"] = ref
            commits = project.commits.list(**params)
            self.last_error = None
            return self._to_commit_dict(commits[0]) if commits else None
        except Exception as exc:
            logger.error("GitLab latest commit fetch failed for %s: %s", project_id, exc)
            self.last_error = str(exc)
            return None

    # ------------------------------------------------------------------
    # Merge requests / pipeline status rollup (kept for existing agent/UI contracts)
    # ------------------------------------------------------------------

    @staticmethod
    def _to_mr_dict(mr: Any) -> Dict[str, Any]:
        author = getattr(mr, "author", None) or {}
        return {
            "id": mr.iid,
            "title": mr.title,
            "description": getattr(mr, "description", None),
            "state": mr.state,
            "merge_status": getattr(mr, "merge_status", None),
            "source_branch": mr.source_branch,
            "target_branch": mr.target_branch,
            "author": author.get("username"),
            "web_url": getattr(mr, "web_url", None),
            "created_at": getattr(mr, "created_at", None),
        }

    def get_merge_requests(self, project_id: str) -> List[Dict[str, Any]]:
        """List open merge requests for a project."""
        if not project_id:
            return []
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            merge_requests = project.mergerequests.list(state="opened", get_all=True)
            self.last_error = None
        except Exception as exc:
            logger.error("GitLab merge request listing failed for %s: %s", project_id, exc)
            self.last_error = str(exc)
            return []

        return [self._to_mr_dict(mr) for mr in merge_requests]

    def get_merge_request(self, project_id: str, mr_iid: str) -> Optional[Dict[str, Any]]:
        """Fetch full details for a single merge request by its IID."""
        if not (project_id and mr_iid):
            return None
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            mr = project.mergerequests.get(mr_iid)
            self.last_error = None
            return self._to_mr_dict(mr)
        except Exception as exc:
            logger.error("GitLab merge request lookup failed for %s/%s: %s", project_id, mr_iid, exc)
            self.last_error = str(exc)
            return None

    def get_merge_request_for_branch(self, project_id: str, branch_name: str) -> Optional[Dict[str, Any]]:
        """Find the (most recently updated) merge request whose source branch is `branch_name`, if any."""
        if not (project_id and branch_name):
            return None
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            merge_requests = project.mergerequests.list(source_branch=branch_name, order_by="updated_at", sort="desc")
            self.last_error = None
            return self._to_mr_dict(merge_requests[0]) if merge_requests else None
        except Exception as exc:
            logger.error("GitLab merge request lookup by branch failed for %s/%s: %s", project_id, branch_name, exc)
            self.last_error = str(exc)
            return None

    def get_pipeline_status(self, project_id: str) -> Dict[str, Any]:
        """Summarize the latest pipeline status and recent success rate for a project."""
        pipelines = self.get_pipelines(project_id, limit=20)
        if not pipelines:
            return {"status": "no_data", "message": "No pipelines found"}

        latest = pipelines[0]
        successes = len([p for p in pipelines if p.get("status") == "success"])
        success_rate = round((successes / len(pipelines)) * 100, 1)

        return {
            "status": latest.get("status", "unknown"),
            "latest_pipeline": latest.get("id"),
            "success_rate": success_rate,
        }

    # ------------------------------------------------------------------
    # Deep investigation (Task 14): failed logs, artifacts, diffs, and a
    # structured failed-vs-last-successful pipeline comparison report.
    # ------------------------------------------------------------------

    def get_commit_diff(self, project_id: str, sha: str) -> List[Dict[str, Any]]:
        """Fetch the changed-files diff for a single commit."""
        if not (project_id and sha):
            return []
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            commit = project.commits.get(sha, lazy=True)
            diffs = commit.diff(get_all=True)
            self.last_error = None
        except Exception as exc:
            logger.error("GitLab commit diff fetch failed for %s/%s: %s", project_id, sha, exc)
            self.last_error = str(exc)
            return []

        return [
            {
                "old_path": d.get("old_path"),
                "new_path": d.get("new_path"),
                "new_file": bool(d.get("new_file")),
                "deleted_file": bool(d.get("deleted_file")),
                "renamed_file": bool(d.get("renamed_file")),
                "diff": d.get("diff", ""),
            }
            for d in diffs
        ]

    def get_job_artifacts_info(self, project_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        """Fetch artifact metadata (filename/size) for a job, if it produced any. No file content is downloaded."""
        if not (project_id and job_id):
            return None
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            job = project.jobs.get(job_id)
            self.last_error = None
        except Exception as exc:
            logger.error("GitLab artifact lookup failed for %s/%s: %s", project_id, job_id, exc)
            self.last_error = str(exc)
            return None

        artifacts_file = getattr(job, "artifacts_file", None)
        if not artifacts_file:
            return None
        return {
            "job_id": job_id,
            "job_name": job.name,
            "filename": artifacts_file.get("filename"),
            "size": artifacts_file.get("size"),
        }

    def get_pipeline_artifacts(self, project_id: str, pipeline_id: str) -> List[Dict[str, Any]]:
        """List artifact metadata for every job in a pipeline that produced artifacts."""
        artifacts = []
        for job in self.get_jobs(project_id, pipeline_id):
            info = self.get_job_artifacts_info(project_id, job.get("id"))
            if info:
                artifacts.append(info)
        return artifacts

    def compare_refs(self, project_id: str, from_ref: str, to_ref: str) -> Dict[str, Any]:
        """Compare two refs/SHAs: commits and file diffs between them."""
        if not (project_id and from_ref and to_ref):
            return {"commits": [], "diffs": []}
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            comparison = project.repository_compare(from_=from_ref, to=to_ref)
            self.last_error = None
        except Exception as exc:
            logger.error("GitLab ref comparison failed for %s (%s...%s): %s", project_id, from_ref, to_ref, exc)
            self.last_error = str(exc)
            return {"commits": [], "diffs": []}

        commits = [
            {
                "id": c.get("id"),
                "short_id": c.get("short_id"),
                "title": c.get("title"),
                "author_name": c.get("author_name"),
                "committed_date": c.get("committed_date"),
            }
            for c in (comparison.get("commits") or [])
        ]
        diffs = [
            {
                "old_path": d.get("old_path"),
                "new_path": d.get("new_path"),
                "new_file": bool(d.get("new_file")),
                "deleted_file": bool(d.get("deleted_file")),
                "renamed_file": bool(d.get("renamed_file")),
                "diff": d.get("diff", ""),
            }
            for d in (comparison.get("diffs") or [])
        ]
        return {"commits": commits, "diffs": diffs}

    def _find_failed_pipeline(self, pipelines: List[Dict[str, Any]], pipeline_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if pipeline_id:
            return next((p for p in pipelines if str(p.get("id")) == str(pipeline_id)), None)
        return next((p for p in pipelines if p.get("status") == "failed"), None)

    @staticmethod
    def _find_last_successful_pipeline(pipelines: List[Dict[str, Any]], before_id: Any) -> Optional[Dict[str, Any]]:
        """Most recent 'success' pipeline older than `before_id`, falling back to the most recent success overall."""
        successes = [p for p in pipelines if p.get("status") == "success"]
        older = [p for p in successes if p.get("id") < before_id]
        if older:
            return max(older, key=lambda p: p["id"])
        return successes[0] if successes else None

    def investigate_pipeline_failure(self, project_id: str, pipeline_id: Optional[str] = None) -> Dict[str, Any]:
        """Build a structured investigation report for a failed pipeline (no AI reasoning - just the facts):
        failed job logs, artifacts, the failing commit, its merge request (if any), and what changed since
        the last successful pipeline (commits + file diffs).
        """
        if not project_id:
            return {"found": False, "message": "No project specified"}

        pipelines = self.get_pipelines(project_id, limit=50)
        failed_pipeline = self._find_failed_pipeline(pipelines, pipeline_id)
        if not failed_pipeline:
            return {"found": False, "message": "No failed pipeline found"}

        successful_pipeline = self._find_last_successful_pipeline(pipelines, failed_pipeline["id"])

        jobs = self.get_jobs(project_id, failed_pipeline["id"])
        failed_jobs = [dict(job, logs=self.get_job_logs(project_id, job["id"]).get("logs", "")) for job in jobs if job.get("status") == "failed"]

        artifacts = self.get_pipeline_artifacts(project_id, failed_pipeline["id"])
        commit = self._to_commit_dict_safe(project_id, failed_pipeline.get("sha"))
        merge_request = self.get_merge_request_for_branch(project_id, failed_pipeline.get("ref"))

        comparison = None
        if successful_pipeline:
            comparison = self.compare_refs(project_id, successful_pipeline["sha"], failed_pipeline["sha"])
            comparison["changed_files"] = sorted({
                d.get("new_path") or d.get("old_path") for d in comparison["diffs"] if d.get("new_path") or d.get("old_path")
            })

        return {
            "found": True,
            "failed_pipeline": failed_pipeline,
            "successful_pipeline": successful_pipeline,
            "failed_jobs": failed_jobs,
            "artifacts": artifacts,
            "commit": commit,
            "merge_request": merge_request,
            "comparison": comparison,
        }

    def _to_commit_dict_safe(self, project_id: str, sha: Optional[str]) -> Optional[Dict[str, Any]]:
        if not sha:
            return None
        try:
            project = self.get_client().projects.get(project_id, lazy=True)
            commit = project.commits.get(sha)
            self.last_error = None
            return self._to_commit_dict(commit)
        except Exception as exc:
            logger.error("GitLab commit lookup failed for %s/%s: %s", project_id, sha, exc)
            self.last_error = str(exc)
            return None

    # ------------------------------------------------------------------
    # Live pipeline investigation (Task 15): structured evidence only - no root
    # cause, no recommendations. Every fact below is either a raw API field or
    # something mechanically derived from the job log / artifacts (see
    # providers/gitlab/investigation.py for the log/artifact parsing).
    # ------------------------------------------------------------------

    @staticmethod
    def _to_investigation_job_dict(job: Any) -> Dict[str, Any]:
        """Job detail including the extra timing/failure fields the basic `_to_job_dict`
        (used by the live GitLab page) doesn't need."""
        return {
            "id": job.id,
            "name": job.name,
            "stage": getattr(job, "stage", None),
            "status": job.status,
            "duration_seconds": getattr(job, "duration", None),
            "queued_duration_seconds": getattr(job, "queued_duration", None),
            "created_at": getattr(job, "created_at", None),
            "started_at": getattr(job, "started_at", None),
            "finished_at": getattr(job, "finished_at", None),
            "failure_reason": getattr(job, "failure_reason", None),
            "allow_failure": bool(getattr(job, "allow_failure", False)),
            "web_url": getattr(job, "web_url", None),
        }

    @staticmethod
    def _build_stage_summary(stage_name: str, stage_jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Stage-level facts derived from its jobs: GitLab has no standalone "stage" object
        with its own duration, so this is aggregated from the jobs that ran in it."""
        durations = [j["duration_seconds"] for j in stage_jobs if j.get("duration_seconds") is not None]
        started = [j["started_at"] for j in stage_jobs if j.get("started_at")]
        finished = [j["finished_at"] for j in stage_jobs if j.get("finished_at")]
        if any(j["status"] == "failed" for j in stage_jobs):
            status = "failed"
        elif stage_jobs and all(j["status"] == "success" for j in stage_jobs):
            status = "success"
        else:
            status = "unknown"

        return {
            "name": stage_name,
            "status": status,
            "jobs": [j["name"] for j in stage_jobs],
            "job_count": len(stage_jobs),
            "total_job_duration_seconds": round(sum(durations), 2) if durations else None,
            "started_at": min(started) if started else None,
            "finished_at": max(finished) if finished else None,
        }

    def _compare_job_with_last_successful(
        self, project: Any, pipelines_summary: List[Dict[str, Any]], failed_pipeline_id: Any, failed_job: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Find the same-named job in the last successful pipeline and compare it to the failed
        one - status and duration only, no interpretation of what the difference means."""
        successful_pipeline = self._find_last_successful_pipeline(pipelines_summary, failed_pipeline_id)
        if not successful_pipeline:
            return {"found": False, "reason": "No successful pipeline found for this project"}

        try:
            successful_pipeline_obj = project.pipelines.get(successful_pipeline["id"], lazy=True)
            successful_jobs = list(successful_pipeline_obj.jobs.list(get_all=True))
        except Exception as exc:
            logger.error(
                "GitLab comparison-pipeline job listing failed for %s/%s: %s",
                project.id, successful_pipeline["id"], exc,
            )
            return {"found": False, "reason": str(exc)}

        matching_job = next((job for job in successful_jobs if job.name == failed_job["name"]), None)
        result: Dict[str, Any] = {
            "found": True,
            "last_successful_pipeline": {
                "id": successful_pipeline["id"],
                "sha": successful_pipeline.get("sha"),
                "created_at": successful_pipeline.get("created_at"),
            },
            "job_in_last_successful_pipeline": None,
            "duration_delta_seconds": None,
        }
        if not matching_job:
            result["reason"] = f"No job named '{failed_job['name']}' in the last successful pipeline"
            return result

        matching_job_dict = self._to_investigation_job_dict(matching_job)
        result["job_in_last_successful_pipeline"] = matching_job_dict

        failed_duration = failed_job.get("duration_seconds")
        successful_duration = matching_job_dict.get("duration_seconds")
        if failed_duration is not None and successful_duration is not None:
            result["duration_delta_seconds"] = round(failed_duration - successful_duration, 2)
        return result

    @staticmethod
    def _empty_pipeline_investigation(project_id: str, pipeline_id: Optional[Any] = None) -> Dict[str, Any]:
        return {
            "pipeline": {"project_id": project_id, "id": pipeline_id},
            "stage": None,
            "failed_job": None,
            "failure_point": None,
            "error_message": None,
            "stack_trace": None,
            "artifacts": [],
            "comparison_with_last_successful_pipeline": {"found": False},
            "evidence": [],
        }

    def investigate_pipeline(self, project_id: str, pipeline_id: Optional[str] = None) -> Dict[str, Any]:
        """Live evidence collection for a failed pipeline (Task 15) - structured facts only,
        no root cause and no recommendations. If `pipeline_id` is omitted, investigates the
        most recent failed pipeline.

        Output shape: {pipeline, stage, failed_job, failure_point, error_message, stack_trace,
        artifacts, comparison_with_last_successful_pipeline, evidence}.
        """
        if not project_id:
            self.last_error = "No project specified"
            return self._empty_pipeline_investigation(project_id, pipeline_id)

        evidence: List[Dict[str, Any]] = []

        try:
            project = self.get_client().projects.get(project_id, lazy=True)
        except Exception as exc:
            logger.error("GitLab pipeline investigation could not resolve project %s: %s", project_id, exc)
            self.last_error = str(exc)
            return self._empty_pipeline_investigation(project_id, pipeline_id)

        pipelines_summary = self.get_pipelines(project_id, limit=50)
        pipeline_summary = self._find_failed_pipeline(pipelines_summary, pipeline_id)
        if not pipeline_summary:
            self.last_error = "No matching failed pipeline found"
            return self._empty_pipeline_investigation(project_id, pipeline_id)

        try:
            full_pipeline = project.pipelines.get(pipeline_summary["id"])
        except Exception as exc:
            logger.error("GitLab pipeline fetch failed for %s/%s: %s", project_id, pipeline_summary["id"], exc)
            self.last_error = str(exc)
            return self._empty_pipeline_investigation(project_id, pipeline_summary["id"])

        pipeline_info = {
            "id": full_pipeline.id,
            "project_id": project_id,
            "status": full_pipeline.status,
            "ref": full_pipeline.ref,
            "sha": full_pipeline.sha,
            "web_url": getattr(full_pipeline, "web_url", None),
            "created_at": getattr(full_pipeline, "created_at", None),
            "started_at": getattr(full_pipeline, "started_at", None),
            "finished_at": getattr(full_pipeline, "finished_at", None),
            "duration_seconds": getattr(full_pipeline, "duration", None),
            "queued_duration_seconds": getattr(full_pipeline, "queued_duration", None),
        }
        evidence.append({
            "type": "pipeline_metadata",
            "description": f"Pipeline #{pipeline_info['id']} status={pipeline_info['status']} on ref '{pipeline_info['ref']}'",
            "source": "GitLab Pipelines API",
            "detail": pipeline_info,
        })

        try:
            pipeline_lazy = project.pipelines.get(pipeline_info["id"], lazy=True)
            raw_jobs = list(pipeline_lazy.jobs.list(get_all=True))
        except Exception as exc:
            logger.error("GitLab job listing failed for %s/%s: %s", project_id, pipeline_info["id"], exc)
            self.last_error = str(exc)
            result = self._empty_pipeline_investigation(project_id, pipeline_info["id"])
            result["pipeline"] = pipeline_info
            result["evidence"] = evidence
            return result

        job_details = [self._to_investigation_job_dict(job) for job in raw_jobs]
        evidence.append({
            "type": "job_list",
            "description": f"{len(job_details)} job(s) found in pipeline #{pipeline_info['id']}",
            "source": "GitLab Jobs API",
            "detail": [{"name": j["name"], "stage": j["stage"], "status": j["status"]} for j in job_details],
        })

        failed_jobs = [job for job in job_details if job["status"] == "failed"]
        if not failed_jobs:
            self.last_error = "No failed job found in this pipeline"
            result = self._empty_pipeline_investigation(project_id, pipeline_info["id"])
            result["pipeline"] = pipeline_info
            result["evidence"] = evidence
            return result

        # Earliest-created failed job = the first point of failure across the pipeline.
        failed_job = min(failed_jobs, key=lambda job: job["id"])
        evidence.append({
            "type": "failed_job",
            "description": f"Job '{failed_job['name']}' (stage '{failed_job['stage']}') failed"
                            + (f": {failed_job['failure_reason']}" if failed_job.get("failure_reason") else ""),
            "source": "GitLab Jobs API",
            "detail": failed_job,
        })

        stage_jobs = [job for job in job_details if job["stage"] == failed_job["stage"]]
        stage_info = self._build_stage_summary(failed_job["stage"], stage_jobs)
        evidence.append({
            "type": "stage_summary",
            "description": f"Stage '{stage_info['name']}' ran {stage_info['job_count']} job(s), status={stage_info['status']}",
            "source": "Derived from job list",
            "detail": stage_info,
        })

        log_text = ""
        try:
            job_obj = project.jobs.get(failed_job["id"])
            trace = job_obj.trace()
            log_text = trace.decode("utf-8", errors="replace") if isinstance(trace, bytes) else str(trace)
            self.last_error = None
        except Exception as exc:
            logger.error("GitLab job log fetch failed for %s/%s: %s", project_id, failed_job["id"], exc)
            self.last_error = str(exc)
            job_obj = None

        failure_point = find_failure_point(log_text)
        error_message = extract_error_message(log_text, failure_point)
        stack_trace = extract_stack_trace(log_text)

        if failure_point:
            evidence.append({
                "type": "log_excerpt",
                "description": "First failure indicator found in the job log",
                "source": f"Job {failed_job['id']} trace, line {failure_point['line_number']}",
                "detail": failure_point,
            })
        if stack_trace:
            evidence.append({
                "type": "stack_trace",
                "description": "Stack trace found in the job log",
                "source": f"Job {failed_job['id']} trace",
                "detail": stack_trace,
            })

        artifacts: List[Dict[str, Any]] = []
        if job_obj is not None and getattr(job_obj, "artifacts_file", None):
            try:
                archive_bytes = job_obj.artifacts()
                artifacts = analyze_artifact_archive(archive_bytes)
                evidence.append({
                    "type": "artifacts",
                    "description": f"{len(artifacts)} artifact file(s) downloaded and analyzed",
                    "source": f"Job {failed_job['id']} artifacts.zip",
                    "detail": [{"filename": a["filename"], "size": a["size"]} for a in artifacts],
                })
            except Exception as exc:
                logger.error("GitLab artifact download failed for %s/%s: %s", project_id, failed_job["id"], exc)

        comparison = self._compare_job_with_last_successful(project, pipelines_summary, pipeline_info["id"], failed_job)
        if comparison.get("found"):
            evidence.append({
                "type": "comparison",
                "description": f"Compared job '{failed_job['name']}' with the same job in pipeline #{comparison['last_successful_pipeline']['id']}",
                "source": "GitLab Pipelines/Jobs API",
                "detail": comparison,
            })

        self.last_error = None
        return {
            "pipeline": pipeline_info,
            "stage": stage_info,
            "failed_job": failed_job,
            "failure_point": failure_point,
            "error_message": error_message,
            "stack_trace": stack_trace,
            "artifacts": artifacts,
            "comparison_with_last_successful_pipeline": comparison,
            "evidence": evidence,
        }

    # ------------------------------------------------------------------
    # Git change intelligence (Task 16): structured evidence only - no root
    # cause, no recommendations. Commit/MR facts and a structural correlation
    # between changed files and the failed stage (see
    # providers/gitlab/change_intelligence.py for the pure classification logic).
    # ------------------------------------------------------------------

    @staticmethod
    def _to_commit_detail_dict(commit: Any) -> Dict[str, Any]:
        stats = getattr(commit, "stats", None) or {}
        return {
            "commit_id": commit.id,
            "short_id": getattr(commit, "short_id", commit.id[:8]),
            "title": getattr(commit, "title", None),
            "message": getattr(commit, "message", None),
            "author_name": getattr(commit, "author_name", None),
            "author_email": getattr(commit, "author_email", None),
            "authored_date": getattr(commit, "authored_date", None),
            "committer_name": getattr(commit, "committer_name", None),
            "committed_date": getattr(commit, "committed_date", None),
            "parent_ids": list(getattr(commit, "parent_ids", None) or []),
            "web_url": getattr(commit, "web_url", None),
            "stats": {
                "additions": stats.get("additions"),
                "deletions": stats.get("deletions"),
                "total": stats.get("total"),
            } if stats else None,
        }

    def _to_commit_detail_dict_safe(self, project: Any, sha: Optional[str]) -> Optional[Dict[str, Any]]:
        if not sha:
            return None
        try:
            commit = project.commits.get(sha)
            return self._to_commit_detail_dict(commit)
        except Exception as exc:
            logger.error("GitLab commit detail fetch failed for %s/%s: %s", project.id, sha, exc)
            return None

    def _to_full_mr_dict(self, project: Any, mr_iid: Any) -> Optional[Dict[str, Any]]:
        """Full MR detail: title/description/state (via `_to_mr_dict`) plus reviewers,
        approvals, comments, and the diff the MR introduces."""
        try:
            mr = project.mergerequests.get(mr_iid)
        except Exception as exc:
            logger.error("GitLab MR detail fetch failed for %s/%s: %s", project.id, mr_iid, exc)
            return None

        result = self._to_mr_dict(mr)

        result["reviewers"] = [
            {"username": reviewer.get("username"), "name": reviewer.get("name")}
            for reviewer in (getattr(mr, "reviewers", None) or [])
        ]

        result["approvals"] = {"approved": None, "approved_by": [], "approvals_required": None, "approvals_left": None}
        try:
            approval_state = mr.approvals.get()
            result["approvals"] = {
                "approved": getattr(approval_state, "approved", None),
                "approved_by": [
                    (entry.get("user") or {}).get("username")
                    for entry in (getattr(approval_state, "approved_by", None) or [])
                ],
                "approvals_required": getattr(approval_state, "approvals_required", None),
                "approvals_left": getattr(approval_state, "approvals_left", None),
            }
        except Exception as exc:
            logger.error("GitLab MR approvals fetch failed for %s/%s: %s", project.id, mr_iid, exc)

        result["comments"] = []
        try:
            for note in mr.notes.list(get_all=True):
                if getattr(note, "system", False):
                    continue
                author = getattr(note, "author", None) or {}
                result["comments"].append({
                    "author": author.get("username"),
                    "body": getattr(note, "body", None),
                    "created_at": getattr(note, "created_at", None),
                })
        except Exception as exc:
            logger.error("GitLab MR notes fetch failed for %s/%s: %s", project.id, mr_iid, exc)

        result["merged_changes"] = []
        try:
            changes_response = mr.changes()
            for raw in (changes_response.get("changes") or []):
                result["merged_changes"].append(classify_diff_entry(raw))
        except Exception as exc:
            logger.error("GitLab MR changes fetch failed for %s/%s: %s", project.id, mr_iid, exc)

        return result

    def _find_merge_request_for_commit(
        self, project: Any, sha: str, ref: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """The MR linked to the pipeline: prefer the MR GitLab associates with the triggering
        commit directly; fall back to matching the pipeline's branch/ref to an MR's source branch."""
        mr_iid = None
        try:
            commit = project.commits.get(sha, lazy=True)
            related = commit.merge_requests()
            if related:
                mr_iid = related[0].get("iid")
        except Exception as exc:
            logger.error("GitLab commit-to-MR lookup failed for %s/%s: %s", project.id, sha, exc)

        if mr_iid is None and ref:
            mr_summary = self.get_merge_request_for_branch(project.id, ref)
            mr_iid = mr_summary.get("id") if mr_summary else None

        if mr_iid is None:
            return None
        return self._to_full_mr_dict(project, mr_iid)

    @staticmethod
    def _find_failed_stage_name(project: Any, pipeline_id: Any) -> Optional[str]:
        """The stage of the earliest-failed job in a pipeline, for correlating changed files."""
        try:
            pipeline_lazy = project.pipelines.get(pipeline_id, lazy=True)
            jobs = list(pipeline_lazy.jobs.list(get_all=True))
        except Exception as exc:
            logger.error(
                "GitLab job listing failed while locating the failed stage for %s/%s: %s",
                project.id, pipeline_id, exc,
            )
            return None

        failed_jobs = [job for job in jobs if job.status == "failed"]
        if not failed_jobs:
            return None
        earliest = min(failed_jobs, key=lambda job: job.id)
        return getattr(earliest, "stage", None)

    @staticmethod
    def _empty_git_change_result() -> Dict[str, Any]:
        return {
            "triggering_commit": None,
            "previous_successful_commit": None,
            "changed_files": [],
            "git_diff": [],
            "merge_request": None,
            "related_files": [],
            "evidence": [],
        }

    def investigate_git_changes(self, project_id: str, pipeline_id: Optional[str] = None) -> Dict[str, Any]:
        """Live Git commit/MR evidence for a pipeline (Task 16) - structured facts only, no
        root cause and no recommendations. If `pipeline_id` is omitted, investigates the most
        recent failed pipeline.

        Output shape: {triggering_commit, previous_successful_commit, changed_files, git_diff,
        merge_request, related_files, evidence}.
        """
        if not project_id:
            self.last_error = "No project specified"
            return self._empty_git_change_result()

        evidence: List[Dict[str, Any]] = []

        try:
            project = self.get_client().projects.get(project_id, lazy=True)
        except Exception as exc:
            logger.error("GitLab git-change investigation could not resolve project %s: %s", project_id, exc)
            self.last_error = str(exc)
            return self._empty_git_change_result()

        pipelines_summary = self.get_pipelines(project_id, limit=50)
        pipeline_summary = self._find_failed_pipeline(pipelines_summary, pipeline_id)
        if not pipeline_summary:
            self.last_error = "No matching failed pipeline found"
            return self._empty_git_change_result()

        # Triggering commit
        triggering_commit = self._to_commit_detail_dict_safe(project, pipeline_summary.get("sha"))
        if triggering_commit:
            evidence.append({
                "type": "triggering_commit",
                "description": f"Pipeline #{pipeline_summary['id']} triggered by commit "
                               f"{triggering_commit['short_id']}: {triggering_commit['title']}",
                "source": "GitLab Commits API",
                "detail": triggering_commit,
            })

        # Previous successful commit
        successful_pipeline = self._find_last_successful_pipeline(pipelines_summary, pipeline_summary["id"])
        previous_successful_commit = None
        if successful_pipeline:
            previous_successful_commit = self._to_commit_detail_dict_safe(project, successful_pipeline.get("sha"))
            if previous_successful_commit:
                evidence.append({
                    "type": "previous_successful_commit",
                    "description": f"Last successful pipeline (#{successful_pipeline['id']}) ran commit "
                                   f"{previous_successful_commit['short_id']}",
                    "source": "GitLab Pipelines/Commits API",
                    "detail": previous_successful_commit,
                })

        # Changed files + git diff: everything changed since the last successful commit, when
        # one is known; otherwise just the triggering commit's own diff against its parent.
        if previous_successful_commit and triggering_commit:
            comparison = self.compare_refs(
                project_id, previous_successful_commit["commit_id"], triggering_commit["commit_id"]
            )
            raw_diffs = comparison.get("diffs") or []
            diff_source = f"Compare {previous_successful_commit['short_id']}...{triggering_commit['short_id']}"
        elif triggering_commit:
            raw_diffs = self.get_commit_diff(project_id, triggering_commit["commit_id"])
            diff_source = f"Commit {triggering_commit['short_id']} diff (no known-good baseline)"
        else:
            raw_diffs, diff_source = [], None

        changed_files: List[Dict[str, Any]] = []
        git_diff: List[Dict[str, Any]] = []
        for raw in raw_diffs:
            classified = classify_diff_entry(raw)
            changed_files.append(classified)
            git_diff.append({**classified, "diff": raw.get("diff", "")})

        if changed_files:
            evidence.append({
                "type": "changed_files",
                "description": f"{len(changed_files)} file(s) changed",
                "source": diff_source,
                "detail": changed_files,
            })

        # Merge request linked to the pipeline
        merge_request = None
        if triggering_commit:
            merge_request = self._find_merge_request_for_commit(
                project, triggering_commit["commit_id"], pipeline_summary.get("ref")
            )
        if merge_request:
            evidence.append({
                "type": "merge_request",
                "description": f"MR !{merge_request['id']} '{merge_request['title']}' ({merge_request['state']}), "
                               f"{len(merge_request.get('comments') or [])} comment(s), "
                               f"{len(merge_request.get('reviewers') or [])} reviewer(s)",
                "source": "GitLab Merge Requests API",
                "detail": merge_request,
            })

        # Correlate changed files with the failed stage
        failed_stage_name = self._find_failed_stage_name(project, pipeline_summary["id"])
        related_files = correlate_related_files(changed_files, failed_stage_name)
        if related_files:
            evidence.append({
                "type": "related_files",
                "description": (
                    f"{len(related_files)} changed file(s) correlated with the failed stage '{failed_stage_name}'"
                    if failed_stage_name else
                    f"{len(related_files)} changed file(s) structurally correlated with each other"
                ),
                "source": "Derived from changed files + failed stage",
                "detail": related_files,
            })

        self.last_error = None
        return {
            "triggering_commit": triggering_commit,
            "previous_successful_commit": previous_successful_commit,
            "changed_files": changed_files,
            "git_diff": git_diff,
            "merge_request": merge_request,
            "related_files": related_files,
            "evidence": evidence,
        }
