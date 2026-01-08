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
        fill_color="007BFF",  # Blue
        fill_opacity=0.6,
        stroke_color="007BFF",  # Blue
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
        fill_color="FFFFFF",  # White
        fill_opacity=0.0,  # No fill
        stroke_color="FFFFFF",  # White outline
        stroke_opacity=1.0,
        stroke_width=2,
    ),
}

# Marker colors matching parcel styles (for numbered pins)
MARKER_COLORS = {
    "target": "FFD700",
    "high_influence": "007BFF",  # Blue
    "medium_influence": "FF8C00",  # Orange
    "low_influence": "808080",  # Gray (no marker, but keep for reference)
    "adjacent": "0000FF",
    "default": "808080",
}


def get_style_for_neighbor(
    influence: Optional[Literal["High", "Medium", "Low", "Unknown"]],
    stance: Optional[Literal["support", "oppose", "neutral", "unknown"]],
    is_adjacent: bool = False,
) -> Optional[ParcelStyle]:
    """
    Determine style based on influence level.

    Priority order:
    1. High influence (crimson)
    2. Medium influence (orange)
    3. Low influence (gray)
    4. None (not highlighted)

    Args:
        influence: Community influence level
        stance: Noted development stance (currently unused)
        is_adjacent: Whether parcel is adjacent to target

    Returns:
        ParcelStyle or None if neighbor shouldn't be highlighted
    """
    if influence == "High":
        return STYLES["high_influence"]

    if influence == "Medium":
        return STYLES["medium_influence"]

    if influence == "Low":
        return STYLES["low_influence"]

    # No special styling - won't be rendered on map
    return None


def get_style_category(
    influence: Optional[Literal["High", "Medium", "Low", "Unknown"]],
    stance: Optional[Literal["support", "oppose", "neutral", "unknown"]],
) -> str:
    """
    Get the style category name for a neighbor based on influence level.

    Returns:
        Category name (e.g., "high_influence", "medium_influence")
    """
    if influence == "High":
        return "high_influence"
    if influence == "Medium":
        return "medium_influence"
    if influence == "Low":
        return "low_influence"
    return "default"


def get_marker_color(
    influence: Optional[Literal["High", "Medium", "Low", "Unknown"]],
    stance: Optional[Literal["support", "oppose", "neutral", "unknown"]],
) -> str:
    """Get marker color hex for a neighbor."""
    category = get_style_category(influence, stance)
    return MARKER_COLORS.get(category, MARKER_COLORS["default"])
