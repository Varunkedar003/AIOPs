"""Structured output contract for the final, cross-system investigation report
produced by Claude Sonnet (see synthesis/claude_synthesizer.py).

Consumes the CrewAI domain investigation reports (agents/crew) and the raw evidence
collected by the specialist agents (agents/orchestrator) for a single user query, and
produces exactly one report combining all of it into a single, evidence-grounded
conclusion - the cross-system correlation step none of the domain agents are allowed
to do themselves.
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """One fact used to support a conclusion, traceable back to where it came from."""

    id: str
    domain: str
    description: str
    source: str


class TimelineEvent(BaseModel):
    """One point in the reconstructed incident timeline."""

    timestamp: Optional[str] = None
    event: str
    domain: str
    evidence_ids: List[str] = Field(default_factory=list)


class ResolutionStep(BaseModel):
    """One concrete, actionable step in the resolution plan.

    `resource`/`pipeline_or_job`/`configuration`/`file` identify exactly what the step
    touches (only the applicable ones need be set); `rationale` explains why fixing
    that specific thing resolves the identified root cause.
    """

    step: str
    resource: Optional[str] = None
    pipeline_or_job: Optional[str] = None
    configuration: Optional[str] = None
    file: Optional[str] = None
    rationale: str
    evidence_ids: List[str] = Field(default_factory=list)


class FinalInvestigationReport(BaseModel):
    """The complete, cross-system investigation report Claude Sonnet produces.

    `evidence_sufficient` / `insufficiency_notes` give the model an explicit, structured
    place to say "the evidence doesn't support a conclusion here" instead of inventing
    one. Every claim-bearing section carries `evidence_ids` referencing
    `supporting_evidence`, so no conclusion is unsupported by construction.
    """

    query: str
    evidence_sufficient: bool
    insufficiency_notes: List[str] = Field(default_factory=list)

    executive_summary: str
    incident_timeline: List[TimelineEvent] = Field(default_factory=list)

    root_cause: str
    root_cause_explanation: str
    root_cause_confidence: float = Field(ge=0.0, le=1.0)
    root_cause_evidence_ids: List[str] = Field(default_factory=list)

    cross_system_correlation: List[str] = Field(default_factory=list)
    supporting_evidence: List[EvidenceItem] = Field(default_factory=list)
    eliminated_possibilities: List[str] = Field(default_factory=list)
    impact_assessment: str

    resolution_plan: List[ResolutionStep] = Field(default_factory=list)
    verification_steps: List[str] = Field(default_factory=list)
    prevention_recommendations: List[str] = Field(default_factory=list)
