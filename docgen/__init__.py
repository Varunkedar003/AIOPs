"""AI Project Documentation Generator (Task 24).

Reuses the existing LangGraph/CrewAI/Claude Sonnet investigation stack and the
existing Azure/GitLab/AKS provider layer to turn a chatbot request like
"Generate documentation for BBXF" into a saved Markdown/DOCX/PDF technical
document under GeneratedDocs/<ProjectName>/.

Nothing in this package duplicates or modifies the investigation pipeline in
workflow/graph.py, agents/, or agents/crew/ - it only adds a project discovery
and evidence-collection layer that feeds the same (unmodified) InvestigationCrew,
plus a new, documentation-specific Claude synthesis step and export layer.
"""
