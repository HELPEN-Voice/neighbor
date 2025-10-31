# src/ii_agent/tools/neighbor/engines/agents_sdk_engine.py
from typing import List, Dict, Any, Optional, Literal, Callable
from .base import ResearchEngine, ResearchEvent


class AgentsSDKEngine(ResearchEngine):
    """
    Placeholder for a future implementation using the OpenAI Agents SDK.
    You will wire up: session creation, web_search tool, streaming events → call on_event, and typed outputs.
    """

    def __init__(self, *args, **kwargs):
        pass

    async def run_batch(
        self,
        names: List[str],
        context: Dict[str, Any],
        entity_type: Literal["person", "organization"],
        on_event: Optional[Callable[[ResearchEvent], None]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError("AgentsSDKEngine not yet implemented.")
