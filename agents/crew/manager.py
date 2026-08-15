"""Investigation Crew manager.

Runs each domain's CrewAI agent over its own already-collected evidence (the
orchestrator's merged specialist-agent output - see agents.orchestrator.Orchestrator.handle)
and returns one DomainInvestigationReport per domain that had evidence.

Deliberately does NOT correlate across domains: each domain agent only ever receives its
own evidence (see _merge_capability_evidence below), so cross-system correlation is
structurally impossible here, not just discouraged by prompting. The final, overall root
cause, detailed explanation, precise solution, verification steps, and prevention
recommendations are produced later, downstream, by Claude Sonnet from these reports.

Adding a new domain: add one entry to DOMAIN_AGENTS mapping a domain name to a
(DomainInvestigationAgent, capability keys) pair - no other plumbing to touch.
"""
from typing import Any, Dict, Iterable, Optional

from agents.crew.aks_investigation import aks_investigation_agent
from agents.crew.azure_infrastructure import azure_infrastructure_agent
from agents.crew.finops import finops_agent
from agents.crew.gitlab_devops import gitlab_devops_agent
from agents.crew.observability import observability_agent
from agents.crew.schemas import DomainInvestigationReport

# domain name -> (agent, orchestrator capability keys whose evidence feeds it).
# A domain can draw on more than one capability (e.g. GitLab DevOps merges both the
# basic project summary and the deep pipeline-failure investigation into one evidence set).
DOMAIN_AGENTS = {
    "azure_infrastructure": (azure_infrastructure_agent, ("azure_infrastructure",)),
    "gitlab_devops": (gitlab_devops_agent, ("gitlab", "gitlab_investigation")),
    "aks_investigation": (aks_investigation_agent, ("aks",)),
    "observability": (observability_agent, ("observability",)),
    "finops": (finops_agent, ("finops",)),
}


def _merge_capability_evidence(results: Dict[str, Any], capability_keys: Iterable[str]) -> Dict[str, Any]:
    """Combine one or more orchestrator capability results into a single evidence payload."""
    merged: Dict[str, Any] = {}
    found_any = False
    for key in capability_keys:
        result = results.get(key)
        if not result:
            continue
        merged[key] = result
        found_any = found_any or result.get("found", True)
    merged["found"] = found_any
    return merged


class InvestigationCrew:
    """Runs the relevant domain CrewAI agents over already-collected orchestrator evidence."""

    def investigate(
        self,
        resource_id: str,
        results: Dict[str, Any],
        question: str = "",
        domains: Optional[Iterable[str]] = None,
    ) -> Dict[str, DomainInvestigationReport]:
        """Analyze every domain that has evidence in `results` (or only `domains`, if given).

        `results` is exactly what agents.orchestrator.Orchestrator.handle() returns under
        "results": capability name -> that specialist agent's structured output dict.
        """
        reports: Dict[str, DomainInvestigationReport] = {}

        for domain, (agent, capability_keys) in DOMAIN_AGENTS.items():
            if domains is not None and domain not in domains:
                continue
            relevant_keys = [key for key in capability_keys if key in results]
            if not relevant_keys:
                continue

            evidence = _merge_capability_evidence(results, capability_keys)
            reports[domain] = agent.investigate(resource_id, evidence, question)

        return reports
