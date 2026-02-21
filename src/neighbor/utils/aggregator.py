"""Aggregate individual neighbor profiles into PII-free summary statistics and themes.

This module is the anonymization boundary: individual NeighborProfile dicts
(with names, PINs, claims) go in; only aggregate counts, distributions, risk
scores, and LLM-generated community themes come out. No PII is retained.
"""

import os
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from ..models.aggregate_schemas import (
    CommunityTheme,
    NeighborAggregateResult,
    OppositionSummary,
    SupportSummary,
)


def _compute_counts(profiles: List[dict]) -> dict:
    """Compute aggregate counts from individual profiles."""
    residents = sum(
        1 for p in profiles
        if (p.get("entity_category") or p.get("entity_type") or "").lower()
        in ["resident", "individual", "trust", "estate"]
    )
    organizations = len(profiles) - residents
    adjacent = sum(
        1 for p in profiles if p.get("owns_adjacent_parcel") == "Yes"
    )
    return {
        "total_screened": len(profiles),
        "residents_count": residents,
        "organizations_count": organizations,
        "adjacent_count": adjacent,
    }


def _compute_influence_distribution(profiles: List[dict]) -> Dict[str, int]:
    """Count neighbors by influence level."""
    dist = {"High": 0, "Medium": 0, "Low": 0}
    for p in profiles:
        level = (p.get("community_influence") or "Low").capitalize()
        if level in dist:
            dist[level] += 1
        else:
            dist["Low"] += 1
    return dist


def _compute_stance_distribution(profiles: List[dict]) -> Dict[str, int]:
    """Count neighbors by noted stance."""
    dist = {"oppose": 0, "support": 0, "neutral": 0, "unknown": 0}
    for p in profiles:
        stance = (p.get("noted_stance") or "unknown").lower()
        if stance in dist:
            dist[stance] += 1
        else:
            dist["unknown"] += 1
    return dist


def _compute_entity_type_breakdown(profiles: List[dict]) -> Dict[str, int]:
    """Count organizations by entity_classification."""
    breakdown = {}
    for p in profiles:
        classification = (p.get("entity_classification") or "unknown").lower()
        breakdown[classification] = breakdown.get(classification, 0) + 1
    return breakdown


def _compute_risk(
    influence_dist: Dict[str, int], stance_dist: Dict[str, int]
) -> tuple:
    """Compute risk score (1-10) and level from influence/stance distributions."""
    high_influence = influence_dist.get("High", 0)
    opposed_count = stance_dist.get("oppose", 0)
    risk_score = min(10, 2 + (high_influence * 2) + (opposed_count * 3))
    if risk_score <= 3:
        risk_level = "low"
    elif risk_score <= 6:
        risk_level = "medium"
    else:
        risk_level = "high"
    return risk_score, risk_level


def _build_opposition_summary(profiles: List[dict]) -> Optional[OppositionSummary]:
    """Build opposition summary from profiles with oppose stance."""
    opposed = [
        p for p in profiles if (p.get("noted_stance") or "").lower() == "oppose"
    ]
    if not opposed:
        return None

    # Collect concerns from motivations
    concerns = []
    for p in opposed:
        motivations = (p.get("approach_recommendations") or {}).get("motivations", [])
        concerns.extend(motivations)
    # Deduplicate while preserving order
    seen = set()
    unique_concerns = []
    for c in concerns:
        if c not in seen:
            seen.add(c)
            unique_concerns.append(c)

    influence_levels = list(set(
        (p.get("community_influence") or "Low").capitalize() for p in opposed
    ))

    return OppositionSummary(
        count=len(opposed),
        common_concerns=unique_concerns,
        influence_levels=influence_levels,
    )


def _build_support_summary(profiles: List[dict]) -> Optional[SupportSummary]:
    """Build support summary from profiles with support stance."""
    supporters = [
        p for p in profiles if (p.get("noted_stance") or "").lower() == "support"
    ]
    if not supporters:
        return None

    reasons = []
    for p in supporters:
        motivations = (p.get("approach_recommendations") or {}).get("motivations", [])
        reasons.extend(motivations)
    seen = set()
    unique_reasons = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            unique_reasons.append(r)

    return SupportSummary(count=len(supporters), common_reasons=unique_reasons)


async def _generate_themes(
    profiles: List[dict],
    location_context: str,
) -> List[CommunityTheme]:
    """Use Gemini Flash to generate community themes from neighbor profiles.

    The LLM receives full profiles (including names) but is instructed to
    produce only aggregate thematic groupings with NO individual names.
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("⚠️  No GEMINI_API_KEY set — skipping theme generation")
        return []

    # Build a summary of each profile for the LLM (include analytical data, minimize PII exposure)
    profile_summaries = []
    for i, p in enumerate(profiles):
        entity_cat = p.get("entity_category", "Unknown")
        entity_type = p.get("entity_type", "Unknown")
        influence = p.get("community_influence", "Unknown")
        stance = p.get("noted_stance", "unknown")
        justification = p.get("influence_justification", "")
        classification = p.get("entity_classification", "unknown")
        motivations = (p.get("approach_recommendations") or {}).get("motivations", [])
        adjacent = p.get("owns_adjacent_parcel", "No")

        profile_summaries.append(
            f"Neighbor {i+1}: {entity_cat} ({entity_type}), "
            f"classification={classification}, influence={influence}, "
            f"stance={stance}, adjacent={adjacent}, "
            f"justification=\"{justification}\", "
            f"motivations={motivations}"
        )

    prompt = f"""You are analyzing neighbor screening results for a land development project.

