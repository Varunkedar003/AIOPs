import streamlit as st

from dashboard.gitlab import (
    render_project_selector,
    render_project_overview,
    render_latest_commit,
    render_branches_tab,
    render_commits_tab,
    render_merge_requests_tab,
    render_pipelines_tab,
    render_pipeline_detail,
    render_investigation_tab,
)


def render_gitlab_workspace() -> None:
    """GitLab page: live projects, branches, pipelines, stages, jobs, and the latest commit."""
    st.markdown("## GitLab")
    st.caption(
        "Live GitLab data (read-only): projects, branches, commits, merge requests, pipelines, "
        "stages, jobs, and AI-assisted pipeline investigation."
    )

    resource_service = st.session_state.resource_service

    preselect_hint = None
    selected_resource_id = st.session_state.get("selected_resource_id")
    if selected_resource_id:
        resource_details = resource_service.get_resource_details(selected_resource_id)
        preselect_hint = (resource_details or {}).get("name")

    project = render_project_selector(resource_service, preselect_hint=preselect_hint)
    if not project:
        return

    project_id = project["id"]
    render_project_overview(project)
    render_latest_commit(resource_service, project_id)
    st.markdown("---")

    tab_branches, tab_commits, tab_merge_requests, tab_pipelines, tab_investigation = st.tabs(
        ["Branches", "Commits", "Merge Requests", "Pipelines", "Investigation"]
    )

    with tab_branches:
        render_branches_tab(resource_service, project_id)

    with tab_commits:
        render_commits_tab(resource_service, project_id)

    with tab_merge_requests:
        render_merge_requests_tab(resource_service, project_id)

    with tab_pipelines:
        pipelines = render_pipelines_tab(resource_service, project_id)
        st.markdown("---")
        render_pipeline_detail(resource_service, project_id, pipelines)

    with tab_investigation:
        render_investigation_tab(resource_service, project_id)
