from abc import ABC, abstractmethod
from typing import Dict, Iterator, List


class BaseLLMClient(ABC):
    """Common interface for chat-based LLM clients (Ollama, future Claude, etc.)"""

    @abstractmethod
    def invoke(self, messages: List[Dict[str, str]]) -> str:
        """Send chat messages and return the full response text"""
        raise NotImplementedError

    @abstractmethod
    def stream(self, messages: List[Dict[str, str]]) -> Iterator[str]:
        """Send chat messages and yield the response incrementally"""
        raise NotImplementedError
