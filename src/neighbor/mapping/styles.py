"""Color and style constants for neighbor map visualization."""

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class ParcelStyle:
    """Style configuration for a parcel polygon."""

    fill_color: str  # Hex without # (e.g., "FFD700")
    fill_opacity: float
    stroke_color: str  # Hex without #
    stroke_opacity: float
    stroke_width: int

    def to_simplestyle(self) -> dict:
        """Convert to SimpleStyle properties for GeoJSON."""
        return {
            "fill": f"#{self.fill_color}",
            "fill-opacity": self.fill_opacity,
            "stroke": f"#{self.stroke_color}",
            "stroke-opacity": self.stroke_opacity,
            "stroke-width": self.stroke_width,
        }


# Style definitions for different parcel categories
STYLES = {
    "target": ParcelStyle(
        fill_color="FFD700",  # Gold
        fill_opacity=0.6,
        stroke_color="B8860B",  # Dark Gold
        stroke_opacity=1.0,
        stroke_width=3,
    ),
    "high_influence": ParcelStyle(
        fill_color="DC143C",  # Crimson
        fill_opacity=0.4,
        stroke_color="8B0000",  # Dark Red
        stroke_opacity=1.0,
        stroke_width=2,
    ),
    "medium_influence": ParcelStyle(
        fill_color="FF8C00",  # Dark Orange
        fill_opacity=0.4,
        stroke_color="FF4500",  # Orange Red
        stroke_opacity=1.0,
        stroke_width=2,
    ),
    "oppose_stance": ParcelStyle(
        fill_color="8B0000",  # Dark Red
        fill_opacity=0.5,
        stroke_color="4A0000",  # Darker Red
        stroke_opacity=1.0,
        stroke_width=2,
    ),
    "support_stance": ParcelStyle(
        fill_color="228B22",  # Forest Green
        fill_opacity=0.4,
        stroke_color="006400",  # Dark Green
        stroke_opacity=1.0,
        stroke_width=2,
    ),
    "adjacent_border": ParcelStyle(
        fill_color="000000",
        fill_opacity=0.0,  # No fill - border only
        stroke_color="0000FF",  # Blue
        stroke_opacity=1.0,
        stroke_width=3,
    ),
    "low_influence": ParcelStyle(
        fill_color="808080",  # Gray
        fill_opacity=0.2,
        stroke_color="606060",  # Dark Gray
        stroke_opacity=0.8,
        stroke_width=1,
    ),
}

# Marker colors matching parcel styles (for numbered pins)
MARKER_COLORS = {
    "target": "FFD700",
    "high_influence": "DC143C",
    "medium_influence": "FF8C00",
    "oppose_stance": "8B0000",
    "support_stance": "228B22",
    "adjacent": "0000FF",
    "default": "808080",
}


def get_style_for_neighbor(
    influence: Optional[Literal["High", "Medium", "Low", "Unknown"]],
    stance: Optional[Literal["support", "oppose", "neutral", "unknown"]],
    is_adjacent: bool = False,
) -> Optional[ParcelStyle]:
    """
    Determine style based on influence and stance with priority rules.

    Priority order:
    1. Oppose stance (highest alert - red)
    2. High influence (crimson)
    3. Support stance (green)
    4. Medium influence (orange)
    5. None (not highlighted)

    Args:
        influence: Community influence level
        stance: Noted development stance
        is_adjacent: Whether parcel is adjacent to target

    Returns:
        ParcelStyle or None if neighbor shouldn't be highlighted
    """
    # Priority 1: Oppose stance (highest alert)
    if stance == "oppose":
        return STYLES["oppose_stance"]

    # Priority 2: High influence
    if influence == "High":
        return STYLES["high_influence"]

    # Priority 3: Support stance
    if stance == "support":
        return STYLES["support_stance"]

    # Priority 4: Medium influence
    if influence == "Medium":
        return STYLES["medium_influence"]

    # No special styling - won't be rendered on map
    return None


def get_style_category(
    influence: Optional[Literal["High", "Medium", "Low", "Unknown"]],
    stance: Optional[Literal["support", "oppose", "neutral", "unknown"]],
) -> str:
    """
    Get the style category name for a neighbor.

    Returns:
        Category name (e.g., "high_influence", "oppose_stance")
    """
    if stance == "oppose":
        return "oppose_stance"
    if influence == "High":
        return "high_influence"
    if stance == "support":
        return "support_stance"
    if influence == "Medium":
        return "medium_influence"
    return "default"


def get_marker_color(
    influence: Optional[Literal["High", "Medium", "Low", "Unknown"]],
    stance: Optional[Literal["support", "oppose", "neutral", "unknown"]],
) -> str:
    """Get marker color hex for a neighbor."""
    category = get_style_category(influence, stance)
    return MARKER_COLORS.get(category, MARKER_COLORS["default"])
