"""AI-powered graph investigation controller (Task 23).

Bridges the Cytoscape.js topology/resource graphs to the *existing* investigation stack -
LangGraph (workflow/graph.py), CrewAI (agents/crew), and Claude Sonnet (synthesis/) - without
touching any of it. Clicking a node (or pressing "Open Investigation" in the AI side panel)
reuses the exact same compiled graph and session state as the AI Copilot chat
(dashboard/chat.get_shared_graph / get_shared_agent_state), so it is literally the same
pipeline run, just triggered from a graph click instead of a typed question.

This module owns two things the backend has no opinion about:
1. Turning the resulting InvestigationState into per-node "state" and per-edge "style" maps
   for the graph's visual layer (Healthy/Investigating/Warning/Root Cause/Dependency/
   Not Involved/Offline; Normal/Active Investigation/Root Cause/Impact).
2. A rolling, timestamped log of investigation events for the playback feature.
"""
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage

from utils.resource_id import normalize_resource_id

_MAX_EVENTS = 300

# --- Node/edge visual states (Task 23 §3) --------------------------------------------------

NODE_HEALTHY = "healthy"
NODE_INVESTIGATING = "investigating"
NODE_WARNING = "warning"
NODE_ROOT_CAUSE = "root_cause"
NODE_DEPENDENCY = "dependency"
NODE_NOT_INVOLVED = "not_involved"
NODE_OFFLINE = "offline"

EDGE_NORMAL = "normal"
EDGE_ACTIVE_INVESTIGATION = "active_investigation"
EDGE_ROOT_CAUSE_PATH = "root_cause_path"
EDGE_IMPACT_PATH = "impact_path"

_UNHEALTHY_STATUSES = ("critical", "error", "failed", "stopped", "offline", "unavailable")
_DEGRADED_STATUSES = ("warning", "degraded", "unhealthy")
_HEALTHY_STATUSES = ("healthy", "running", "active", "online", "success", "available", "succeeded")


def _in_progress_stages() -> tuple:
    return ("intent", "agents", "claude")


def compute_health_score(resource: Optional[Dict[str, Any]], health: Optional[Dict[str, Any]], active_alerts: int) -> int:
    """A single 0-100 score derived from real, already-fetched fields (health status +
    active alert count) - not a fabricated number, just one deterministic view of data
    the app already has."""
    status = ((resource or {}).get("health_status") or (health or {}).get("health_status") or "").lower()
    if status in _UNHEALTHY_STATUSES:
        base = 20
    elif status in _DEGRADED_STATUSES:
        base = 60
    elif status in _HEALTHY_STATUSES:
        base = 100
    else:
        base = 75  # unknown status - don't imply either healthy or unhealthy

    penalty = min(base - 5, active_alerts * 10)
    return max(5, base - penalty)


def status_bucket(resource: Optional[Dict[str, Any]], health: Optional[Dict[str, Any]]) -> str:
    """Map a resource's own recorded status to one of the "at rest" node states
    (healthy/warning/offline) - used before any investigation has touched the node."""
    status = ((resource or {}).get("health_status") or (health or {}).get("health_status") or "").lower()
    if status in _UNHEALTHY_STATUSES:
        return NODE_OFFLINE
    if status in _DEGRADED_STATUSES:
        return NODE_WARNING
    return NODE_HEALTHY


def _edge_key(edge: Dict[str, Any]) -> str:
    return f"{edge.get('source')}->{edge.get('target')}"


def _node_key(node: Dict[str, Any]) -> Optional[str]:
    return node.get("id") or node.get("resource_id")


def _current_investigation() -> Dict[str, Any]:
    agent_state = st.session_state.get("agent_state") or {}
    return agent_state.get("investigation") or {}


