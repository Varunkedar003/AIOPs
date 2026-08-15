"""LangGraph state for the documentation-generation workflow (Task 24).

Separate from workflow/state.py's AgentState/InvestigationState - the chat investigation
pipeline is untouched by this module.
"""
from typing import Any, Dict, List, Optional, TypedDict

from docgen.discovery import ProjectContext


class DocGenState(TypedDict):
    """Snapshot of one documentation-generation run: Discover -> Collect -> Agents -> Claude ->
    Export -> Complete."""
    request_message: str
    project_hint: Optional[str]
    focus: Optional[str]
    generated_at: str

    stage: str  # "idle" | "discover" | "collect" | "agents" | "claude" | "export" | "complete"
    project_context: Optional[ProjectContext]
    evidence: Dict[str, Any]
    domain_reports: Dict[str, Any]
    document: Optional[Dict[str, Any]]  # ProjectDocumentation.model_dump()
    markdown: Optional[str]
    output_paths: Dict[str, str]
    timeline: List[str]
    error: Optional[str]


def new_docgen_state(request_message: str, project_hint: Optional[str], focus: Optional[str], generated_at: str) -> DocGenState:
    """Build a fresh DocGenState for a new documentation request."""
    return {
        "request_message": request_message,
        "project_hint": project_hint,
        "focus": focus,
        "generated_at": generated_at,
        "stage": "idle",
        "project_context": None,
        "evidence": {},
        "domain_reports": {},
        "document": None,
        "markdown": None,
        "output_paths": {},
        "timeline": [],
        "error": None,
    }
