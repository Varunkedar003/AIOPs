"""Pure, network-free helpers for Git change intelligence (Task 16 - Git Change Intelligence).

Kept separate from providers/gitlab/client.py (which does the live API calls) so file-status
classification and the changed-files/failed-stage correlation can be exercised without any
GitLab credentials. Nothing here draws a root-cause conclusion - `correlate_related_files` only
groups files by structural facts (path pattern, shared module name, stage-keyword match) and
records *why* each grouping was made, never *whether* it caused anything.
"""
import os
from typing import Any, Dict, List, Optional

_TEST_PATH_MARKERS = ("test", "tests", "spec", "specs", "__tests__")
_TEST_FILENAME_MARKERS = (".test.", ".spec.", "_test.", "_spec.", "test_")
_BUILD_CONFIG_FILENAMES = (
    "package.json", "package-lock.json", "yarn.lock", "requirements.txt", "pyproject.toml",
    "poetry.lock", "pom.xml", "build.gradle", "dockerfile", ".gitlab-ci.yml", "gitlab-ci.yml",
    "webpack.config.js", "tsconfig.json", "setup.py", "makefile", "go.mod", "go.sum",
)
_LINT_CONFIG_FILENAMES = (".eslintrc", ".eslintrc.json", ".flake8", ".pylintrc", ".prettierrc")


def classify_diff_entry(diff: Dict[str, Any]) -> Dict[str, Any]:
    """Turn one raw diff entry (old_path/new_path/new_file/deleted_file/renamed_file) into a
    {"path", "status"} pair. `status` is one of "added" | "removed" | "modified" | "renamed"."""
    if diff.get("new_file"):
        status = "added"
    elif diff.get("deleted_file"):
        status = "removed"
    elif diff.get("renamed_file"):
        status = "renamed"
    else:
        status = "modified"

    entry: Dict[str, Any] = {"path": diff.get("new_path") or diff.get("old_path"), "status": status}
    if status == "renamed":
        entry["old_path"] = diff.get("old_path")
    return entry


def _is_test_path(path: str) -> bool:
    normalized = path.lower().replace("\\", "/")
    parts = normalized.split("/")
    if any(marker in parts for marker in _TEST_PATH_MARKERS):
        return True
    basename = parts[-1] if parts else normalized
    return any(marker in basename for marker in _TEST_FILENAME_MARKERS)


def _is_build_config_path(path: str) -> bool:
    return os.path.basename(path).lower() in _BUILD_CONFIG_FILENAMES


def _is_lint_config_path(path: str) -> bool:
    return os.path.basename(path).lower() in _LINT_CONFIG_FILENAMES


def _module_key(path: str) -> str:
    """A rough "same module, different role" key so e.g. 'src/app.js' and 'src/app.test.js'
    (or 'tests/test_app.py' and 'app.py') share a key despite living in different files."""
    directory, basename = os.path.split(path)
    name, _, _ext = basename.rpartition(".")
    name = name or basename
    for marker in (".test", ".spec", "_test", "_spec"):
        if name.endswith(marker):
            name = name[: -len(marker)]
            break
    if name.startswith("test_"):
        name = name[len("test_"):]
    return f"{directory}/{name}".lower()


def correlate_related_files(
    changed_files: List[Dict[str, Any]], failed_stage_name: Optional[str]
) -> List[Dict[str, Any]]:
    """Group changed files that are structurally related to the failed stage, or to each other.

    A file is included when it matches the failed stage's likely concern (tests for a
    test/e2e/unit stage, config/manifest files for a build/deploy stage, lint config for a
    lint/style stage) or shares a module name with another changed file (e.g. a source file and
    its test). Each entry records `reasons` (plain facts, not a causal claim).
    """
    stage_lower = (failed_stage_name or "").lower()
    wants_tests = any(keyword in stage_lower for keyword in ("test", "spec", "unit", "e2e", "integration"))
    wants_build = any(keyword in stage_lower for keyword in ("build", "compile", "package", "deploy", "release"))
    wants_lint = any(keyword in stage_lower for keyword in ("lint", "style", "format"))

    by_module: Dict[str, List[str]] = {}
    for entry in changed_files:
        by_module.setdefault(_module_key(entry["path"]), []).append(entry["path"])

    related: List[Dict[str, Any]] = []
    for entry in changed_files:
        path = entry["path"]
        reasons: List[str] = []

        if wants_tests and _is_test_path(path):
            reasons.append(f"test-related path, matches the failed stage '{failed_stage_name}'")
        if wants_build and _is_build_config_path(path):
            reasons.append(f"build/dependency manifest, matches the failed stage '{failed_stage_name}'")
        if wants_lint and _is_lint_config_path(path):
            reasons.append(f"lint/format config, matches the failed stage '{failed_stage_name}'")

        siblings = [p for p in by_module.get(_module_key(path), []) if p != path]
        if siblings:
            reasons.append(f"same module as: {', '.join(siblings)}")

        if reasons:
            related.append({"path": path, "status": entry["status"], "reasons": reasons})

    return related
