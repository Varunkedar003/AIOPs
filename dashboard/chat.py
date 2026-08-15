from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage

from docgen.intent import DocumentationRequest, parse_documentation_request
from docgen.pipeline import generate_documentation
from workflow.docgen_graph import ALL_DOCGEN_STAGES
from workflow.graph import build_graph
from workflow.router import ALL_STAGES
from workflow.state import initial_state

_STAGE_ORDER = ALL_STAGES
_STAGE_LABELS = {"intent": "Intent", "agents": "Agents", "claude": "Claude", "complete": "Complete"}
_DOCGEN_STAGE_LABELS = {
    "discover": "Discover", "collect": "Collect", "agents": "Agents",
    "claude": "Claude", "export": "Export", "complete": "Complete",
}
_DOWNLOAD_FORMATS = {
    "markdown": ("Markdown", "text/markdown"),
    "docx": ("DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pdf": ("PDF", "application/pdf"),
}


def get_shared_graph():
    """The one compiled LangGraph pipeline for this session (Intent -> Agents -> Claude ->
    Complete). Public so other UI surfaces (e.g. dashboard/graph_investigation.py, for
    click-to-investigate on the topology graphs) reuse the exact same graph/session instead
    of building a second one - same LangGraph, same CrewAI agents, same Claude Sonnet call."""
    if "workflow_graph" not in st.session_state:
        st.session_state.workflow_graph = build_graph()
    return st.session_state.workflow_graph


def get_shared_agent_state():
    """The one AgentState for this session, shared with any other UI surface that triggers an
    investigation, so graph-click investigations show up in the same conversation/history as
    the AI Copilot chat."""
    if "agent_state" not in st.session_state:
        st.session_state.agent_state = initial_state()
    return st.session_state.agent_state


def render_progress_strip(target, stage: str, stage_order: Optional[List[str]] = None,
                           stage_labels: Optional[Dict[str, str]] = None) -> None:
    """Render a pipeline progress strip on `target` (either `st` itself, or an `st.empty()`
    placeholder, for live updates mid-stream). Defaults to the Intent -> Agents -> Claude ->
    Complete investigation pipeline; pass `stage_order`/`stage_labels` to render a different
    pipeline's stages (e.g. the documentation generator's Discover -> ... -> Complete)."""
    stage_order = stage_order or _STAGE_ORDER
    stage_labels = stage_labels or _STAGE_LABELS
    reached = stage_order.index(stage) if stage in stage_order else -1
    parts = []
    for i, key in enumerate(stage_order):
        if i < reached:
            marker = "✅"
        elif i == reached:
            marker = "🔄"
        else:
            marker = "⬜"
        parts.append(f"{marker} {stage_labels[key]}")
    target.caption(" → ".join(parts))


def _render_docgen_reply(final_state: Dict[str, Any]) -> str:
    """Build the chat reply text for a finished (or failed) documentation-generation run."""
    document = final_state.get("document") or {}
    project_name = document.get("project_name") or final_state.get("project_hint") or "the project"
    output_paths = final_state.get("output_paths") or {}

    if not output_paths:
        error = final_state.get("error") or "No files were generated."
        return f"⚠️ I couldn't generate documentation for **{project_name}**: {error}"

    formats = ", ".join(sorted(key.upper() for key in output_paths if key in _DOWNLOAD_FORMATS))
    lines = [
        f"✅ Documentation generated for **{project_name}**.",
        f"Saved under `GeneratedDocs/{project_name}/` ({formats}). Use the download buttons above the chat to get the files.",
    ]
    notes = document.get("data_completeness_notes") or []
    if notes:
        lines.append("")
        lines.append("**Data completeness notes:**")
        for note in notes[:8]:
            lines.append(f"- {note}")
    return "\n".join(lines)


def _run_documentation_request(state, doc_request: DocumentationRequest, progress_placeholder) -> Dict[str, Any]:
    """Run the documentation-generation LangGraph workflow, updating the progress strip live."""
    final_docgen_state: Dict[str, Any] = {"error": None, "output_paths": {}}
    try:
        for step_state in generate_documentation(doc_request, selected_resource_id=state.get("selected_resource_id")):
            final_docgen_state = step_state
            stage = step_state.get("stage", "idle")
            if stage in ALL_DOCGEN_STAGES:
                render_progress_strip(progress_placeholder, stage, stage_order=ALL_DOCGEN_STAGES, stage_labels=_DOCGEN_STAGE_LABELS)
    except Exception as exc:
        final_docgen_state = {"error": str(exc), "output_paths": {}}
    return final_docgen_state


def _render_docgen_downloads() -> None:
    """Download buttons for the most recently generated documentation, if any (Task 24)."""
    final_state = st.session_state.get("last_docgen")
    if not final_state:
        return
    output_paths = final_state.get("output_paths") or {}
    if not output_paths:
        return

    document = final_state.get("document") or {}
    project_name = document.get("project_name") or "project"

    with st.expander(f"📄 Generated Documentation: {project_name}", expanded=True):
        cols = st.columns(len(_DOWNLOAD_FORMATS))
        for i, (key, (label, mime)) in enumerate(_DOWNLOAD_FORMATS.items()):
            path = output_paths.get(key)
            if not path:
                continue
            try:
                data = Path(path).read_bytes()
            except OSError:
                continue
            cols[i].download_button(
                f"⬇️ {label}", data=data, file_name=Path(path).name, mime=mime,
                key=f"download_{key}_{project_name}",
            )
        extra_files = [p for k, p in output_paths.items() if k not in _DOWNLOAD_FORMATS and p]
        if extra_files:
            st.caption("Also saved: " + ", ".join(f"`{p}`" for p in extra_files))


def render_chat():
    """Render the AI Investigation right panel.

    Every submitted question runs the full LangGraph pipeline (Intent -> Agents -> Claude) via
    `.stream()`, so the progress strip above updates live as each stage completes, instead of
    the UI blocking silently until the whole turn finishes.
    """

    st.markdown("### AI Investigation")

    state = get_shared_agent_state()
    state["selected_resource_id"] = st.session_state.get("selected_resource_id")

    _render_docgen_downloads()

    with st.expander("AI Chat", expanded=True):
        for message in state["messages"]:
            role = "user" if isinstance(message, HumanMessage) else "assistant"
            with st.chat_message(role):
                st.markdown(message.content)

        with st.form("chat_form", clear_on_submit=True):
            question = st.text_area(
                "Ask about Azure, GitLab, AKS, monitoring, cost, or infrastructure...",
                placeholder="e.g. Why is this resource unhealthy? What changed in the last failed pipeline?",
                height=100,
            )
            submitted = st.form_submit_button("Send")

        progress_placeholder = st.empty()

        if submitted and question.strip():
            state["messages"].append(HumanMessage(content=question))

            doc_request = parse_documentation_request(question)
            if doc_request is not None:
                # Documentation-generation requests (Task 24) take a separate LangGraph
                # workflow (workflow/docgen_graph.py) instead of the investigation pipeline
                # below - the investigation code path is otherwise completely untouched.
                final_docgen_state = _run_documentation_request(state, doc_request, progress_placeholder)
                state["messages"].append(AIMessage(content=_render_docgen_reply(final_docgen_state)))
                st.session_state.agent_state = state
                st.session_state.last_docgen = final_docgen_state
                st.rerun()
            else:
                final_state = state
                try:
                    for step_state in get_shared_graph().stream(state, stream_mode="values"):
                        final_state = step_state
                        stage = (step_state.get("investigation") or {}).get("stage", "idle")
                        if stage in _STAGE_ORDER:
                            render_progress_strip(progress_placeholder, stage)
                except Exception as exc:
                    final_state = state
                    final_state["messages"].append(AIMessage(content=f"Something went wrong: {exc}"))

                st.session_state.agent_state = final_state
                st.rerun()

    investigation = state.get("investigation") or {}
    stage = investigation.get("stage", "idle")

    # Investigation Progress
    with st.expander("Investigation Progress", expanded=False):
        if stage == "idle":
            st.markdown("_No investigation has run yet - ask a question above._")
        else:
            render_progress_strip(st, stage if stage in _STAGE_ORDER else "complete")
            if investigation.get("resource_name"):
                st.caption(f"Resource: **{investigation['resource_name']}**")
            if investigation.get("capabilities"):
                st.caption(f"Matched domains: {', '.join(investigation['capabilities'])}")
            timeline = investigation.get("timeline") or []
            if timeline:
                st.markdown("**Steps:**")
                for entry in timeline[-12:]:
                    st.markdown(f"- {entry}")
            if investigation.get("error"):
                st.error(investigation["error"])

    # Evidence Sources
    with st.expander("Evidence Sources"):
        evidence_sources = investigation.get("evidence_sources") or []
        domain_reports = investigation.get("domain_reports") or {}
        final_report = investigation.get("final_report") or {}

        if not evidence_sources and not domain_reports:
            st.markdown("_No evidence has been collected yet._")
        else:
            if evidence_sources:
                st.markdown(f"**Specialist agents consulted:** {', '.join(evidence_sources)}")

            for domain, report in domain_reports.items():
                status = report.get("status", "unknown")
                confidence = report.get("confidence_score", 0.0)
                with st.container(border=True):
                    st.markdown(f"**{domain.replace('_', ' ').title()}** — {status} (confidence: {confidence:.0%})")
                    if report.get("root_cause"):
                        st.caption(f"Domain finding: {report['root_cause']}")

            cited_evidence = final_report.get("supporting_evidence") or []
            if cited_evidence:
                st.markdown("**Evidence Claude cited in its final answer:**")
                for item in cited_evidence:
                    st.markdown(f"- **[{item.get('id')}]** ({item.get('domain')}) {item.get('description')} — _{item.get('source')}_")

    # Final Report (structured JSON, for the UI / debugging)
    with st.expander("Final Report (JSON)"):
        if investigation.get("final_report"):
            st.json(investigation["final_report"])
        else:
            st.markdown("_No completed investigation yet._")
