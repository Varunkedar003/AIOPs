"""Resource context management.

Grounds the AI in whichever resource is currently selected in the UI, so the
user never has to name it explicitly. If a resource is selected but the
message didn't name a specific capability (e.g. "tell me about this", "how's
it doing?"), default to a general resource overview instead of deflecting -
the AI already knows what "it" refers to from the selection, not the wording.
"""
from typing import Any, Iterable, Optional

DEFAULT_CONTEXT_CAPABILITY = "azure_infrastructure"


def apply_resource_context(intent: Any, resource_id: Optional[str], available_capabilities: Iterable[str]) -> Any:
    """Ground an ambiguous intent in the selected resource, if one is selected.

    `intent` is an agents.orchestrator.Intent (duck-typed here to avoid a
    circular import). Mutates and returns it unchanged when there's nothing
    to ground, or when the message already named specific capabilities.
    """
    if resource_id and not intent.capabilities and DEFAULT_CONTEXT_CAPABILITY in available_capabilities:
        intent.capabilities = [DEFAULT_CONTEXT_CAPABILITY]
        intent.action = "investigate"
    return intent
