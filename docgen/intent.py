"""Chatbot intent detection for documentation requests (Task 24).

Keyword/regex-based, consistent with agents/orchestrator.py's understand_intent -
this only decides WHETHER a chat message is a documentation request and extracts
a project name/focus if present. Returns None for anything else, so
dashboard/chat.py can fall through to the existing, unmodified investigation
pipeline for every other message.
"""
import re
from dataclasses import dataclass
from typing import Optional

_FOCUS_KEYWORDS = {
    "deployment": ("deployment documentation", "deployment docs"),
    "infrastructure": ("infrastructure documentation", "infrastructure docs"),
    "cicd": ("ci/cd documentation", "ci-cd documentation", "cicd documentation", "ci cd documentation", "pipeline documentation"),
}

# Specific patterns are always tried; the generic "document X" fallback is only tried when
# the message doesn't look like a question (see _looks_like_question) - "document why the
# pipeline failed" is an investigation question, not a documentation request.
_SPECIFIC_NAME_PATTERNS = [
    re.compile(r"(?:generate|create)\s+(?:complete\s+|full\s+)?documentation\s+for\s+(?P<name>.+)", re.IGNORECASE),
    re.compile(r"document\s+the\s+(?P<name>.+?)\s+project\b", re.IGNORECASE),
    re.compile(r"document\s+(?P<name>.+?)\s+project\b", re.IGNORECASE),
]
_LOOSE_NAME_PATTERNS = [
    re.compile(r"document\s+the\s+(?P<name>.+)", re.IGNORECASE),
    re.compile(r"document\s+(?P<name>.+)", re.IGNORECASE),
]
_QUESTION_STARTERS = ("why", "what", "how", "when", "who", "is", "does", "did", "can", "should", "will")
_INVESTIGATION_CANDIDATE_STARTERS = _QUESTION_STARTERS + ("the pipeline", "the build", "the deployment failure")

_TRAILING_PROJECT_WORD = re.compile(r"\bproject\b\s*$", re.IGNORECASE)


def _looks_like_question(lowered: str) -> bool:
    return lowered.endswith("?") or lowered.split(" ", 1)[0] in _QUESTION_STARTERS


@dataclass
class DocumentationRequest:
    """A parsed chatbot documentation request."""
    raw_message: str
    project_hint: Optional[str] = None  # explicit project name mentioned, if any
    focus: Optional[str] = None  # "deployment" | "infrastructure" | "cicd" | None (full 19-section doc)


def _clean_name(candidate: str) -> str:
    candidate = candidate.strip().strip(".!?").strip()
    candidate = candidate.strip("'\"")
    candidate = _TRAILING_PROJECT_WORD.sub("", candidate).strip()
    return candidate


def parse_documentation_request(message: str) -> Optional[DocumentationRequest]:
    """Parse a chat message into a DocumentationRequest, or None if it isn't one.

    Matches: "Generate documentation for X", "Create complete documentation for X",
    "Document the X project", "Document X", "Generate deployment/infrastructure/CI-CD
    documentation" (no project name - resolved by the caller from the selected resource).
    """
    text = (message or "").strip()
    if not text:
        return None
    lowered = text.lower()

    if "documentation" not in lowered and not re.search(r"\bdocument\b", lowered):
        return None

    patterns = _SPECIFIC_NAME_PATTERNS if _looks_like_question(lowered) else _SPECIFIC_NAME_PATTERNS + _LOOSE_NAME_PATTERNS

    name = None
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _clean_name(match.group("name"))
        if not candidate:
            continue
        if candidate.lower().startswith(_INVESTIGATION_CANDIDATE_STARTERS):
            # e.g. "document why the pipeline failed" - an investigation question, not a
            # documentation request, even though it starts with the word "document".
            continue
        name = candidate
        break

    focus = None
    for key, keywords in _FOCUS_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            focus = key
            break

    if name is None and "documentation" not in lowered:
        # A bare "document ..." with no extractable name and no "documentation" keyword is
        # too ambiguous (e.g. "document why the pipeline failed" is an investigation, not a
        # doc-gen request) - only treat it as documentation when that word is explicit.
        return None

    return DocumentationRequest(raw_message=text, project_hint=name, focus=focus)
