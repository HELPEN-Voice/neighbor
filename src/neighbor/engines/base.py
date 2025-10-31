# src/ii_agent/tools/neighbor/engines/base.py
from typing import Protocol, List, Dict, Any, Optional, Literal, Callable, TypedDict


class ResearchEvent(TypedDict, total=False):
    type: str  # "start" | "progress" | "finish" | "error"
    batch_size: int
    entity_type: Literal["person", "organization"]
    message: str
    meta: Dict[str, Any]


# Engine protocol for swap-ability (Responses today, Agents SDK later)
class ResearchEngine(Protocol):
    async def run_batch(
        self,
        names: List[str],
        context: Dict[str, Any],
        entity_type: Literal["person", "organization"],
        on_event: Optional[Callable[[ResearchEvent], None]] = None,
    ) -> Dict[str, Any]:
        """
        Returns:
          {
            "neighbors": [ {NeighborProfile-like dict}, ... ],
            "annotations": [ {title,url,...}, ... ],
            "raw_text": "...",   # optional for diagnostics
          }
        """
        ...
