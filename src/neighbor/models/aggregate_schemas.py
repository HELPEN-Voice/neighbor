"""Aggregate schemas for PII-free neighbor analysis output.

These schemas represent the final output of the neighbor pipeline after
all individual profiles have been aggregated and PII stripped. No names,
PINs, addresses, or other personally identifiable information is retained.
"""

from pydantic import BaseModel
from typing import Dict, List, Optional


class CommunityTheme(BaseModel):
    """A thematic grouping of neighbors with shared characteristics."""

    theme: str  # e.g., "Agricultural Community"
    description: str  # 2-3 sentences, NO individual names
    neighbor_count: int
    prevalent_concerns: List[str] = []  # e.g., ["farmland_preservation", "livestock_safety"]
    typical_influence: str = "Low"  # e.g., "Low to Medium"
    engagement_approach: str = ""  # Generic strategy for this group


class OppositionSummary(BaseModel):
    """Summary of neighbors who have expressed opposition."""

    count: int
    common_concerns: List[str] = []
    influence_levels: List[str] = []  # e.g., ["High", "Medium"]


class SupportSummary(BaseModel):
    """Summary of neighbors who have expressed support."""

    count: int
    common_reasons: List[str] = []


class NeighborAggregateResult(BaseModel):
    """PII-free aggregate result from the neighbor screening pipeline.

    Contains only aggregate statistics, thematic community insights,
    and risk scoring. No individual neighbor profiles or identifying
    information is retained.
    """

    # Counts
    total_screened: int = 0
    residents_count: int = 0
    organizations_count: int = 0
    adjacent_count: int = 0

    # Distributions
    influence_distribution: Dict[str, int] = {}  # {"High": 3, "Medium": 8, "Low": 17}
    stance_distribution: Dict[str, int] = {}  # {"oppose": 2, "support": 1, ...}
    entity_type_breakdown: Dict[str, int] = {}  # {"agriculture": 5, "religious": 2, ...}

    # Risk
    risk_score: int = 2  # 1-10
    risk_level: str = "low"  # "low" / "medium" / "high"

    # Thematic insights
    themes: List[CommunityTheme] = []
    opposition_summary: Optional[OppositionSummary] = None
    support_summary: Optional[SupportSummary] = None
    overview_summary: Optional[str] = None

    # Metadata
    location_context: Optional[str] = None
    city: Optional[str] = None
    county: Optional[str] = None
    state: Optional[str] = None
    run_id: Optional[str] = None
    runtime_minutes: Optional[float] = None
    success: bool = True

    # Map (sentiment rings — no individual parcels)
    map_image_path: Optional[str] = None
    map_ring_stats: Optional[List[dict]] = None
    map_metadata: Optional[dict] = None
