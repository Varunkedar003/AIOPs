"""Repository structure analysis helpers for GitLabClient (Task 24: documentation generator).

Pure functions over a repository tree listing (as returned by GitLab's repository_tree
API) and small file contents - no network calls happen here, so these are trivially
testable and reusable from providers/gitlab/client.py without adding any new dependency
on the GitLab SDK itself.
"""
import re
from typing import Any, Dict, List, Optional

_DOCKERFILE_PATTERN = re.compile(r"(^|/)dockerfile([.\-].+)?$", re.IGNORECASE)
_HELM_CHART_PATTERN = re.compile(r"(^|/)chart\.ya?ml$", re.IGNORECASE)
_K8S_DIR_PATTERN = re.compile(r"(^|/)(k8s|kubernetes|manifests|deploy)/", re.IGNORECASE)
_README_PATTERN = re.compile(r"^readme(\.md|\.rst|\.txt)?$", re.IGNORECASE)
_GITLAB_CI_PATTERN = re.compile(r"^\.gitlab-ci\.ya?ml$", re.IGNORECASE)

_EXTENSION_TECH = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript / React",
    ".js": "JavaScript",
    ".jsx": "JavaScript / React",
    ".java": "Java",
    ".kt": "Kotlin",
    ".go": "Go",
    ".rb": "Ruby",
    ".cs": "C#",
    ".php": "PHP",
    ".rs": "Rust",
    ".tf": "Terraform",
}
_MARKER_FILE_TECH = {
    "package.json": "Node.js",
    "requirements.txt": "Python (pip)",
    "pyproject.toml": "Python (Poetry/PEP 621)",
    "pipfile": "Python (Pipenv)",
    "pom.xml": "Java (Maven)",
    "build.gradle": "Java/Kotlin (Gradle)",
    "go.mod": "Go modules",
    "gemfile": "Ruby (Bundler)",
    "composer.json": "PHP (Composer)",
    "dockerfile": "Docker",
    "chart.yaml": "Helm",
    ".gitlab-ci.yml": "GitLab CI/CD",
}


def find_dockerfiles(tree_paths: List[str]) -> List[str]:
    """Repository paths that look like a Dockerfile (root or nested)."""
    return [p for p in tree_paths if _DOCKERFILE_PATTERN.search(p)]


def find_helm_charts(tree_paths: List[str]) -> List[str]:
    """Repository paths for Helm Chart.yaml files, signalling a Helm chart is present."""
    return [p for p in tree_paths if _HELM_CHART_PATTERN.search(p)]


def find_kubernetes_manifests(tree_paths: List[str]) -> List[str]:
    """Repository paths that look like raw Kubernetes manifests, by conventional directory
    name or a top-level *.yaml/*.yml file (Helm chart templates are reported separately)."""
    matches = []
    for path in tree_paths:
        lower = path.lower()
        if not (lower.endswith(".yaml") or lower.endswith(".yml")):
            continue
        if _HELM_CHART_PATTERN.search(path):
            continue
        if _K8S_DIR_PATTERN.search(path) or lower in ("k8s.yaml", "kubernetes.yaml"):
            matches.append(path)
    return matches


def find_readme(tree_paths: List[str]) -> Optional[str]:
    """The best README candidate at the repository root, if any."""
    candidates = [p for p in tree_paths if "/" not in p and _README_PATTERN.match(p)]
    return sorted(candidates, key=len)[0] if candidates else None


def find_gitlab_ci_config(tree_paths: List[str]) -> Optional[str]:
    """The `.gitlab-ci.yml` path at the repository root, if present."""
    return next((p for p in tree_paths if "/" not in p and _GITLAB_CI_PATTERN.match(p)), None)


def detect_tech_stack_from_tree(tree_paths: List[str]) -> List[str]:
    """Best-effort technology stack, inferred only from file/marker names actually present
    in the repository tree - no guessing beyond what's observable."""
    detected = set()
    for path in tree_paths:
        base = path.rsplit("/", 1)[-1].lower()
        if base in _MARKER_FILE_TECH:
            detected.add(_MARKER_FILE_TECH[base])
        for ext, tech in _EXTENSION_TECH.items():
            if base.endswith(ext):
                detected.add(tech)
                break
    return sorted(detected)


def parse_k8s_manifest_summary(content: str) -> List[Dict[str, str]]:
    """Extract {kind, name} for each YAML document in a manifest's raw text, tolerating
    parse failures (returns what could be parsed, never raises)."""
    try:
        import yaml
        docs = [d for d in yaml.safe_load_all(content) if isinstance(d, dict)]
    except Exception:
        return []

    summary = []
    for doc in docs:
        kind = doc.get("kind")
        name = ((doc.get("metadata") or {}).get("name")) if isinstance(doc.get("metadata"), dict) else None
        if kind:
            summary.append({"kind": kind, "name": name or "Unknown"})
    return summary
