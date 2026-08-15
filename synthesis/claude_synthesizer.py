"""Claude Sonnet cross-system investigation synthesis (Task 17/18).

Final stage of the investigation pipeline: takes the CrewAI domain investigation
reports (agents/crew - one per involved system, each deliberately scoped to only its
own evidence) plus all raw evidence collected by the specialist agents
(agents/orchestrator) and the user's original query, and produces ONE correlated,
evidence-grounded investigation report. Cross-system correlation and a final root
cause are only ever produced here - no CrewAI agent and no LangGraph node does this.

Every user message reaches this module, even ones with no matched capability or no
selected resource - Claude decides how to respond (a normal reply, or a request for
the specific missing information), never a hardcoded string. See the system prompt
below for how that's constrained.
"""
import json
import logging
from typing import Any, Dict, List, Optional

import anthropic

from config import Config
from synthesis.markdown import render_markdown_report
from synthesis.schemas import FinalInvestigationReport

logger = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS = 40000  # keep the prompt bounded regardless of how much evidence was collected
_MAX_TOKENS = 16000
_MAX_HISTORY_TURNS = 6

_SYSTEM_PROMPT = """You are the final investigation engine and conversational front-end for an
AIOps platform covering Azure infrastructure, GitLab CI/CD, AKS, monitoring/observability, and
FinOps. For every message, you receive:
1. Domain Investigation Reports - independent, per-system analyses (Azure Infrastructure, GitLab
   DevOps, AKS Investigation, Monitoring & Observability, FinOps), each already identifying
   findings, a domain-scoped probable cause, supporting evidence, a confidence score, and
   domain-specific recommended actions. Each of those reports was deliberately kept blind to
   every other domain's evidence, so none of them could correlate across systems. This may be
   empty - not every message needs an investigation.
2. All collected raw evidence the domain reports were built from (also may be empty), plus
   whether a resource is currently selected in the UI.
3. Recent conversation history, for follow-up questions.
4. The user's current message.

When there ARE domain reports and evidence: your job is what none of the domain reports were
allowed to do - correlate evidence ACROSS systems and produce ONE final, cross-system
investigation report.

When there are NO domain reports (a greeting, a general question, or an infra/ops question with
no resource selected or no matching system): do not fabricate an investigation. Put your entire
reply in executive_summary as a normal conversational response, leave root_cause as "Not
applicable", leave incident_timeline/supporting_evidence/eliminated_possibilities/resolution_plan
empty, and set root_cause_confidence to 0. If the message clearly wants an investigation but is
missing something you'd need (e.g. no resource selected, or which project/cluster/resource
they mean), set evidence_sufficient to false and use insufficiency_notes to ask for that specific
missing information - do not guess which resource, project, or system they mean.

Non-negotiable rules:
- Never invent a fact, timestamp, resource name, pipeline/job name, configuration key, file path,
  or log line that is not present in the evidence given to you. If you are not sure, say so - do
  not fill gaps with plausible-sounding detail.
- If the evidence is insufficient to support a confident root cause, set evidence_sufficient to
  false and explain exactly what is missing in insufficiency_notes. A correct "we don't know yet
  because X wasn't collected, please provide it" beats a confident guess.
- Every conclusion (the root cause, each timeline event, each resolution step) must reference the
  specific evidence that supports it via evidence_ids, pointing at entries you create in
  supporting_evidence. If a conclusion has no supporting evidence behind it, do not state it as
  fact - move it to eliminated_possibilities or insufficiency_notes instead.
- The resolution plan must be precise: name the exact resource, pipeline/job, configuration key,
  and file (whichever apply) - not general advice - and explain why fixing that specific thing
  addresses the root cause you identified, not just what to do.
- Do not repeat a domain agent's finding verbatim as your own conclusion without checking it
  against the other domains' evidence first - that cross-check is the entire point of this step.
- Use the conversation history for continuity on follow-up questions, but ground every claim in
  the evidence given for THIS message, not assumptions carried over from earlier turns.
"""


class ClaudeSynthesizer:
    """Produces the final response (investigation report or grounded conversational reply)
    for every chatbot message, via Claude Sonnet."""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None):
        self.model = model or Config.ANTHROPIC_MODEL
        self._client = anthropic.Anthropic(api_key=api_key or Config.ANTHROPIC_API_KEY or None)
        self.last_error: Optional[str] = None

    @staticmethod
    def _build_user_message(
        domain_reports: Dict[str, Any], evidence: Dict[str, Any], query: str, resource_id: Optional[str]
    ) -> str:
        domain_reports_json = json.dumps(domain_reports, default=str, indent=2)[:_MAX_CONTEXT_CHARS] if domain_reports else "{}"
        evidence_json = json.dumps(evidence, default=str, indent=2)[:_MAX_CONTEXT_CHARS] if evidence else "{}"
        return (
            f"Resource currently selected in the UI: {resource_id or 'None'}\n\n"
            f"User message: {query or '(empty message)'}\n\n"
            f"Domain Investigation Reports (one per system, each independently produced from "
            f"only its own evidence):\n"
            f"```json\n{domain_reports_json}\n```\n\n"
            f"All collected raw evidence these reports were built from:\n"
            f"```json\n{evidence_json}\n```\n\n"
            f"Respond now, following the rules in your system prompt."
        )

    def _fallback_report(self, query: str, reason: str) -> FinalInvestigationReport:
        """Used only when the Claude API call itself fails (auth/network/parsing) - an
        infrastructure failure, not a canned answer to the user's actual question."""
        return FinalInvestigationReport(
            query=query,
            evidence_sufficient=False,
            insufficiency_notes=[f"Claude could not be reached to answer this: {reason}"],
            executive_summary=(
                "I couldn't reach Claude to analyze this request. Please check the Anthropic API "
                "configuration and try again."
            ),
            root_cause="Not applicable",
            root_cause_explanation="",
            root_cause_confidence=0.0,
            impact_assessment="Not applicable",
        )

    def synthesize(
        self,
        domain_reports: Dict[str, Any],
        evidence: Dict[str, Any],
        query: str = "",
        resource_id: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Produce the final response for one chatbot turn.

        Always calls Claude, even with empty domain_reports/evidence (see the system prompt for
        how it's expected to handle that case) - never short-circuits to a hardcoded reply. Only
        exception: the API call itself failing, which returns an explicit "couldn't reach Claude"
        report rather than raising, consistent with how every other data-layer module in this app
        fails closed instead of crashing the caller.

        Returns {"structured": <dict matching FinalInvestigationReport>, "markdown": <str>}.
        """
        messages: List[Dict[str, str]] = []
        for turn in (history or [])[-_MAX_HISTORY_TURNS:]:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({
            "role": "user",
            "content": self._build_user_message(domain_reports, evidence, query, resource_id),
        })

        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=messages,
                output_format=FinalInvestigationReport,
            )
            report = response.parsed_output
            if report is None:
                raise ValueError(f"Claude did not return a parseable report (stop_reason={response.stop_reason})")
            self.last_error = None
        except Exception as exc:
            logger.error("Claude synthesis failed: %s", exc)
            self.last_error = str(exc)
            report = self._fallback_report(query, str(exc))

        return {"structured": report.model_dump(), "markdown": render_markdown_report(report)}
