"""Deterministic, evidence-based issue detection for a namespace's Kubernetes workloads.

Rule-based (no LLM) so a namespace investigation can show "what's wrong and where" the instant
the data is fetched - Claude's synthesis (see synthesis/claude_synthesizer.py, wired in via
dashboard/aks.py's render_namespace_investigation) only ever layers a root-cause narrative and
remediation steps on top of exactly these facts, never inventing a different resource or
symptom. Every issue carries `evidence`: the exact raw pod/deployment/replicaset/event dict it
was derived from - nothing here is a bare claim.
"""
from collections import OrderedDict
from typing import Any, Dict, List

# Restart counts below this are treated as normal pod lifecycle noise (a single restart soon
# after deploy is common); at/above it, a pod is flagged as repeatedly failing.
_RESTART_WARNING_THRESHOLD = 3
_RESTART_CRITICAL_THRESHOLD = 10

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# Warning events are deduped and capped - a single flapping pod can emit dozens of identical
# "BackOff" events, which would otherwise bury every other finding under repeats of one fact.
_MAX_EVENT_ISSUES = 20


def _pod_issues(pods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    issues = []
    for pod in pods:
        name = pod.get("name")
        namespace = pod.get("namespace")
        phase = pod.get("phase")
        restarts = pod.get("restart_count", 0) or 0
        ready = pod.get("ready", "")

        if phase in ("Failed", "Unknown"):
            issues.append({
                "severity": "critical",
                "resource_type": "Pod",
                "resource_name": name,
                "namespace": namespace,
                "title": f'Pod "{name}" is in phase {phase}',
                "detail": f"Kubernetes reports this pod's phase as {phase}, meaning it isn't running.",
                "evidence": pod,
            })
        elif phase == "Pending":
            issues.append({
                "severity": "warning",
                "resource_type": "Pod",
                "resource_name": name,
                "namespace": namespace,
                "title": f'Pod "{name}" is stuck Pending',
                "detail": (
                    "The pod has been scheduled but its containers haven't started yet - common "
                    "causes are insufficient node resources, an unschedulable node, or an image "
                    "that can't be pulled."
                ),
                "evidence": pod,
            })
        elif phase == "Running" and restarts >= _RESTART_WARNING_THRESHOLD:
            issues.append({
                "severity": "critical" if restarts >= _RESTART_CRITICAL_THRESHOLD else "warning",
                "resource_type": "Pod",
                "resource_name": name,
                "namespace": namespace,
                "title": f'Pod "{name}" has restarted {restarts} times',
                "detail": (
                    "Frequent restarts usually mean a container is crashing (CrashLoopBackOff) or "
                    "failing its liveness probe."
                ),
                "evidence": pod,
            })
        elif phase == "Running" and not pod.get("is_healthy"):
            issues.append({
                "severity": "warning",
                "resource_type": "Pod",
                "resource_name": name,
                "namespace": namespace,
                "title": f'Pod "{name}" is Running but not fully Ready ({ready})',
                "detail": "One or more containers in this pod haven't passed their readiness check yet.",
                "evidence": pod,
            })
    return issues


def _deployment_issues(deployments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    issues = []
    for d in deployments:
        if d.get("replicas", 0) > 0 and not d.get("is_healthy"):
            name = d.get("name")
            issues.append({
                "severity": "critical",
                "resource_type": "Deployment",
                "resource_name": name,
                "namespace": d.get("namespace"),
                "title": (
                    f'Deployment "{name}" is under-provisioned '
                    f"({d.get('ready_replicas', 0)}/{d.get('replicas', 0)} ready)"
                ),
                "detail": (
                    "Fewer replicas are available than requested - the rollout may be stuck, "
                    "crashing, or unable to schedule."
                ),
                "evidence": d,
            })
    return issues


def _replicaset_issues(replicasets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    issues = []
    for rs in replicasets:
        if rs.get("replicas", 0) > 0 and not rs.get("is_healthy"):
            name = rs.get("name")
            issues.append({
                "severity": "warning",
                "resource_type": "ReplicaSet",
                "resource_name": name,
                "namespace": rs.get("namespace"),
                "title": (
                    f'ReplicaSet "{name}" is under-provisioned '
                    f"({rs.get('available_replicas', 0)}/{rs.get('replicas', 0)} available)"
                ),
                "detail": f"Owned by {rs.get('owner') or 'an unknown controller'}.",
                "evidence": rs,
            })
    return issues


def _event_issues(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: "OrderedDict[Any, Dict[str, Any]]" = OrderedDict()
    for e in events:
        if (e.get("type") or "").lower() != "warning":
            continue
        key = (e.get("reason"), e.get("involved_object"), e.get("message"))
        bucket = grouped.setdefault(key, {"event": e, "count": 0})
        bucket["count"] += 1

    issues = []
    for (reason, involved_object, message), bucket in grouped.items():
        count = bucket["count"]
        suffix = f" (x{count})" if count > 1 else ""
        issues.append({
            "severity": "warning",
            "resource_type": "Event",
            "resource_name": involved_object,
            "namespace": None,
            "title": f"Warning event: {reason} on {involved_object}{suffix}",
            "detail": message or "",
            "evidence": bucket["event"],
        })
    return issues[:_MAX_EVENT_ISSUES]


def detect_namespace_issues(
    pods: List[Dict[str, Any]],
    deployments: List[Dict[str, Any]],
    replicasets: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Rule-based scan of already-collected namespace evidence for concrete problems.

    Returns issues sorted critical-first, each with a plain-language title/detail and the exact
    raw evidence dict it came from - never a synthesized or inferred fact. An empty pods/
    deployments/replicasets/events list simply contributes no issues from that category; it does
    not imply that category is healthy if it was actually never collected (see
    ResourceService.investigate_namespace's `fetch_errors`).
    """
    issues = (
        _pod_issues(pods)
        + _deployment_issues(deployments)
        + _replicaset_issues(replicasets)
        + _event_issues(events)
    )
    issues.sort(key=lambda issue: _SEVERITY_ORDER.get(issue["severity"], 9))
    return issues
