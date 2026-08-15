"""Structured output contract for the CrewAI domain-investigation layer.

Every domain agent (Azure Infrastructure, GitLab DevOps, AKS Investigation,
Monitoring & Observability, FinOps) returns exactly this shape, so LangGraph
can merge them into one dict (domain -> report) without any domain-specific
handling, and hand that merged dict to Claude Sonnet later for cross-system
synthesis.
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class DomainInvestigationReport(BaseModel):
    """One domain's investigation report - findings, a domain-scoped probable cause, evidence,
    confidence, and recommended actions. Never a cross-system verdict: `root_cause` is whatever
    this domain's own evidence supports, including "not this domain" when applicable. The final,
    overall root cause across multiple systems is produced later, downstream, by Claude Sonnet.
    """

    domain: str
    resource_id: str
    status: str = "analyzed"  # "analyzed" | "no_evidence" | "error"
    findings: List[str] = Field(default_factory=list)
    root_cause: Optional[str] = None
    eliminated_possibilities: List[str] = Field(default_factory=list)
    supporting_evidence: List[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    recommended_actions: List[str] = Field(default_factory=list)
    error: Optional[str] = None
