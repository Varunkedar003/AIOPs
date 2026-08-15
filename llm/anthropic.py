import logging
from typing import Dict, Iterator, List, Optional

import anthropic

from config import Config
from llm.base import BaseLLMClient

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOKENS = 16000


class AnthropicError(Exception):
    """Raised when the Anthropic API is unreachable or returns an error"""


class AnthropicClient(BaseLLMClient):
    """Chat client for Claude, backed by the official Anthropic SDK."""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None, max_tokens: int = _DEFAULT_MAX_TOKENS):
        self.model = model or Config.ANTHROPIC_MODEL
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key or Config.ANTHROPIC_API_KEY or None)

    @staticmethod
    def _split_system(messages: List[Dict[str, str]]):
        """The Messages API takes no 'system' role in `messages` - split it into the top-level `system` field."""
        system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
        chat_messages = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages if m.get("role") != "system"
        ]
        return ("\n\n".join(system_parts) or None), chat_messages

    def invoke(self, messages: List[Dict[str, str]]) -> str:
        """Send chat messages and return the full response text"""
        system, chat_messages = self._split_system(messages)
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=chat_messages,
            )
            return next((block.text for block in response.content if block.type == "text"), "")
        except Exception as exc:
            logger.error("Anthropic invoke failed (model=%s): %s", self.model, exc)
            raise AnthropicError(f"Failed to get a response from Claude (model={self.model}): {exc}") from exc

    def stream(self, messages: List[Dict[str, str]]) -> Iterator[str]:
        """Send chat messages and yield the response incrementally"""
        system, chat_messages = self._split_system(messages)
        try:
            with self._client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=chat_messages,
            ) as stream:
                for text in stream.text_stream:
                    yield text
        except Exception as exc:
            logger.error("Anthropic stream failed (model=%s): %s", self.model, exc)
            raise AnthropicError(f"Failed to stream a response from Claude (model={self.model}): {exc}") from exc