LOCATION: {location_context}
TOTAL NEIGHBORS: {len(profiles)}

NEIGHBOR DATA (anonymized):
{chr(10).join(profile_summaries)}

TASK:
Group these neighbors into 3-5 community themes that capture the key patterns.
For each theme, provide a JSON object with:
- "theme": Short theme name (e.g., "Agricultural Community", "Local Government Presence", "Residential Cluster")
- "description": 2-3 sentence description of this group. DO NOT mention any individual names, PINs, or addresses. Describe the pattern, not the people.
- "neighbor_count": How many neighbors fall into this theme
- "prevalent_concerns": List of concern keywords (e.g., ["farmland_preservation", "property_value"])
- "typical_influence": The typical influence level for this group (e.g., "Low", "Medium", "High", "Low to Medium")
- "engagement_approach": A 1-2 sentence recommended engagement strategy for this group

RULES:
- DO NOT mention any individual names, PINs, parcel IDs, or addresses
- Each neighbor should be counted in exactly one theme
- Theme neighbor_counts must sum to {len(profiles)}
- Focus on patterns relevant to energy/infrastructure development decisions

Return ONLY a JSON array of theme objects. No preamble or explanation."""

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,
            response_mime_type="application/json",
        ),
    )

    raw = (response.text or "").strip()
    if not raw:
        print("⚠️  Gemini returned empty response for theme generation")
        return []

    import json
    try:
        themes_data = json.loads(raw)
        if not isinstance(themes_data, list):
            themes_data = [themes_data]
        return [CommunityTheme(**t) for t in themes_data]
    except Exception as e:
        print(f"⚠️  Failed to parse theme JSON: {e}")
        return []


async def aggregate_neighbors(
    profiles: List[dict],
    location_context: str,
    overview_summary: Optional[str] = None,
    city: Optional[str] = None,
    county: Optional[str] = None,
    state: Optional[str] = None,
    run_id: Optional[str] = None,
    runtime_minutes: Optional[float] = None,
    map_image_path: Optional[str] = None,
    map_ring_stats: Optional[list] = None,
    map_metadata: Optional[dict] = None,
) -> dict:
    """Convert individual neighbor profiles into an aggregate PII-free result.

    This is the anonymization boundary. Names, PINs, addresses, claims, social
    media profiles, and all other PII are consumed here but NOT included in the
    output. Only aggregate statistics, thematic groupings, and risk scores are
    returned.

    Args:
        profiles: List of NeighborProfile dicts (with PII — used in-memory only)
        location_context: e.g., "Neighbors within 0.5 mi of 39.12,-94.56"
        overview_summary: Pre-generated overview (should not contain names)
        city, county, state: Location metadata
        run_id: Pipeline run identifier
        runtime_minutes: Total pipeline runtime
        map_image_path: Path to generated ring map image
        map_ring_stats: List of ring stat dicts from SentimentRingGenerator
        map_metadata: Map generation metadata

    Returns:
        dict representation of NeighborAggregateResult (no PII)
    """
    counts = _compute_counts(profiles)
    influence_dist = _compute_influence_distribution(profiles)
    stance_dist = _compute_stance_distribution(profiles)
    entity_breakdown = _compute_entity_type_breakdown(profiles)
    risk_score, risk_level = _compute_risk(influence_dist, stance_dist)
    opposition = _build_opposition_summary(profiles)
    support = _build_support_summary(profiles)

    # Generate themes via LLM
    print(f"\n📊 Generating community themes from {len(profiles)} neighbor profiles...")
    themes = await _generate_themes(profiles, location_context)
    if themes:
        print(f"   ✅ Generated {len(themes)} community themes")
    else:
        print("   ⚠️  No themes generated — falling back to empty list")

    result = NeighborAggregateResult(
        total_screened=counts["total_screened"],
        residents_count=counts["residents_count"],
        organizations_count=counts["organizations_count"],
        adjacent_count=counts["adjacent_count"],
        influence_distribution=influence_dist,
        stance_distribution=stance_dist,
        entity_type_breakdown=entity_breakdown,
        risk_score=risk_score,
        risk_level=risk_level,
        themes=themes,
        opposition_summary=opposition,
        support_summary=support,
        overview_summary=overview_summary,
        location_context=location_context,
        city=city,
        county=county,
        state=state,
        run_id=run_id,
        runtime_minutes=runtime_minutes,
        map_image_path=map_image_path,
        map_ring_stats=map_ring_stats,
        map_metadata=map_metadata,
    )

    return result.dict()
