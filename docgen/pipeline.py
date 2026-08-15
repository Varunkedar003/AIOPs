"""Top-level entry point for the documentation generator (Task 24).

Ties together intent parsing, project-hint resolution against the currently selected
resource, and the LangGraph documentation workflow (workflow/docgen_graph.py). This is the
one function dashboard/chat.py calls - it owns no investigation logic itself, only wiring.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

from docgen.discovery import ProjectDiscovery
from docgen.intent import DocumentationRequest
from workflow.docgen_graph import build_docgen_graph
from workflow.docgen_state import new_docgen_state


def resolve_project_hint(request: DocumentationRequest, selected_resource_id: Optional[str],
                          discovery: Optional[ProjectDiscovery] = None) -> Optional[str]:
    """The project name to document: whatever was named in the message, else a best-effort
    guess from the currently selected resource, else None (caller must ask the user)."""
    if request.project_hint:
        return request.project_hint

    discovery = discovery or ProjectDiscovery()
    return discovery.resolve_hint_from_resource(selected_resource_id)


def generate_documentation(
    request: DocumentationRequest,
    selected_resource_id: Optional[str] = None,
    graph=None,
) -> Iterator[Dict[str, Any]]:
    """Run the documentation-generation LangGraph workflow, yielding the state after each
    stage (Discover -> Collect -> Agents -> Claude -> Export -> Complete) for a live progress
    UI, exactly like dashboard/chat.py streams the investigation graph.
    """
    project_hint = resolve_project_hint(request, selected_resource_id)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    state = new_docgen_state(
        request_message=request.raw_message,
        project_hint=project_hint,
        focus=request.focus,
        generated_at=generated_at,
    )

    compiled_graph = graph or build_docgen_graph()
    for step_state in compiled_graph.stream(state, stream_mode="values"):
        yield step_state
