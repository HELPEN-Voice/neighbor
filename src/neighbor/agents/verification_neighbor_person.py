"""Verification agent for Person/Resident neighbor profiles using Gemini Deep Research.

Extends the base verification agent with person-specific search patterns
and field validation.
"""

from typing import Dict, Any, List

from .verification_neighbor_base import NeighborVerificationAgent
from ..config.prompts import VERIFICATION_NEIGHBOR_SYSTEM, VERIFICATION_PERSON_ADDENDUM


class NeighborPersonVerificationAgent(NeighborVerificationAgent):
    """Verification agent for individual resident/person neighbor profiles."""

    def __init__(self):
        """Initialize the person verification agent."""
        super().__init__()
        self.name = "Verification Agent - Neighbor Person (Gemini Deep Research)"

    def _get_system_prompt(self) -> str:
        """Get the system prompt with person-specific additions."""
        return VERIFICATION_NEIGHBOR_SYSTEM + VERIFICATION_PERSON_ADDENDUM

    def verify_batch(
        self,
        profiles: List[Dict[str, Any]],
        context: Dict[str, Any],
        entity_type: str = "person",
    ) -> Dict[str, Any]:
        """Verify a batch of person profiles.

        Args:
            profiles: List of person profile dicts to verify
            context: Dict with county, state, city
            entity_type: Always "person" for this agent

        Returns:
            Dict with verified profiles and metadata
        """
        # Force entity_type to person
        return super().verify_batch(profiles, context, entity_type="person")
