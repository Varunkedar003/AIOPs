"""Documentation-generation workflow graph (Task 24): Discover -> Collect -> Agents -> Claude
-> Export -> Complete, built and compiled with LangGraph exactly like the chat investigation
pipeline (workflow/graph.py), which this module does not modify or import from.

Reuses agents/crew/manager.py's InvestigationCrew UNCHANGED for the "Agents" stage - the same
CrewAI domain agents used for investigations, just fed documentation evidence instead of
investigation evidence. Every node fails closed: if project discovery finds nothing, later
nodes detect `state["error"]` and skip their own work rather than crashing.
"""
from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph

from agents.crew import InvestigationCrew
from docgen.collector import DocumentationCollector
from docgen.diagrams import (
    build_deployment_flow_diagram,
    build_dependency_diagram,
    build_resource_inventory_rows,
    build_resource_inventory_table,
)
from docgen.discovery import ProjectDiscovery
from docgen.exporters import export_all
from docgen.renderer import render_documentation_markdown
from docgen.schemas import ProjectDocumentation
from docgen.synthesizer import DocumentationSynthesizer
from workflow.docgen_state import DocGenState

NODE_DISCOVER = "discover"
NODE_COLLECT = "collect"
NODE_AGENTS = "agents"
NODE_CLAUDE = "claude"
NODE_EXPORT = "export"
NODE_COMPLETE = "complete"
ALL_DOCGEN_STAGES = [NODE_DISCOVER, NODE_COLLECT, NODE_AGENTS, NODE_CLAUDE, NODE_EXPORT, NODE_COMPLETE]

_APP_SERVICE_TYPE = "microsoft.web/sites"


def _make_discover_node(discovery: ProjectDiscovery):
    def _node(state: DocGenState) -> Dict[str, Any]:
        context = discovery.resolve(state.get("project_hint") or "")
        timeline = (state.get("timeline") or []) + [
            f"Discover: found project '{context.display_name}'" if context.found
            else f"Discover: could not resolve a project ({'; '.join(context.notes) or 'no project name given'})"
        ]
        return {
            "stage": NODE_DISCOVER,
            "project_context": context,
            "timeline": timeline,
            "error": None if context.found else "; ".join(context.notes) or "No project could be resolved.",
        }

    return _node


def _make_collect_node(collector: DocumentationCollector):
    def _node(state: DocGenState) -> Dict[str, Any]:
        if state.get("error"):
            return {"stage": NODE_COLLECT}
        context = state["project_context"]
        evidence = collector.collect(context)
        timeline = (state.get("timeline") or []) + ["Collect: gathered live GitLab/Azure/AKS/Monitoring/Cost evidence"]
        return {"stage": NODE_COLLECT, "evidence": evidence, "timeline": timeline}

    return _node


def _make_agents_node(crew: InvestigationCrew):
    def _node(state: DocGenState) -> Dict[str, Any]:
        if state.get("error"):
            return {"stage": NODE_AGENTS}
        context = state["project_context"]
        evidence = state.get("evidence") or {}
        question = f"Generate technical documentation for the {context.display_name} project."

        domain_reports = crew.investigate(context.slug, evidence, question=question)
        domain_reports_dicts = {domain: report.model_dump() for domain, report in domain_reports.items()}

        ran = sorted(domain_reports_dicts.keys())
        timeline = (state.get("timeline") or []) + [
            f"Agents: analyzed {', '.join(ran)}" if ran else "Agents: no domain had usable evidence"
        ]
        return {"stage": NODE_AGENTS, "domain_reports": domain_reports_dicts, "timeline": timeline}

    return _node


def _make_claude_node(synthesizer: DocumentationSynthesizer):
    def _node(state: DocGenState) -> Dict[str, Any]:
        if state.get("error"):
            return {"stage": NODE_CLAUDE}
        context = state["project_context"]
        document = synthesizer.synthesize(
            project_name=context.display_name,
            evidence=state.get("evidence") or {},
            domain_reports=state.get("domain_reports") or {},
            discovery_notes=context.notes,
            focus=state.get("focus"),
        )
        timeline = (state.get("timeline") or []) + ["Claude: documentation drafted"]
        result: Dict[str, Any] = {"stage": NODE_CLAUDE, "document": document.model_dump(), "timeline": timeline}
        if synthesizer.last_error:
            result["error"] = synthesizer.last_error
        return result

    return _node


