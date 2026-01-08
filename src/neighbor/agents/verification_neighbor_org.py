"""Verification agent for Organization neighbor profiles using Gemini Deep Research.

Extends the base verification agent with organization-specific search patterns
and field validation.
"""

from typing import Dict, Any, List, Optional

from .verification_neighbor_base import NeighborVerificationAgent
from ..config.prompts import VERIFICATION_NEIGHBOR_SYSTEM, VERIFICATION_ORG_ADDENDUM


class NeighborOrgVerificationAgent(NeighborVerificationAgent):
    """Verification agent for organization neighbor profiles."""

    def __init__(self):
        """Initialize the organization verification agent."""
        super().__init__()
        self.name = "Verification Agent - Neighbor Organization (Gemini Deep Research)"

    def _get_system_prompt(self) -> str:
        """Get the system prompt with organization-specific additions."""
        return VERIFICATION_NEIGHBOR_SYSTEM + VERIFICATION_ORG_ADDENDUM

    def verify_batch(
        self,
        profiles: List[Dict[str, Any]],
        context: Dict[str, Any],
        entity_type: str = "organization",
        source_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verify a batch of organization profiles.

        Args:
            profiles: List of organization profile dicts to verify
            context: Dict with county, state, city
            entity_type: Always "organization" for this agent
            source_file: Name of the source dr_*.json file (for DEBUG matching)

        Returns:
            Dict with verified profiles and metadata
        """
        # Force entity_type to organization
        return super().verify_batch(profiles, context, entity_type="organization", source_file=source_file)
