"""Claude Sonnet documentation synthesis (Task 24).

Takes the collected live evidence (docgen/collector.py) plus the CrewAI domain reports
(agents/crew - reused, unmodified) and produces one structured ProjectDocumentation via the
exact same `anthropic.Anthropic().messages.parse(output_format=...)` pattern already used in
synthesis/claude_synthesizer.py. No investigation logic lives here - this only turns already
-collected facts into readable technical documentation.
"""
import json
import logging
from typing import Any, Dict, List, Optional

import anthropic

from config import Config
from docgen.schemas import ProjectDocumentation

logger = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS = 60000  # documentation evidence (repo tree, manifests, etc.) runs larger than investigation evidence
_MAX_TOKENS = 16000

_SYSTEM_PROMPT = """You are a technical writer generating a project documentation package for an
AIOps platform covering Azure infrastructure, GitLab CI/CD, AKS, monitoring/observability, and
FinOps. You receive:
1. Live evidence collected from GitLab (project info, repository structure, README, tech stack,
   Dockerfile/Helm/Kubernetes manifests, .gitlab-ci.yml, pipeline stages/jobs, environments,
   merge requests, recent commits) and from Azure/AKS/Monitoring/Cost providers (resource
   inventory, AKS workloads, networking, managed identities, alerts, health, cost).
2. CrewAI domain analysis reports (one per system that had evidence), each already flagging
   findings and recommended actions for its own domain only.
3. Discovery notes listing anything that could NOT be automatically found or correlated (e.g.
   "no Azure resources could be correlated with this project").

Write exactly the 19 sections defined by the output schema: Executive Summary, Project
Overview, Architecture Overview, Technology Stack, Repository Structure, Azure Infrastructure,
AKS Deployment, App Service Deployment, CI/CD Pipeline, Configuration, Networking,
Monitoring & Logging, Security Overview, Resource Inventory Summary, Deployment Flow,
Dependencies, Troubleshooting Guide, AI Recommendations, and Appendix.

Non-negotiable rules:
- NEVER include a secret VALUE of any kind (connection strings, keys, passwords, tokens,
  certificates). The evidence you are given never contains secret values, only metadata (e.g.
  Kubernetes Secret names and types, Key Vault names and access-policy counts) - document only
  that metadata, and if asked to describe a secret, describe its name/purpose/type only.
- Use ONLY the live data given to you. Never invent a resource, endpoint, version, file path,
  pipeline stage, environment, cost figure, or configuration value that isn't present in the
  evidence.
- If a section has no supporting evidence (e.g. no AKS cluster was correlated, so there is
  nothing to write for "AKS Deployment"), say so explicitly in that section - e.g. "No AKS
  cluster could be automatically correlated with this project; this section is not applicable."
  Never fabricate content to fill a section, and never silently omit a section from the output.
- The Configuration and Security Overview sections must describe secrets/configuration only by
  name, type, and purpose - never by value.
- Resource Inventory Summary should be a short narrative summary (the detailed table is
  rendered separately, deterministically, from the same evidence - do not reproduce it here).
- Troubleshooting Guide should be built from real signals in the evidence (failed pipeline
  stages, active alerts, unhealthy deployments) - if nothing indicates a problem, say the
  evidence shows no active issues rather than inventing generic troubleshooting advice.
- AI Recommendations must be concrete and reference the specific resource/pipeline/
  configuration they apply to, grounded in the evidence and domain reports given.
- data_completeness_notes must list every specific gap you relied on the discovery notes for,
  plus anything else you could not document due to missing evidence - do not leave this empty
  if any domain had `"found": false`.
"""


class DocumentationSynthesizer:
    """Produces the structured ProjectDocumentation for one documentation request, via Claude Sonnet."""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None):
        self.model = model or Config.ANTHROPIC_MODEL
        self._client = anthropic.Anthropic(api_key=api_key or Config.ANTHROPIC_API_KEY or None)
        self.last_error: Optional[str] = None

    @staticmethod
    def _build_user_message(
        project_name: str,
        evidence: Dict[str, Any],
        domain_reports: Dict[str, Any],
        discovery_notes: List[str],
        focus: Optional[str],
    ) -> str:
        evidence_json = json.dumps(evidence, default=str, indent=2)[:_MAX_CONTEXT_CHARS]
        domain_reports_json = json.dumps(domain_reports, default=str, indent=2)[:_MAX_CONTEXT_CHARS] if domain_reports else "{}"
        focus_line = f"\nThe user specifically asked for {focus} documentation - give that section(s) the most depth, but still fill in every section.\n" if focus else ""

        return (
            f"Project: {project_name}\n"
            f"{focus_line}\n"
            f"Discovery notes (things that could NOT be automatically found/correlated - "
            f"reflect these in data_completeness_notes and the relevant sections):\n"
            f"{json.dumps(discovery_notes)}\n\n"
            f"CrewAI domain analysis reports:\n```json\n{domain_reports_json}\n```\n\n"
            f"Collected live evidence:\n```json\n{evidence_json}\n```\n\n"
            f"Write the full ProjectDocumentation now, following every rule in your system prompt."
        )

    def _fallback_document(self, project_name: str, reason: str) -> ProjectDocumentation:
        """Used only when the Claude API call itself fails - an infrastructure failure, not a
        canned answer, consistent with ClaudeSynthesizer's fallback behavior."""
        message = f"Documentation could not be generated: Claude could not be reached ({reason})."
        return ProjectDocumentation(
            project_name=project_name,
            executive_summary=message,
            project_overview="Not available.",
            architecture_overview="Not available.",
            technology_stack="Not available.",
            repository_structure="Not available.",
            azure_infrastructure="Not available.",
            aks_deployment="Not available.",
            app_service_deployment="Not available.",
            cicd_pipeline="Not available.",
            configuration="Not available.",
            networking="Not available.",
            monitoring_logging="Not available.",
            security_overview="Not available.",
            resource_inventory_summary="Not available.",
            deployment_flow="Not available.",
            dependencies="Not available.",
            troubleshooting_guide="Not available.",
            appendix="Not available.",
            data_completeness_notes=[message],
        )

    def synthesize(
        self,
        project_name: str,
        evidence: Dict[str, Any],
        domain_reports: Optional[Dict[str, Any]] = None,
        discovery_notes: Optional[List[str]] = None,
        focus: Optional[str] = None,
    ) -> ProjectDocumentation:
        """Produce the structured documentation for one project. Never raises: an API failure
        degrades to an explicit "could not be generated" document rather than crashing the
        caller, matching every other fail-closed module in this app."""
        message = self._build_user_message(project_name, evidence, domain_reports or {}, discovery_notes or [], focus)

        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": message}],
                output_format=ProjectDocumentation,
            )
            document = response.parsed_output
            if document is None:
                raise ValueError(f"Claude did not return a parseable document (stop_reason={response.stop_reason})")
            self.last_error = None
            return document
        except Exception as exc:
            logger.error("Claude documentation synthesis failed: %s", exc)
            self.last_error = str(exc)
            return self._fallback_document(project_name, str(exc))
