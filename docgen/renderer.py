"""Deterministic Markdown rendering for generated project documentation (Task 24).

Kept separate from the LLM call, same principle as synthesis/markdown.py: the Markdown/DOCX/
PDF exports and the structured JSON always come from the exact same ProjectDocumentation
Claude produced, plus the deterministic diagrams/table built from evidence - never a second,
independently-generated rendering that could drift.
"""
from typing import Any, Dict

from docgen.schemas import ProjectDocumentation


def render_documentation_markdown(
    document: ProjectDocumentation,
    dependency_diagram: str,
    deployment_flow_diagram: str,
    resource_inventory_table: str,
    metadata: Dict[str, Any],
) -> str:
    """Render the full documentation package as one Markdown document."""
    lines = [f"# {document.project_name} - Project Documentation"]
    lines.append("")
    lines.append(f"_Generated {metadata.get('generated_at', 'Unknown')} from live Azure/GitLab/AKS data._")
    if metadata.get("requested_by_message"):
        lines.append(f"_Requested via: \"{metadata['requested_by_message']}\"_")
    lines.append("")

    lines.append("## 1. Executive Summary")
    lines.append(document.executive_summary)
    lines.append("")

    lines.append("## 2. Project Overview")
    lines.append(document.project_overview)
    lines.append("")

    lines.append("## 3. Architecture Overview")
    lines.append(document.architecture_overview)
    lines.append("")
    lines.append("### Infrastructure Dependency Diagram")
    lines.append(dependency_diagram)
    lines.append("")

    lines.append("## 4. Technology Stack")
    lines.append(document.technology_stack)
    lines.append("")

    lines.append("## 5. Repository Structure")
    lines.append(document.repository_structure)
    lines.append("")

    lines.append("## 6. Azure Infrastructure")
    lines.append(document.azure_infrastructure)
    lines.append("")

    lines.append("## 7. AKS Deployment")
    lines.append(document.aks_deployment)
    lines.append("")

    lines.append("## 8. App Service Deployment")
    lines.append(document.app_service_deployment)
    lines.append("")

    lines.append("## 9. CI/CD Pipeline")
    lines.append(document.cicd_pipeline)
    lines.append("")
    lines.append("### Deployment Flow Diagram")
    lines.append(deployment_flow_diagram)
    lines.append("")

    lines.append("## 10. Configuration")
    lines.append(document.configuration)
    lines.append("")

    lines.append("## 11. Networking")
    lines.append(document.networking)
    lines.append("")

    lines.append("## 12. Monitoring & Logging")
    lines.append(document.monitoring_logging)
    lines.append("")

    lines.append("## 13. Security Overview")
    lines.append(document.security_overview)
    lines.append("")

    lines.append("## 14. Resource Inventory")
    lines.append(document.resource_inventory_summary)
    lines.append("")
    lines.append(resource_inventory_table)
    lines.append("")

    lines.append("## 15. Deployment Flow")
    lines.append(document.deployment_flow)
    lines.append("")

    lines.append("## 16. Dependencies")
    lines.append(document.dependencies)
    lines.append("")

    lines.append("## 17. Troubleshooting Guide")
    lines.append(document.troubleshooting_guide)
    lines.append("")

    lines.append("## 18. AI Recommendations")
    if document.ai_recommendations:
        for item in document.ai_recommendations:
            lines.append(f"- {item}")
    else:
        lines.append("_No recommendations were generated._")
    lines.append("")

    lines.append("## 19. Appendix")
    lines.append(document.appendix)

    if document.data_completeness_notes:
        lines.append("")
        lines.append("---")
        lines.append("**⚠️ Data completeness notes:**")
        for note in document.data_completeness_notes:
            lines.append(f"- {note}")

    return "\n".join(lines)
