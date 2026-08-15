"""Structured output contract for the AI-generated project documentation (Task 24).

Produced by docgen/synthesizer.py (Claude Sonnet), from the same "one Pydantic model via
client.messages.parse(output_format=...)" pattern already used in
synthesis/schemas.py/synthesis/claude_synthesizer.py. Diagrams and the resource inventory
table are deliberately NOT part of this schema - those are rendered deterministically from
evidence in docgen/diagrams.py so resource names/edges can never be hallucinated; Claude only
ever writes the narrative sections, grounded in the evidence it was given.
"""
from typing import List

from pydantic import BaseModel, Field


class ProjectDocumentation(BaseModel):
    """The 19 narrative sections of a generated project document, plus AI recommendations
    and an explicit list of anything the evidence didn't cover."""

    project_name: str
    executive_summary: str
    project_overview: str
    architecture_overview: str
    technology_stack: str
    repository_structure: str
    azure_infrastructure: str
    aks_deployment: str
    app_service_deployment: str
    cicd_pipeline: str
    configuration: str
    networking: str
    monitoring_logging: str
    security_overview: str
    resource_inventory_summary: str
    deployment_flow: str
    dependencies: str
    troubleshooting_guide: str
    ai_recommendations: List[str] = Field(default_factory=list)
    appendix: str

    # Explicit "we don't know" list (Task 24: "If information is unavailable, explicitly
    # state it") - populated from both discovery/collection gaps and anything Claude itself
    # flagged as missing while writing the sections above.
    data_completeness_notes: List[str] = Field(default_factory=list)
