from abc import ABC, abstractmethod
from typing import Any, Dict


class SpecialistAgent(ABC):
    """Common interface for specialist agents the orchestrator can invoke"""

    name: str = "specialist"

    @abstractmethod
    def run(self, resource_id: str) -> Dict[str, Any]:
        """Investigate the given resource and return structured findings as a dict"""
        raise NotImplementedError
