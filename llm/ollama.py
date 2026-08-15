import logging
from typing import Dict, Iterator, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from config import Config
from llm.base import BaseLLMClient

logger = logging.getLogger(__name__)

_ROLE_TO_MESSAGE_CLASS = {
    "system": SystemMessage,
    "user": HumanMessage,
    "assistant": AIMessage,
}


class OllamaError(Exception):
    """Raised when the Ollama backend is unreachable or returns an error"""


class OllamaClient(BaseLLMClient):
    """Chat client for a local Ollama model, backed by LangChain's ChatOllama"""

    def __init__(self, model: str = None, base_url: str = None, **kwargs):
        self.model = model or Config.OLLAMA_MODEL
        self.base_url = base_url or Config.OLLAMA_BASE_URL
        self._chat = ChatOllama(model=self.model, base_url=self.base_url, **kwargs)

    def _to_langchain_messages(self, messages: List[Dict[str, str]]):
        return [
            _ROLE_TO_MESSAGE_CLASS.get(msg.get("role", "user"), HumanMessage)(content=msg.get("content", ""))
            for msg in messages
        ]

    def invoke(self, messages: List[Dict[str, str]]) -> str:
        """Send chat messages and return the full response text"""
        try:
            response = self._chat.invoke(self._to_langchain_messages(messages))
            return response.content
        except Exception as exc:
            logger.error("Ollama invoke failed (model=%s, base_url=%s): %s", self.model, self.base_url, exc)
            raise OllamaError(
                f"Failed to get a response from Ollama at {self.base_url} (model={self.model}): {exc}"
            ) from exc

    def stream(self, messages: List[Dict[str, str]]) -> Iterator[str]:
        """Send chat messages and yield the response incrementally"""
        try:
            for chunk in self._chat.stream(self._to_langchain_messages(messages)):
                if chunk.content:
                    yield chunk.content
        except Exception as exc:
            logger.error("Ollama stream failed (model=%s, base_url=%s): %s", self.model, self.base_url, exc)
            raise OllamaError(
                f"Failed to stream a response from Ollama at {self.base_url} (model={self.model}): {exc}"
            ) from exc
