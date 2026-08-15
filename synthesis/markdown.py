"""Deterministic Markdown rendering for a FinalInvestigationReport.

Kept separate from the LLM call so the chatbot-facing Markdown and the UI-facing JSON
are always in sync - both come from the exact same structured report Claude produced,
not two independent generations that could drift or contradict each other.
"""
from typing import List

from synthesis.schemas import FinalInvestigationReport


def _evidence_refs(evidence_ids: List[str]) -> str:
    return f" _(evidence: {', '.join(evidence_ids)})_" if evidence_ids else ""


def _is_conversational(report: FinalInvestigationReport) -> bool:
    """True when Claude had nothing to investigate (greeting, general question, or a request
    for missing information) - the full 10-section template would just be empty noise."""
    return (
        report.root_cause.strip().lower() in ("", "not applicable", "n/a")
        and not report.incident_timeline
        and not report.supporting_evidence
        and not report.resolution_plan
        and not report.cross_system_correlation
        and not report.eliminated_possibilities
    )


def render_markdown_report(report: FinalInvestigationReport) -> str:
    """Render Claude's response as Markdown for the chatbot.

    A genuine investigation gets the full 10-section report; a conversational reply or a
    request for missing information (see `_is_conversational`) is just the plain text Claude
    wrote, optionally followed by what it needs from the user - never the empty template.
    """
    if _is_conversational(report):
        lines: List[str] = [report.executive_summary or "_No response generated._"]
        if not report.evidence_sufficient and report.insufficiency_notes:
            lines.append("")
            for note in report.insufficiency_notes:
                lines.append(f"- {note}")
        return "\n".join(lines)

    lines: List[str] = ["# Investigation Report"]
    if report.query:
        lines.append(f"**Question:** {report.query}")
    lines.append("")

    lines.append("## 1. Executive Summary")
    lines.append(report.executive_summary or "_No summary available._")
    lines.append("")

    lines.append("## 2. Incident Timeline")
    if report.incident_timeline:
        for event in report.incident_timeline:
            timestamp = event.timestamp or "Unknown time"
            lines.append(f"- **{timestamp}** [{event.domain}] {event.event}{_evidence_refs(event.evidence_ids)}")
    else:
        lines.append("_No timeline could be reconstructed from the available evidence._")
    lines.append("")

    lines.append("## 3. Detailed Root Cause Analysis")
    lines.append(f"**Root cause:** {report.root_cause}")
    if report.root_cause_explanation:
        lines.append("")
        lines.append(report.root_cause_explanation)
    lines.append("")
    lines.append(f"**Confidence:** {report.root_cause_confidence:.0%}{_evidence_refs(report.root_cause_evidence_ids)}")
    lines.append("")

    lines.append("## 4. Cross-System Correlation")
    if report.cross_system_correlation:
        for item in report.cross_system_correlation:
            lines.append(f"- {item}")
    else:
        lines.append("_No cross-system correlation was found or applicable._")
    lines.append("")

    lines.append("## 5. Supporting Evidence")
    if report.supporting_evidence:
        for item in report.supporting_evidence:
            lines.append(f"- **[{item.id}]** ({item.domain}) {item.description} — _source: {item.source}_")
    else:
        lines.append("_No supporting evidence was recorded._")
    lines.append("")

    lines.append("## 6. Eliminated Possibilities")
    if report.eliminated_possibilities:
        for item in report.eliminated_possibilities:
            lines.append(f"- {item}")
    else:
        lines.append("_None recorded._")
    lines.append("")

    lines.append("## 7. Impact Assessment")
    lines.append(report.impact_assessment or "_Not assessed._")
    lines.append("")

    lines.append("## 8. Precise Resolution Plan")
    if report.resolution_plan:
        for i, step in enumerate(report.resolution_plan, 1):
            target_bits = []
            if step.resource:
                target_bits.append(f"resource `{step.resource}`")
            if step.pipeline_or_job:
                target_bits.append(f"pipeline/job `{step.pipeline_or_job}`")
            if step.configuration:
                target_bits.append(f"configuration `{step.configuration}`")
            if step.file:
                target_bits.append(f"file `{step.file}`")
            target = f" ({', '.join(target_bits)})" if target_bits else ""
            lines.append(f"{i}. {step.step}{target}{_evidence_refs(step.evidence_ids)}")
            lines.append(f"   - Why this resolves it: {step.rationale}")
    else:
        lines.append("_No resolution plan could be produced from the available evidence._")
    lines.append("")

    lines.append("## 9. Verification Steps")
    if report.verification_steps:
        for i, item in enumerate(report.verification_steps, 1):
            lines.append(f"{i}. {item}")
    else:
        lines.append("_None recorded._")
    lines.append("")

    lines.append("## 10. Prevention Recommendations")
    if report.prevention_recommendations:
        for item in report.prevention_recommendations:
            lines.append(f"- {item}")
    else:
        lines.append("_None recorded._")

    if not report.evidence_sufficient:
        lines.append("")
        lines.append("---")
        lines.append("**⚠️ Evidence insufficient for a fully confident conclusion:**")
        for note in report.insufficiency_notes:
            lines.append(f"- {note}")

    return "\n".join(lines)