def _find_deployment_target_label(context) -> Optional[str]:
    if context.aks_cluster:
        return f"AKS Cluster: {context.aks_cluster.get('name')}"
    app_service = next(
        (r for r in context.azure_resources if (r.get("type") or "").lower() == _APP_SERVICE_TYPE), None
    )
    return f"App Service: {app_service.get('name')}" if app_service else None


def _make_export_node():
    def _node(state: DocGenState) -> Dict[str, Any]:
        if state.get("error"):
            return {"stage": NODE_EXPORT}

        context = state["project_context"]
        document = ProjectDocumentation(**(state.get("document") or {}))
        evidence = state.get("evidence") or {}
        gitlab_evidence = evidence.get("gitlab") or {}
        aks_evidence = evidence.get("aks") or {}
        finops_evidence = evidence.get("finops") or {}

        dependency_diagram = build_dependency_diagram(
            document.project_name,
            context.azure_resources,
            aks_cluster=context.aks_cluster,
            aks_namespace=context.aks_namespace,
            aks_deployments=aks_evidence.get("deployments"),
        )
        deployment_flow_diagram = build_deployment_flow_diagram(
            gitlab_evidence.get("default_branch"),
            gitlab_evidence.get("latest_pipeline"),
            gitlab_evidence.get("pipeline_stages") or [],
            gitlab_evidence.get("environments") or [],
            _find_deployment_target_label(context),
        )
        inventory_rows = build_resource_inventory_rows(context.azure_resources, finops_evidence.get("breakdown_by_resource"))
        inventory_table = build_resource_inventory_table(context.azure_resources, finops_evidence.get("breakdown_by_resource"))

        metadata = {"generated_at": state.get("generated_at"), "requested_by_message": state.get("request_message")}
        markdown_text = render_documentation_markdown(
            document, dependency_diagram, deployment_flow_diagram, inventory_table, metadata
        )
        output_paths = export_all(
            document, markdown_text, dependency_diagram, deployment_flow_diagram, inventory_rows, metadata
        )

        timeline = (state.get("timeline") or []) + [
            f"Export: saved {len(output_paths)} file(s) under GeneratedDocs/{document.project_name}/"
        ]
        return {"stage": NODE_EXPORT, "markdown": markdown_text, "output_paths": output_paths, "timeline": timeline}

    return _node


def _make_complete_node():
    def _node(state: DocGenState) -> Dict[str, Any]:
        timeline = (state.get("timeline") or []) + ["Complete"]
        return {"stage": NODE_COMPLETE, "timeline": timeline}

    return _node


def build_docgen_graph(
    discovery: Optional[ProjectDiscovery] = None,
    collector: Optional[DocumentationCollector] = None,
    crew: Optional[InvestigationCrew] = None,
    synthesizer: Optional[DocumentationSynthesizer] = None,
):
    """Build and compile the documentation-generation graph: Discover -> Collect -> Agents ->
    Claude -> Export -> Complete -> END. Use `.stream(state, stream_mode="values")` to observe
    each stage as it completes, same pattern as workflow/graph.py."""
    discovery = discovery or ProjectDiscovery()
    collector = collector or DocumentationCollector()
    crew = crew or InvestigationCrew()
    synthesizer = synthesizer or DocumentationSynthesizer()

    graph = StateGraph(DocGenState)
    graph.add_node(NODE_DISCOVER, _make_discover_node(discovery))
    graph.add_node(NODE_COLLECT, _make_collect_node(collector))
    graph.add_node(NODE_AGENTS, _make_agents_node(crew))
    graph.add_node(NODE_CLAUDE, _make_claude_node(synthesizer))
    graph.add_node(NODE_EXPORT, _make_export_node())
    graph.add_node(NODE_COMPLETE, _make_complete_node())

    graph.add_edge(START, NODE_DISCOVER)
    graph.add_edge(NODE_DISCOVER, NODE_COLLECT)
    graph.add_edge(NODE_COLLECT, NODE_AGENTS)
    graph.add_edge(NODE_AGENTS, NODE_CLAUDE)
    graph.add_edge(NODE_CLAUDE, NODE_EXPORT)
    graph.add_edge(NODE_EXPORT, NODE_COMPLETE)
    graph.add_edge(NODE_COMPLETE, END)

    return graph.compile()
