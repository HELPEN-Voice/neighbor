# src/ii_agent/tools/neighbor/models/schemas.py
from pydantic import BaseModel, HttpUrl, field_validator
from typing import List, Optional, Literal, Dict, Any


class Evidence(BaseModel):
    claim: str
    url: Optional[HttpUrl] = None
    title: Optional[str] = None
    date: Optional[str] = None  # as published / ISO if known
    note: Optional[str] = None  # e.g., "County minutes p.3"


class SocialLink(BaseModel):
    label: str
    url: HttpUrl


class SocialFootprint(BaseModel):
    platforms: List[str] = []
    groups_or_pages: List[str] = []
    notable_posts: List[Evidence] = []  # public posts/comments only
    links: List[SocialLink] = []  # social media profile links


class InfluenceSignals(BaseModel):
    formal_roles: List[str] = []  # boards, elected/appointed
    informal_roles: List[str] = []  # fish fry organizer, coffee group, coach
    economic_footprint: List[str] = []  # sponsor, land/business scale, employer
    affiliations: List[str] = []  # church, VFW, Farm Bureau, 4-H, co-op
    network_notes: List[str] = []  # connectors (reunion committee, etc.)
    selected: List[str] = []  # selected key influence indicators for display


class ApproachRecommendations(BaseModel):
    """Structured approach recommendations with motivations and engagement strategy"""

    motivations: List[str] = []  # List of motivation enums from controlled vocabulary
    engage: str = ""  # Engagement strategy text (≤45 words)


class Disambiguation(BaseModel):
    candidates: List[str] = []  # other possible matches
    method: List[str] = []  # how we picked final (county, parcel addr)


class NeighborProfile(BaseModel):
    """Simplified neighbor profile matching the new STRICT JSON output format"""

    neighbor_id: str  # e.g., "N-01"
    name: str  # e.g., "Last, First M." or "Org Name, LLC"
    entity_category: Literal["Resident", "Organization"]
    entity_type: str  # Accept any entity type from deep research
    pins: List[str] = []  # parcel IDs associated with this neighbor
    owns_adjacent_parcel: Literal["Yes", "No"] = (
        "No"  # Whether this neighbor owns a parcel adjacent to the target
    )
    claims: str = ""  # The analytical write-up with inline citations
    confidence: Literal["high", "medium", "low"] = "medium"

    # New analytical framework fields (required for residents)
    noted_stance: Optional[Literal["support", "oppose", "neutral", "unknown"]] = (
        None  # Explicit position on energy/development
    )
    community_influence: Optional[Literal["High", "Medium", "Low", "Unknown"]] = None
    influence_justification: Optional[str] = (
        None  # Explanation of influence rating (≤8 words)
    )
    approach_recommendations: Optional[ApproachRecommendations] = (
        None  # Structured engagement strategy
    )

    @field_validator("claims", mode="before")
    @classmethod
    def coerce_claims_to_str(cls, v):
        """Join list of strings into single string if Gemini returns a list."""
        if isinstance(v, list):
            return " ".join(str(item) for item in v)
        return v

    @field_validator("noted_stance", mode="before")
    @classmethod
    def lowercase_noted_stance(cls, v):
        """Normalize noted_stance by converting to lowercase"""
        if isinstance(v, str) and v:
            return v.lower()
        return v

    @field_validator("community_influence", mode="before")
    @classmethod
    def capitalize_community_influence(cls, v):
        """Normalize community_influence by capitalizing first letter"""
        if isinstance(v, str) and v:
            return v.capitalize()
        return v

    @field_validator("approach_recommendations", mode="before")
    @classmethod
    def normalize_approach_recommendations(cls, v):
        """Convert empty strings to None for approach_recommendations"""
        if v == "" or v is None:
            return None
        return v

    # Additional field for organizations
    entity_classification: Optional[
        Literal[
            "energy_developer",
            "land_investment",
            "agriculture",
            "religious",
            "municipal",
            "speculation",
            "unknown",
        ]
    ] = None

    @field_validator("entity_classification", mode="before")
    @classmethod
    def normalize_entity_classification(cls, v):
        """Map invalid entity_classification values to 'unknown'"""
        valid = {
            "energy_developer",
            "land_investment",
            "agriculture",
            "religious",
            "municipal",
            "speculation",
            "unknown",
        }
        if v is None:
            return None
        v_lower = str(v).lower().strip()
        if v_lower in valid:
            return v_lower
        # Map common invalid values
        return "unknown"

    # Legacy fields for backward compatibility (optional)
    profile_summary: Optional[str] = None  # Maps to claims
    stance: Optional[str] = None  # Extracted from claims if needed
    signal: Optional[str] = None  # Extracted from claims if needed
    influence_level: Optional[Literal["high", "medium", "low", "unknown"]] = None

    @field_validator("influence_level", mode="before")
    @classmethod
    def lowercase_influence_level(cls, v):
        """Normalize influence_level to lowercase."""
        if isinstance(v, str) and v:
            return v.lower()
        return v

    risk_level: Optional[Literal["high", "medium", "low", "unknown"]] = None

    @field_validator("risk_level", mode="before")
    @classmethod
    def lowercase_risk_level(cls, v):
        """Normalize risk_level to lowercase."""
        if isinstance(v, str) and v:
            return v.lower()
        return v
    engagement_recommendation: Optional[str] = None

    # Legacy nested structures (optional, will be empty in new format)
    social: Optional[SocialFootprint] = None
    influence: Optional[InfluenceSignals] = None
    behavioral_indicators: Optional[List[str]] = None
    financial_stress_signals: Optional[List[str]] = None
    coalition_predictors: Optional[List[str]] = None
    disambiguation: Optional[Disambiguation] = None

    @field_validator("disambiguation", mode="before")
    @classmethod
    def coerce_disambiguation(cls, v):
        """Convert plain string to Disambiguation object."""
        if isinstance(v, str):
            return Disambiguation(method=[v]) if v.strip() else None
        return v
    citations: Optional[List[Evidence]] = None  # Citations are now inline in claims


class NeighborResult(BaseModel):
    neighbors: List[NeighborProfile]
    location_context: Optional[str] = None
    overview_summary: Optional[str] = None  # 2-3 sentence expert snapshot from prompt
    success: bool = True
    runtime_minutes: Optional[float] = None
    citations_flat: List[dict] = []
