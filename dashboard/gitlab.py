import streamlit as st
from typing import Any, Dict, List, Optional

from synthesis.claude_synthesizer import ClaudeSynthesizer


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

    search_query = st.text_input(
        "Search projects", value="", placeholder="Filter by name or path...", key="gitlab_project_search"
    ).strip().lower()

    # Filters the already-fetched (and provider-cached) project list in memory - never
    # re-fetches from GitLab, so typing in the search box can't trigger repeated API calls.
    filtered_projects = (
        [
            project for project in projects
            if search_query in (project.get("name") or "").lower()
            or search_query in (project.get("path") or "").lower()
        ]
        if search_query else projects
    )
    if not filtered_projects:
        st.info(f"No projects match '{search_query}'.")
        return None

    options = {project["name"]: project for project in filtered_projects}
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

    selected_name = st.selectbox("Project", options=names, index=default_index, key="gitlab_project_selector")
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


def render_commits_tab(resource_service: Any, project_id: str) -> None:
    commits = resource_service.get_project_recent_commits(project_id)
    if not commits:
        st.info("No commit data available.")
        return

    rows = [
        {
            "SHA": commit.get("short_id") or (commit.get("commit_id") or "")[:8],
            "Author": commit.get("author_name") or "Unknown author",
            "Message": commit.get("title") or commit.get("message") or "",
            "Date": commit.get("committed_date") or commit.get("authored_date"),
        }
        for commit in commits
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("##### Investigate a Commit")
    options = {
        f"{(c.get('short_id') or (c.get('commit_id') or '')[:8])} — {c.get('title') or c.get('message') or ''}": c
        for c in commits
    }
    selected_key = st.selectbox("Select a commit", options=list(options.keys()), key=f"gitlab_commit_select_{project_id}")
    selected_commit = options[selected_key]

    if selected_commit.get("web_url"):
        st.markdown(f"[Open commit in GitLab]({selected_commit['web_url']})")

    diff = resource_service.get_commit_diff(project_id, selected_commit["commit_id"])
    if not diff:
        st.info("No changed files for this commit.")
        return

    st.caption(f"{len(diff)} file(s) changed in this commit:")
    diff_rows = [
        {
            "File": d.get("new_path") or d.get("old_path"),
            "New": "Yes" if d.get("new_file") else "",
            "Deleted": "Yes" if d.get("deleted_file") else "",
            "Renamed": "Yes" if d.get("renamed_file") else "",
        }
        for d in diff
    ]
    st.dataframe(diff_rows, use_container_width=True, hide_index=True)
    for d in diff:
        file_label = d.get("new_path") or d.get("old_path")
        if d.get("diff"):
            with st.expander(f"Diff: {file_label}"):
                st.code(d["diff"], language="diff")


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


def render_merge_requests_tab(resource_service: Any, project_id: str) -> None:
    merge_requests = resource_service.get_project_merge_requests(project_id)
    if not merge_requests:
        st.info("No open merge requests.")
        return

    rows = [
        {
            "IID": f"!{mr.get('id')}",
            "Title": mr.get("title"),
            "Author": mr.get("author") or "Unknown",
            "State": mr.get("state"),
            "Source Branch": mr.get("source_branch"),
            "Target Branch": mr.get("target_branch"),
            "Created": mr.get("created_at"),
        }
        for mr in merge_requests
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


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

    st.markdown("---")
    render_advanced_rca(resource_service, project_id, report)


def _get_claude_synthesizer() -> ClaudeSynthesizer:
    """Reuses the same ClaudeSynthesizer the chatbot's synthesis stage uses (see
    synthesis/claude_synthesizer.py / workflow/graph.py's NODE_COMPLETE) - cached in
    session_state so this page doesn't construct a new Anthropic client on every rerun."""
    if "gitlab_claude_synthesizer" not in st.session_state:
        st.session_state["gitlab_claude_synthesizer"] = ClaudeSynthesizer()
    return st.session_state["gitlab_claude_synthesizer"]


def render_advanced_rca(resource_service: Any, project_id: str, basic_report: Dict[str, Any]) -> None:
    """Advanced AI root cause analysis for the failed pipeline identified above.

    Combines three existing, already-implemented evidence sources - the basic
    failure/comparison report (`investigate_pipeline_failure`), the deep pipeline evidence
    (`investigate_pipeline`: failure point, error message, stack trace, artifacts, same-job
    comparison - Task 15), and the deep git-change evidence (`investigate_git_changes`:
    triggering/previous-successful commit, diff, linked MR with approvals/comments - Task 16)
    - then feeds all of it, unmodified, into the same Claude Sonnet synthesis stage the
    chatbot uses. No evidence is invented here; Claude only ever sees facts already collected
    by the existing GitLab client methods, and is instructed (see its system prompt) to say so
    explicitly when the evidence doesn't support a confident conclusion.
    """
    failed_pipeline = (basic_report or {}).get("failed_pipeline") or {}
    if not failed_pipeline:
        return

    pipeline_id = failed_pipeline.get("id")
    st.markdown("##### Advanced Root Cause Analysis (AI)")
    st.caption(
        "Combines deep pipeline evidence (failure point, error message, stack trace, artifacts), "
        "git-change evidence (commit diff, linked MR with approvals/comments), and the failure "
        "summary above, then asks Claude Sonnet to correlate them into one root cause and "
        "resolution plan - grounded only in the evidence collected."
    )

    state_key = f"gitlab_advanced_rca_{project_id}_{pipeline_id}"
    if st.button("Run Advanced AI Root Cause Analysis", key=f"gitlab_rca_btn_{project_id}_{pipeline_id}"):
        with st.spinner("Gathering deep pipeline/git evidence and running Claude synthesis..."):
            pipeline_investigation = resource_service.investigate_pipeline(project_id, pipeline_id)
            git_changes = resource_service.investigate_git_changes(project_id, pipeline_id)
            synthesizer = _get_claude_synthesizer()
            outcome = synthesizer.synthesize(
                domain_reports={},
                evidence={
                    "pipeline_failure_summary": basic_report,
                    "pipeline_investigation": pipeline_investigation,
                    "git_changes": git_changes,
                },
                query=f"Perform root cause analysis on the failed pipeline #{pipeline_id} for this project.",
                resource_id=project_id,
            )
            st.session_state[state_key] = {
                "outcome": outcome,
                "pipeline_investigation": pipeline_investigation,
                "git_changes": git_changes,
                "error": synthesizer.last_error,
            }

    result = st.session_state.get(state_key)
    if not result:
        st.info("Click **Run Advanced AI Root Cause Analysis** to correlate the deep evidence above into a root cause.")
        return

    pipeline_investigation = result["pipeline_investigation"]
    git_changes = result["git_changes"]

    st.markdown("###### Affected (from collected evidence)")
    col1, col2, col3 = st.columns(3)
    with col1:
        failed_job = pipeline_investigation.get("failed_job") or {}
        st.markdown("**Job**")
        job_label = failed_job.get("name") or "Unknown"
        if failed_job.get("stage"):
            job_label += f" (stage: {failed_job['stage']})"
        st.markdown(f"`{job_label}`")
    with col2:
        commit = git_changes.get("triggering_commit") or {}
        st.markdown("**Commit**")
        st.markdown(f"`{commit.get('short_id') or 'Unknown'}` {commit.get('title') or ''}")
    with col3:
        mr = git_changes.get("merge_request")
        st.markdown("**Merge Request**")
        st.markdown(f"!{mr.get('id')} {mr.get('title')}" if mr else "None linked")

    if pipeline_investigation.get("error_message"):
        st.markdown("###### Error Message")
        st.code(pipeline_investigation["error_message"], language="text")
    if pipeline_investigation.get("stack_trace"):
        with st.expander("Stack Trace"):
            st.code(pipeline_investigation["stack_trace"], language="text")

    outcome = result["outcome"]
    if result.get("error"):
        st.error(f"Claude synthesis failed: {result['error']}")

    st.markdown("---")
    st.markdown(outcome["markdown"])
