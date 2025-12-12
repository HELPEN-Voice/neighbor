# src/neighbor/config/assessment_ratios.py
"""
State-level assessment ratios for converting assessed value to estimated market value.

Assessment Ratio = Assessed Value / Market Value
To get Market Value: Market Value = Assessed Value / Ratio

These are approximate statewide ratios. Some states (like IL) vary significantly
by county. For critical decisions, county-specific ratios should be researched.

Sources: State Department of Revenue / Property Tax Division publications (2024/2025)
"""

ASSESSMENT_RATIOS = {
    # States where assessed = market value (or close to it)
    "TX": 1.0,
    "CA": 1.0,  # Note: Prop 13 means values may be artificially low
    "NJ": 1.0,
    "FL": 1.0,
    "AZ": 1.0,
    "WA": 1.0,
    "OR": 1.0,
    "NV": 1.0,
    "ID": 1.0,
    "MT": 1.0,
    "NM": 0.333,
    "UT": 1.0,
    "WY": 0.095,  # 9.5% for residential

    # States with significant assessment ratios
    "CO": 0.0715,  # 7.15% for residential (critical - very low ratio)
    "IL": 0.3333,  # 33.33% standard (Cook County is different)
    "OH": 0.35,    # 35%
    "GA": 0.40,    # 40%
    "NY": 0.80,    # Varies by municipality, ~80% average
    "PA": 0.25,    # Varies significantly by county
    "MI": 0.50,    # 50% (taxable value)
    "IN": 1.0,     # Market value based
    "WI": 1.0,     # Full market value
    "MN": 1.0,     # Market value
    "IA": 0.466,   # ~46.6% for residential
    "MO": 0.19,    # 19% for residential
    "KS": 0.115,   # 11.5% for residential
    "NE": 1.0,     # Market value
    "SD": 0.85,    # 85% of market
    "ND": 0.50,    # 50% for residential
    "OK": 0.11,    # 11% for most property
    "AR": 0.20,    # 20%
    "LA": 0.10,    # 10%
    "MS": 0.15,    # 15% for residential
    "AL": 0.10,    # 10% for Class III (residential)
    "TN": 0.25,    # 25% for residential
    "KY": 1.0,     # 100% fair cash value
    "WV": 0.60,    # 60%
    "VA": 1.0,     # Fair market value
    "NC": 1.0,     # Market value
    "SC": 0.04,    # 4% for owner-occupied residential
    "MD": 1.0,     # Full cash value
    "DE": 0.50,    # Varies, ~50%
    "CT": 0.70,    # 70%
    "RI": 1.0,     # Full value
    "MA": 1.0,     # Full and fair cash value
    "VT": 1.0,     # Fair market value
    "NH": 1.0,     # Full market value
    "ME": 1.0,     # Just value (market)

    # Default for unknown states
    "DEFAULT": 1.0,
}

# Notes for states with special considerations
ASSESSMENT_NOTES = {
    "CO": "Uses ~7.15% for residential. This is a critical multiplier - assessed values will appear very low.",
    "IL": "Standard is 33.33% outside Cook County. Cook County uses different rates by property class.",
    "CA": "Prop 13 means values are artificially low based on purchase price; use as 'minimum floor'.",
    "SC": "4% for owner-occupied is extremely low; 6% for other residential.",
    "WY": "9.5% for residential, 11.5% for commercial.",
    "PA": "Varies dramatically by county - some haven't reassessed in decades.",
}


def get_assessment_ratio(state_code: str) -> float:
    """Get the assessment ratio for a state code."""
    return ASSESSMENT_RATIOS.get(state_code.upper(), ASSESSMENT_RATIOS["DEFAULT"])


def normalize_to_market_value(assessed_value: float, state_code: str, value_type: str = None) -> float:
    """
    Convert assessed value to estimated market value.

    Args:
        assessed_value: The raw value from Regrid
        state_code: Two-letter state abbreviation
        value_type: Optional parvaltype field (e.g., "MARKET", "ASSESSED")

    Returns:
        Estimated market value
    """
    if assessed_value is None or assessed_value <= 0:
        return 0.0

    # If value_type indicates it's already market value, return as-is
    if value_type:
        vtype_lower = str(value_type).lower()
        if "market" in vtype_lower or "appraised" in vtype_lower or "fair" in vtype_lower:
            return assessed_value

    # Apply state ratio
    ratio = get_assessment_ratio(state_code)
    if ratio <= 0:
        return assessed_value

    return assessed_value / ratio
