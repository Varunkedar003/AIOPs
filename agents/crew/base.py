"""Base class for CrewAI domain-investigation agents.

Evidence collection already happened upstream (providers/*.py + agents/*_agent.py,
orchestrated by agents/orchestrator.py). Each agent built from this base is purely
the ANALYSIS layer on top of one domain's already-collected evidence: it interprets
that evidence, flags anomalies, proposes a domain-scoped probable cause, notes what
it can rule out, and recommends domain-specific next steps.

Structural guarantee, not just a prompting convention: an agent built here is only
ever given its own domain's evidence (see agents/crew/manager.py) - it has no way to
see what other systems found, so it cannot fabricate a cross-system conclusion even
if asked to. That final, overall root cause across multiple systems is produced
later, downstream, by Claude Sonnet, once every domain's report has been collected.

Adding a new domain is: instantiate DomainInvestigationAgent with a role/goal/backstory
(see agents/crew/azure_infrastructure.py for the pattern) and register it in
agents/crew/manager.py's DOMAIN_AGENTS - no other plumbing to touch.
"""
import json
import logging
from typing import Any, Dict, Optional

from crewai import Agent, Crew, LLM, Process, Task

from config import Config
from agents.crew.schemas import DomainInvestigationReport

logger = logging.getLogger(__name__)

# Local models (this app defaults to a 3B Ollama model) are unreliable with function-calling
# based structured output, so the report shape is enforced by instruction + a tolerant JSON
# parser below, rather than CrewAI's output_pydantic (which needs reliable tool-calling).
_REPORT_INSTRUCTIONS = """
You are analyzing evidence for ONE domain only. You do not know what other systems, if any,
are also being investigated, and you must not speculate about them.

Rules:
- Base every finding strictly on the evidence given below - never invent data that isn't there.
- If the evidence shows nothing wrong in this domain, say so plainly; do not force a root cause.
- If the evidence suggests the real cause is likely OUTSIDE this domain, say that explicitly in
  root_cause (e.g. "No anomaly found in this domain; likely caused upstream in another system")
  instead of guessing at what that other domain's problem might be.
- You are NOT producing the final, overall answer for the investigation. A separate cross-system
  reviewer does that after seeing every domain's report - your job is this domain's evidence only.
- confidence_score is a number from 0.0 to 1.0: your confidence that root_cause is correct, given
  only this domain's evidence.
- eliminated_possibilities lists domain-specific causes the evidence rules OUT, not other domains.

Respond with ONLY a JSON object, no other text, no markdown fences, matching exactly this shape:
{
  "findings": ["...", "..."],
  "root_cause": "..." or null,
  "eliminated_possibilities": ["...", "..."],
  "supporting_evidence": ["...", "..."],
  "confidence_score": 0.0,
  "recommended_actions": ["...", "..."]
}
"""

_MAX_EVIDENCE_CHARS = 12000  # keep prompts bounded - small local models degrade on long contexts


def _build_llm() -> LLM:
    return LLM(model=Config.CREWAI_OLLAMA_MODEL, base_url=Config.OLLAMA_BASE_URL, temperature=0.1)


def _parse_report_json(raw: str) -> Dict[str, Any]:
    """Extract the JSON object from an LLM's response, tolerating the code fences and stray
    prose that small local models sometimes wrap around otherwise-valid JSON."""
    text = (raw or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object found in model output: {text[:200]!r}")
    data = json.loads(text[start:end + 1])

    return {
        "findings": [str(item) for item in (data.get("findings") or [])],
        "root_cause": data.get("root_cause") or None,
        "eliminated_possibilities": [str(item) for item in (data.get("eliminated_possibilities") or [])],
        "supporting_evidence": [str(item) for item in (data.get("supporting_evidence") or [])],
        "confidence_score": max(0.0, min(1.0, float(data.get("confidence_score") or 0.0))),
        "recommended_actions": [str(item) for item in (data.get("recommended_actions") or [])],
    }


class DomainInvestigationAgent:
    """One domain's CrewAI investigator: analyzes pre-collected evidence for that domain only."""

    def __init__(self, domain: str, role: str, goal: str, backstory: str, llm: Optional[LLM] = None):
        self.domain = domain
        self.role = role
        self._llm = llm or _build_llm()
        self._agent = Agent(
            role=role,
            goal=goal,
            backstory=backstory,
            llm=self._llm,
            verbose=False,
            allow_delegation=False,
        )

    def _build_task(self, evidence: Dict[str, Any], question: str) -> Task:
        evidence_json = json.dumps(evidence, default=str, indent=2)[:_MAX_EVIDENCE_CHARS]
        description = (
            f"Investigation question: {question or 'Investigate this resource for problems.'}\n\n"
            f"Evidence collected from the {self.domain} system for this resource "
            f"(already gathered - do not ask for more; analyze what's given):\n"
            f"```json\n{evidence_json}\n```\n\n"
            f"{_REPORT_INSTRUCTIONS}"
        )
        return Task(description=description, expected_output="A single JSON object, and nothing else.", agent=self._agent)

    def investigate(self, resource_id: str, evidence: Dict[str, Any], question: str = "") -> DomainInvestigationReport:
        """Analyze this domain's already-collected evidence and produce a structured report.

        Never raises: missing evidence, an unreachable LLM, or malformed model output all degrade
        to a DomainInvestigationReport with status="no_evidence"/"error" - consistent with how
        every data-layer provider in this app fails closed instead of crashing the caller.
        """
        if not evidence or not evidence.get("found", True):
            return DomainInvestigationReport(
                domain=self.domain,
                resource_id=resource_id,
                status="no_evidence",
                findings=["No evidence was collected for this domain, so there is nothing to analyze."],
            )

        try:
            task = self._build_task(evidence, question)
            crew = Crew(agents=[self._agent], tasks=[task], process=Process.sequential, verbose=False, tracing=False)
            raw = crew.kickoff().raw
            parsed = _parse_report_json(raw)
            return DomainInvestigationReport(domain=self.domain, resource_id=resource_id, status="analyzed", **parsed)
        except Exception as exc:
            logger.error("CrewAI investigation failed for domain=%s resource=%s: %s", self.domain, resource_id, exc)
            return DomainInvestigationReport(
                domain=self.domain,
                resource_id=resource_id,
                status="error",
                error=str(exc),
                findings=["Analysis could not be completed for this domain."],
            )
