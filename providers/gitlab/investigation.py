"""Pure, network-free log/artifact analysis helpers for live GitLab pipeline investigation
(Task 15 - GitLab Pipeline Investigation).

Kept separate from providers/gitlab/client.py (which does the live API calls) so this parsing
logic can be exercised against a plain string/bytes without any GitLab credentials. Nothing here
draws conclusions (no root cause, no recommendations) - every function extracts a fact from the
text/bytes it's given, or returns None/[] when that fact isn't present.
"""
import io
import re
import zipfile
from typing import Any, Dict, List, Optional

_CONTEXT_LINES = 3
_MAX_ERROR_MESSAGE_CHARS = 1000
_MAX_STACK_TRACE_CHARS = 4000
_MAX_ARTIFACT_PREVIEW_BYTES = 20_000
_MAX_ARTIFACT_FILES = 50
_PREVIEW_TEXT_EXTENSIONS = (".txt", ".log", ".json", ".xml", ".md", ".out")

# Broad indicators that a log line marks a failure point - checked in order, first match wins.
# "error"/"exception" are intentionally unanchored (no leading \b) so they also catch compound
# identifiers like "TypeError:", "NullPointerException" that a strict \berror\b would miss.
_FAILURE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"error", r"exception", r"\bfail(?:ed|ure)?\b", r"\btraceback\b",
        r"\bfatal\b", r"panic:", r"command not found", r"no such file or directory",
        r"permission denied", r"cannot find", r"\bnot found\b", r"assertionerror",
        r"exit code [1-9]", r"exit status [1-9]", r"\berr!",
    )
]

# Narrower patterns for a line that IS the error message itself (as opposed to just mentioning
# the word "error" in passing, e.g. a log line reading "0 errors, 2 warnings"). The third pattern
# catches named exception types (TypeError:, NullPointerException:, ValueError: ...).
_ERROR_LINE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (r"^\s*error[: ]", r"^\s*fatal[: ]", r"\w*(?:error|exception)\w*\s*:", r"^\s*err!", r"^\s*e:\s")
]

_STACK_TRACE_START_PATTERNS = [
    re.compile(p) for p in (
        r"Traceback \(most recent call last\)",
        r"^\s*at .+\(.+:\d+(:\d+)?\)",
        r'^\s*File ".+", line \d+, in ',
    )
]


def find_failure_point(log_text: str) -> Optional[Dict[str, Any]]:
    """The first line in the log matching a known failure indicator, with surrounding context.

    Returns {"line_number": 1-indexed int, "line": str, "context": [str, ...]}, or None if the
    log has no such line (e.g. it's empty, or the job failed for a reason outside its own output).
    """
    if not log_text:
        return None
    lines = log_text.splitlines()
    for index, line in enumerate(lines):
        if any(pattern.search(line) for pattern in _FAILURE_PATTERNS):
            start = max(0, index - _CONTEXT_LINES)
            end = min(len(lines), index + _CONTEXT_LINES + 1)
            return {
                "line_number": index + 1,
                "line": line.strip(),
                "context": [context_line.strip() for context_line in lines[start:end]],
            }
    return None


def extract_error_message(log_text: str, failure_point: Optional[Dict[str, Any]]) -> Optional[str]:
    """The most specific error-message-shaped line in the log; falls back to the failure
    point's line when nothing more specific is found."""
    if not log_text:
        return None
    for line in log_text.splitlines():
        stripped = line.strip()
        if stripped and any(pattern.search(stripped) for pattern in _ERROR_LINE_PATTERNS):
            return stripped[:_MAX_ERROR_MESSAGE_CHARS]
    if failure_point:
        return failure_point["line"][:_MAX_ERROR_MESSAGE_CHARS]
    return None


def extract_stack_trace(log_text: str) -> Optional[str]:
    """Captures a contiguous stack-trace-shaped block starting at the first recognized marker
    (Python traceback, Java/Node `at ...` frames), or None if the log has no such block."""
    if not log_text:
        return None
    lines = log_text.splitlines()
    for index, line in enumerate(lines):
        if not any(pattern.search(line) for pattern in _STACK_TRACE_START_PATTERNS):
            continue

        block = [line]
        for next_line in lines[index + 1:]:
            if not next_line.strip():
                break
            looks_like_frame = next_line.startswith((" ", "\t")) or re.match(r'^\s*(at |File ")', next_line)
            if looks_like_frame:
                block.append(next_line)
            else:
                break

        trace = "\n".join(block).strip()
        if len(trace) > _MAX_STACK_TRACE_CHARS:
            trace = trace[:_MAX_STACK_TRACE_CHARS] + "\n... (truncated)"
        return trace
    return None


def _summarize_junit_xml(content: str) -> Optional[Dict[str, Any]]:
    """Best-effort JUnit-style test-report summary via attribute regex - tolerates attribute
    reordering and partial/malformed XML that a strict parser would reject outright."""
    tag_match = re.search(r"<testsuite[s]?\b[^>]*>", content, re.IGNORECASE)
    if not tag_match:
        return None
    tag = tag_match.group(0)

    def _attr(name: str) -> Optional[int]:
        match = re.search(rf'{name}="(\d+)"', tag)
        return int(match.group(1)) if match else None

    tests = _attr("tests")
    if tests is None:
        return None
    return {
        "tests": tests,
        "failures": _attr("failures") or 0,
        "errors": _attr("errors") or 0,
        "skipped": _attr("skipped") or 0,
    }


def analyze_artifact_archive(data: bytes) -> List[Dict[str, Any]]:
    """List every file in a job's artifacts.zip; small text/log/report files get a content
    preview (and a test-count summary for JUnit-style XML), everything else is metadata only."""
    if not data:
        return []

    results: List[Dict[str, Any]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist()[:_MAX_ARTIFACT_FILES]:
                if info.is_dir():
                    continue
                entry: Dict[str, Any] = {"filename": info.filename, "size": info.file_size}

                is_previewable = (
                    0 < info.file_size <= _MAX_ARTIFACT_PREVIEW_BYTES
                    and info.filename.lower().endswith(_PREVIEW_TEXT_EXTENSIONS)
                )
                if is_previewable:
                    try:
                        content = archive.read(info.filename).decode("utf-8", errors="replace")
                        entry["content_preview"] = content[:2000]
                        if info.filename.lower().endswith(".xml"):
                            summary = _summarize_junit_xml(content)
                            if summary:
                                entry["test_summary"] = summary
                    except Exception:
                        pass
                results.append(entry)
    except Exception:
        return []
    return results
