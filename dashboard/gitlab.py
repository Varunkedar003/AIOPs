import streamlit as st
from typing import Any, Dict, List, Optional


def _status_color(status: str) -> str:
    status_lower = (status or "").lower()
    if status_lower in ("success", "passed"):
        return "green"
    if status_lower in ("running", "pending", "created", "waiting_for_resource"):
        return "orange"
    if status_lower in ("failed", "canceled", "skipped"):
        return "red"
    return "blue"


def render_project_selector(resource_service: Any, preselect_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Project picker; returns the selected project dict, or None if no projects exist.

    `preselect_hint` (typically the currently-selected Azure resource's name) biases the
    default selection toward a GitLab project with a matching name, so picking a resource
    elsewhere in the app carries over here automatically when a reasonable match exists.
    """
    projects = resource_service.get_gitlab_projects()
    if not projects:
        st.info("No GitLab projects available. Check GITLAB_URL / GITLAB_TOKEN in Settings.")
        return None

    options = {project["name"]: project for project in projects}
    names = list(options.keys())

    default_index = 0
    matched_name = None
    if preselect_hint:
        hint_lower = preselect_hint.lower()
        for index, name in enumerate(names):
            if name.lower() in hint_lower or hint_lower in name.lower():
                default_index = index
                matched_name = name
                break

    selected_name = st.selectbox("Project", options=names, index=default_index)
    if matched_name and selected_name == matched_name:
        st.caption(f"Auto-selected based on the selected resource (**{preselect_hint}**).")
    return options[selected_name]


def render_project_overview(project: Dict[str, Any]) -> None:
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**Path**")
        st.markdown(project.get("path") or "Unknown")
    with col2:
        st.markdown("**Default Branch**")
        st.markdown(project.get("default_branch") or "Unknown")
    with col3:
        st.markdown("**Visibility**")
        st.markdown(project.get("visibility") or "Unknown")

    if project.get("web_url"):
        st.markdown(f"[Open in GitLab]({project['web_url']})")


def render_latest_commit(resource_service: Any, project_id: str) -> None:
    st.markdown("#### Latest Commit")
    commit = resource_service.get_project_latest_commit(project_id)
    if not commit:
        st.info("No commit data available.")
        return

    st.markdown(f"`{commit.get('short_id') or (commit.get('commit_id') or '')[:8]}` — {commit.get('title') or commit.get('message') or ''}")
    st.caption(f"{commit.get('author_name') or 'Unknown author'} · {commit.get('committed_date') or 'Unknown date'}")


def render_branches_tab(resource_service: Any, project_id: str) -> None:
    branches = resource_service.get_project_branches(project_id)
    if not branches:
        st.info("No branch data available.")
        return

    rows = [
        {
            "Branch": branch.get("name"),
            "Default": "Yes" if branch.get("default") else "",
            "Protected": "Yes" if branch.get("protected") else "",
            "Latest Commit": (branch.get("commit_id") or "")[:8],
            "Committed": branch.get("committed_date"),
        }
        for branch in branches
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def render_pipelines_tab(resource_service: Any, project_id: str) -> List[Dict[str, Any]]:
    pipelines = resource_service.get_project_pipelines(project_id)
    if not pipelines:
        st.info("No pipeline data available.")
        return []

    failed = [p for p in pipelines if p.get("status") == "failed"]
    if failed:
        st.warning(f"⚠️ {len(failed)} failed pipeline(s).")

    rows = [
        {
            "Pipeline": pipeline.get("id"),
            "Status": pipeline.get("status"),
            "Ref": pipeline.get("ref"),
            "SHA": (pipeline.get("sha") or "")[:8],
            "Created": pipeline.get("created_at"),
        }
        for pipeline in pipelines
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    return pipelines


def render_pipeline_detail(resource_service: Any, project_id: str, pipelines: List[Dict[str, Any]]) -> None:
    if not pipelines:
        st.info("No pipelines to inspect.")
        return

    options = {f"#{pipeline['id']} ({pipeline.get('status', 'unknown')})": pipeline for pipeline in pipelines}
    selected_key = st.selectbox("Pipeline", options=list(options.keys()), key=f"gitlab_pipeline_{project_id}")
    pipeline_id = options[selected_key]["id"]

    st.markdown("##### Stages")
    stages = resource_service.get_pipeline_stages(project_id, pipeline_id)
    if not stages:
        st.info("No stage data available.")
    else:
        failed_stages = [s for s in stages if s.get("status") == "failed"]
        if failed_stages:
            st.warning(f"⚠️ {len(failed_stages)} stage(s) failed.")
        stage_rows = [
            {"Stage": s.get("name"), "Status": s.get("status"), "Jobs": ", ".join(s.get("jobs", []))}
            for s in stages
        ]
        st.dataframe(stage_rows, use_container_width=True, hide_index=True)

    st.markdown("##### Jobs")
    jobs = resource_service.get_pipeline_jobs(project_id, pipeline_id)
    if not jobs:
        st.info("No job data available.")
        return

    failed_jobs = [j for j in jobs if j.get("status") == "failed"]
    if failed_jobs:
        st.warning(f"⚠️ {len(failed_jobs)} failed job(s).")

    job_rows = [
        {
            "Job": job.get("name"),
            "Stage": job.get("stage"),
            "Status": job.get("status"),
            "Duration (s)": job.get("duration"),
        }
        for job in jobs
    ]
    st.dataframe(job_rows, use_container_width=True, hide_index=True)


def render_investigation_tab(resource_service: Any, project_id: str) -> None:
    """Structured, factual report comparing the latest failed pipeline to the last successful one.

    Purely data assembly - no AI reasoning happens here (see Task 14/15 for that layer).
    """
    st.caption(
        "Compares the latest failed pipeline against the last successful one: failed job logs, "
        "artifacts, the failing commit, its merge request, and what changed in between."
    )

    state_key = f"gitlab_investigation_{project_id}"
    if st.button("Run Investigation", key=f"gitlab_investigate_btn_{project_id}"):
        st.session_state[state_key] = resource_service.investigate_pipeline_failure(project_id)

    report = st.session_state.get(state_key)
    if not report:
        st.info("Click **Run Investigation** to analyze the most recent failed pipeline.")
        return

    if not report.get("found"):
        st.success(report.get("message", "No failed pipeline found."))
        return

    failed = report.get("failed_pipeline") or {}
    successful = report.get("successful_pipeline") or {}

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("##### ❌ Failed Pipeline")
        st.markdown(f"**#{failed.get('id')}** — `{failed.get('ref')}` @ `{(failed.get('sha') or '')[:8]}`")
        st.markdown(f"Status: :red[{failed.get('status')}]")
        st.caption(f"Created: {failed.get('created_at')}")
    with col2:
        st.markdown("##### ✅ Last Successful Pipeline")
        if successful:
            st.markdown(f"**#{successful.get('id')}** — `{successful.get('ref')}` @ `{(successful.get('sha') or '')[:8]}`")
            st.markdown(f"Status: :green[{successful.get('status')}]")
            st.caption(f"Created: {successful.get('created_at')}")
        else:
            st.info("No prior successful pipeline found.")

    commit = report.get("commit")
    if commit:
        st.markdown("##### Failing Commit")
        st.markdown(f"`{commit.get('short_id')}` — {commit.get('title') or commit.get('message') or ''}")
        st.caption(f"{commit.get('author_name') or 'Unknown author'} · {commit.get('committed_date') or 'Unknown date'}")

    merge_request = report.get("merge_request")
    if merge_request:
        st.markdown("##### Merge Request")
        st.markdown(f"**!{merge_request.get('id')}** {merge_request.get('title')} ({merge_request.get('state')})")
        st.markdown(f"`{merge_request.get('source_branch')}` → `{merge_request.get('target_branch')}`")
        if merge_request.get("web_url"):
            st.markdown(f"[Open in GitLab]({merge_request['web_url']})")

    comparison = report.get("comparison")
    if comparison:
        st.markdown("##### What Changed")
        commits = comparison.get("commits") or []
        changed_files = comparison.get("changed_files") or []
        if commits:
            st.caption(f"{len(commits)} commit(s) since the last successful pipeline:")
            commit_rows = [
                {
                    "Commit": c.get("short_id"),
                    "Title": c.get("title"),
                    "Author": c.get("author_name"),
                    "Date": c.get("committed_date"),
                }
                for c in commits
            ]
            st.dataframe(commit_rows, use_container_width=True, hide_index=True)
        if changed_files:
            st.caption(f"{len(changed_files)} file(s) changed:")
            st.code("\n".join(changed_files), language="text")
        if not commits and not changed_files:
            st.info("No differences found between the two pipelines' commits.")

    st.markdown("##### Failed Jobs")
    failed_jobs = report.get("failed_jobs") or []
    if not failed_jobs:
        st.info("No failed job details available.")
    else:
        for job in failed_jobs:
            with st.expander(f"❌ {job.get('name')} (stage: {job.get('stage')})"):
                st.caption(f"Duration: {job.get('duration')}s · Finished: {job.get('finished_at')}")
                if job.get("web_url"):
                    st.markdown(f"[Open in GitLab]({job['web_url']})")
                logs = job.get("logs") or ""
                st.code(logs[-8000:], language="text") if logs else st.info("No logs available.")

    st.markdown("##### Artifacts")
    artifacts = report.get("artifacts") or []
    if not artifacts:
        st.info("No artifacts produced.")
    else:
        artifact_rows = [
            {"Job": a.get("job_name"), "File": a.get("filename"), "Size (bytes)": a.get("size")}
            for a in artifacts
        ]
        st.dataframe(artifact_rows, use_container_width=True, hide_index=True)