def build_node_states(
    nodes: List[Dict[str, Any]],
    dependency_ids: Optional[List[str]] = None,
    resource_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, str]:
    """One visual state per node, layering the live/most-recent investigation on top of each
    node's own recorded health. `dependency_ids` are the direct neighbors of whichever node is
    selected (used by the depth-1 Resource Graph); pass None/[] for the full topology view,
    where nothing is a "dependency" of anything in particular.
    """
    investigation = _current_investigation()
    stage = investigation.get("stage", "idle")
    selected_id = st.session_state.get("selected_resource_id")
    selected_norm = normalize_resource_id(selected_id) if selected_id else None
    dependency_norm = {normalize_resource_id(d) for d in (dependency_ids or [])}
    has_run_investigation = stage != "idle"
    final_report = investigation.get("final_report") or {}
    root_cause = (final_report.get("root_cause") or "").strip().lower()
    has_root_cause = bool(root_cause) and root_cause not in ("not applicable", "n/a", "")

    states: Dict[str, str] = {}
    for node in nodes:
        node_id = _node_key(node)
        if not node_id:
            continue
        node_norm = normalize_resource_id(node_id)
        resource = (resource_lookup or {}).get(node_id) or node

        is_selected = selected_norm is not None and node_norm == selected_norm
        is_dependency = node_norm in dependency_norm

        if is_selected and stage in _in_progress_stages():
            states[node_id] = NODE_INVESTIGATING
        elif is_selected and stage == "complete" and has_root_cause:
            states[node_id] = NODE_ROOT_CAUSE
        elif is_selected:
            states[node_id] = status_bucket(resource, None)
        elif is_dependency:
            states[node_id] = NODE_DEPENDENCY
        elif has_run_investigation and dependency_ids is not None:
            # Depth-1 resource-graph context: everything outside selected+dependencies is
            # explicitly "not involved" in this investigation.
            states[node_id] = NODE_NOT_INVOLVED
        else:
            states[node_id] = status_bucket(resource, None)

    return states


def build_edge_styles(
    edges: List[Dict[str, Any]],
    dependency_ids: Optional[List[str]] = None,
) -> Dict[str, str]:
    """One visual style per edge (keyed "source->target"), reflecting whether it touches the
    selected/investigated node and what the investigation concluded."""
    investigation = _current_investigation()
    stage = investigation.get("stage", "idle")
    selected_id = st.session_state.get("selected_resource_id")
    selected_norm = normalize_resource_id(selected_id) if selected_id else None
    final_report = investigation.get("final_report") or {}
    root_cause = (final_report.get("root_cause") or "").strip().lower()
    has_root_cause = bool(root_cause) and root_cause not in ("not applicable", "n/a", "")
    dependency_norm = {normalize_resource_id(d) for d in (dependency_ids or [])}

    styles: Dict[str, str] = {}
    for edge in edges:
        source_norm = normalize_resource_id(edge.get("source"))
        target_norm = normalize_resource_id(edge.get("target"))
        touches_selected = selected_norm in (source_norm, target_norm) if selected_norm else False
        other = target_norm if source_norm == selected_norm else source_norm

        if touches_selected and stage in _in_progress_stages():
            styles[_edge_key(edge)] = EDGE_ACTIVE_INVESTIGATION
        elif touches_selected and stage == "complete" and has_root_cause and other in dependency_norm:
            styles[_edge_key(edge)] = EDGE_ROOT_CAUSE_PATH
        elif touches_selected and stage == "complete":
            styles[_edge_key(edge)] = EDGE_IMPACT_PATH
        else:
            styles[_edge_key(edge)] = EDGE_NORMAL

    return styles


# --- Investigation event log, for playback (Task 23 §6) -------------------------------------

def record_event(node_id: str, stage: str, label: str) -> None:
    events: List[Dict[str, Any]] = st.session_state.setdefault("investigation_events", [])
    events.append({
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "node_id": node_id,
        "stage": stage,
        "label": label,
    })
    del events[:-_MAX_EVENTS]


def get_events() -> List[Dict[str, Any]]:
    return st.session_state.get("investigation_events") or []


# --- Triggering an investigation (Task 23 §1) ------------------------------------------------

def investigate_node(node_id: str, resource_name: Optional[str] = None, question: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """Run the shared LangGraph pipeline for `node_id`, yielding the state after each stage.

    Reuses dashboard.chat's shared graph/session so this is the *same* LangGraph run, CrewAI
    agents, and Claude Sonnet call the AI Copilot chat uses - not a parallel implementation.
    """
    from dashboard.chat import get_shared_agent_state, get_shared_graph

    state = get_shared_agent_state()
    state["selected_resource_id"] = node_id
    st.session_state.selected_resource_id = node_id

    prompt = question or f"Investigate {resource_name or node_id} for problems."
    state["messages"].append(HumanMessage(content=prompt))

    label = resource_name or node_id
    record_event(node_id, "intent", f"Investigation started for {label}")

    final_state = state
    try:
        for step_state in get_shared_graph().stream(state, stream_mode="values"):
            final_state = step_state
            stage = (step_state.get("investigation") or {}).get("stage", "idle")
            if stage != "idle":
                record_event(node_id, stage, f"{stage.title()} stage complete for {label}")
            yield step_state
    except Exception as exc:
        final_state["messages"].append(AIMessage(content=f"Something went wrong: {exc}"))
        record_event(node_id, "error", f"Investigation failed for {label}: {exc}")
        yield final_state

    st.session_state.agent_state = final_state
    st.session_state.last_investigated_node_id = node_id
