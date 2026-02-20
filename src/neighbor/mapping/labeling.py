"""Parcel label generation for map visualization."""

import math
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

from ..models.schemas import NeighborProfile
from ..utils.pin import normalize_pin
from .geometry_utils import get_centroid
from .styles import get_marker_color


@dataclass
class ParcelLabel:
    """A label to display on a parcel."""

    text: str  # Display text (e.g., "FLACH" or short name)
    full_name: str  # Full name for legend
    lon: float  # Centroid longitude
    lat: float  # Centroid latitude
    marker_number: int  # Numeric ID for marker (0=target, 1-35 for neighbors)
    marker_char: str  # Character for marker (T, 1-9, a-z)
    color: str  # Hex color without # (e.g., "DC143C")
    is_target: bool = False
    is_adjacent: bool = False
    influence: Optional[str] = None
    stance: Optional[str] = None
    pin: str = ""


@dataclass
class LegendEntry:
    """Entry for the map legend."""

    marker_char: str
    label_text: str
    full_name: str
    color: str
    influence: Optional[str] = None
    stance: Optional[str] = None
    is_adjacent: bool = False


class LabelGenerator:
    """Generate labels for map parcels."""

    def __init__(self, max_label_length: int = 8):
        """
        Initialize label generator.

        Args:
            max_label_length: Maximum characters for label text
        """
        self.max_label_length = max_label_length
        self._marker_counter = 0

    def reset(self):
        """Reset the marker counter for a new map."""
        self._marker_counter = 0

    def get_label_text(
        self,
        neighbor: Optional[NeighborProfile],
        pin: str,
        is_target: bool = False,
    ) -> str:
        """
        Generate anonymized label text for a parcel.

        Uses influence-based identifiers instead of names to avoid PII.

        Args:
            neighbor: NeighborProfile or None
            pin: Parcel identification number
            is_target: Whether this is the target site

        Returns:
            Label text (max 8 characters)
        """
        if is_target:
            return "TARGET"

        if not neighbor:
            return "PARCEL"

        # Use influence-based label prefix
        influence = (neighbor.community_influence or "Low").capitalize()
        prefix = influence[0]  # H, M, or L
        return f"{prefix}{neighbor.neighbor_id.replace('N-', '')}" if neighbor.neighbor_id else prefix

    def _extract_last_name(self, name: str) -> str:
        """
        Extract last name from full name.

        Handles formats like:
        - "FLACH, MARK C"
        - "MARK C FLACH"
        - "John Smith Jr."
        """
        if not name:
            return "UNKNOWN"

        # Remove common suffixes
        for suffix in [" JR", " SR", " II", " III", " IV"]:
            name = name.replace(suffix, "").replace(suffix.lower(), "")

        parts = name.split()
        if not parts:
            return "UNKNOWN"

        # Handle "LAST, FIRST" format
        if "," in name:
            return parts[0].replace(",", "").upper()[: self.max_label_length]

        # Handle "FIRST MIDDLE LAST" format
        if len(parts) >= 2:
            return parts[-1].upper()[: self.max_label_length]

        return parts[0].upper()[: self.max_label_length]

    def _abbreviate_org(self, name: str) -> str:
        """
        Abbreviate organization name.

        Removes common suffixes and truncates.
        """
        if not name:
            return "ORG"

        # Remove common business suffixes
        for suffix in [" LLC", " INC", " CORP", " LTD", " CO", " LP", " LLP"]:
            name = name.replace(suffix, "").replace(suffix.lower(), "")

        # Take first word or first 8 chars
        parts = name.upper().split()
        if parts:
            return parts[0][: self.max_label_length]

        return name.upper()[: self.max_label_length]

    def _format_pin(self, pin: str) -> str:
        """
        Format PIN for display.

        Takes last 6 meaningful characters.
        """
        if not pin:
            return "N/A"

        # Remove common separators for length calculation
        clean = pin.replace("-", "").replace(".", "").replace(" ", "")

        if len(clean) > 6:
            return clean[-6:]
        return clean

    def _get_marker_char(self, number: int) -> str:
        """
        Convert marker number to character.

        0 -> T (target)
        1-9 -> 1-9
        10-35 -> a-z
        """
        if number == 0:
            return "t"
        if number <= 9:
            return str(number)
        if number <= 35:
            return chr(ord("a") + number - 10)
        # Fallback for > 35
        return str(number % 10)

    def _generate_target_label(
        self,
        geometry: Dict[str, Any],
        pin: str,
    ) -> Optional[ParcelLabel]:
        """Generate label for target parcel."""
        if not geometry:
            return None

        try:
            lon, lat = get_centroid(geometry)
        except Exception:
            return None

        return ParcelLabel(
            text="TARGET",
            full_name="Target Site",
            lon=lon,
            lat=lat,
            marker_number=0,
            marker_char="t",
            color="FFD700",  # Gold
            is_target=True,
            is_adjacent=False,
            influence=None,
            stance=None,
            pin="",
        )

    def _generate_neighbor_label(
        self,
        geometry: Dict[str, Any],
        neighbor: NeighborProfile,
        pin: str,
        marker_num: int,
        is_adjacent: bool,
    ) -> Optional[ParcelLabel]:
        """Generate label for a neighbor parcel with assigned marker number."""
        if not geometry:
            return None

        try:
            lon, lat = get_centroid(geometry)
        except Exception:
            return None

        label_text = self.get_label_text(neighbor, pin, is_target=False)
        marker_char = self._get_marker_char(marker_num)
        color = get_marker_color(neighbor.community_influence, neighbor.noted_stance)

        # Build anonymous label from influence + stance
        influence_str = (neighbor.community_influence or "Low").capitalize()
        stance_str = (neighbor.noted_stance or "Unknown").capitalize()
        anonymous_label = f"{influence_str} Influence — {stance_str}"

        return ParcelLabel(
            text=label_text,
            full_name=anonymous_label,
            lon=lon,
            lat=lat,
            marker_number=marker_num,
            marker_char=marker_char,
            color=color,
            is_target=False,
            is_adjacent=is_adjacent,
            influence=neighbor.community_influence,
            stance=neighbor.noted_stance,
            pin="",
        )

    def generate_label(
        self,
        geometry: Dict[str, Any],
        neighbor: Optional[NeighborProfile] = None,
        pin: str = "",
        is_target: bool = False,
        is_adjacent: bool = False,
    ) -> Optional[ParcelLabel]:
        """
        Generate a single parcel label.

        Args:
            geometry: GeoJSON geometry for centroid calculation
            neighbor: NeighborProfile or None
            pin: Parcel identification number
            is_target: Whether this is the target site
            is_adjacent: Whether parcel is adjacent to target

        Returns:
            ParcelLabel or None if geometry invalid or Low influence (no marker)
        """
        if not geometry:
            return None

        # Skip markers for Low influence neighbors (they only get polygon outlines)
        if neighbor and neighbor.community_influence == "Low":
            return None

        # Get centroid for label placement
        try:
            lon, lat = get_centroid(geometry)
        except Exception:
            return None

        # Generate label text
        label_text = self.get_label_text(neighbor, pin, is_target)

        # Assign marker number
        if is_target:
            marker_num = 0
        else:
            self._marker_counter += 1
            marker_num = self._marker_counter

        marker_char = self._get_marker_char(marker_num)

        # Get color
        if is_target:
            color = "FFD700"  # Gold
        elif neighbor:
            color = get_marker_color(neighbor.community_influence, neighbor.noted_stance)
        else:
            color = "808080"  # Gray

        # Get influence and stance for legend
        influence = neighbor.community_influence if neighbor else None
        stance = neighbor.noted_stance if neighbor else None

        # Build anonymous label
        if is_target:
            anonymous_label = "Target Site"
        elif neighbor:
            inf_str = (neighbor.community_influence or "Low").capitalize()
            st_str = (neighbor.noted_stance or "Unknown").capitalize()
            anonymous_label = f"{inf_str} Influence — {st_str}"
        else:
            anonymous_label = "Parcel"

        return ParcelLabel(
            text=label_text,
            full_name=anonymous_label,
            lon=lon,
            lat=lat,
            marker_number=marker_num,
            marker_char=marker_char,
            color=color,
            is_target=is_target,
            is_adjacent=is_adjacent,
            influence=influence,
            stance=stance,
            pin="",
        )

    def generate_labels_for_features(
        self,
        features: List[Dict[str, Any]],
        neighbor_lookup: Dict[str, NeighborProfile],
    ) -> Tuple[List[ParcelLabel], List[LegendEntry]]:
        """
        Generate labels for all features.

        Numbers are assigned per unique owner name, so all parcels owned by
        the same entity share the same number.

        Args:
            features: List of GeoJSON features with properties
            neighbor_lookup: Map of neighbor_id to NeighborProfile

        Returns:
            Tuple of (labels list, legend entries)
        """
        self.reset()
        labels = []
        legend = []

        # First pass: collect unique owners by neighbor_id (excluding target and Low influence)
        owner_to_neighbor: Dict[str, NeighborProfile] = {}

        for feat in features:
            props = feat.get("properties", {})
            is_target = props.get("is_target", False)
            if is_target:
                continue

            pin = props.get("pin", "")
            neighbor = neighbor_lookup.get(normalize_pin(pin)) if pin else None

            # Skip Low influence (no markers for them)
            if neighbor and neighbor.community_influence == "Low":
                continue

            nid = neighbor.neighbor_id if neighbor else None
            if neighbor and nid and nid not in owner_to_neighbor:
                owner_to_neighbor[nid] = neighbor

        # Sort owners by influence (High first, then Medium) and assign numbers
        influence_order = {"High": 0, "Medium": 1}
        sorted_owners = sorted(
            owner_to_neighbor.items(),
            key=lambda x: influence_order.get(x[1].community_influence, 2)
        )
        owner_to_number: Dict[str, int] = {}
        for i, (nid, _) in enumerate(sorted_owners, start=1):
            owner_to_number[nid] = i

        # Build reverse lookup: neighbor name -> neighbor_id for matching
        name_to_nid = {n.name: nid for nid, n in owner_to_neighbor.items()}

        # Second pass: generate labels using assigned numbers
        for feat in features:
            geometry = feat.get("geometry")
            props = feat.get("properties", {})

            is_target = props.get("is_target", False)
            pin = props.get("pin", "")
            is_adjacent = props.get("is_adjacent", False)

            neighbor = neighbor_lookup.get(normalize_pin(pin)) if pin else None

            if is_target:
                # Target parcel
                label = self._generate_target_label(geometry, pin)
                if label:
                    labels.append(label)
            elif neighbor and neighbor.community_influence != "Low":
                # Get assigned number for this owner
                nid = neighbor.neighbor_id
                marker_num = owner_to_number.get(nid, 0)
                label = self._generate_neighbor_label(
                    geometry=geometry,
                    neighbor=neighbor,
                    pin=pin,
                    marker_num=marker_num,
                    is_adjacent=is_adjacent,
                )
                if label:
                    labels.append(label)

        # Generate legend entries (one per unique owner — anonymous labels)
        for nid, marker_num in owner_to_number.items():
            neighbor = owner_to_neighbor[nid]
            marker_char = self._get_marker_char(marker_num)
            color = get_marker_color(neighbor.community_influence, neighbor.noted_stance)

            influence_str = (neighbor.community_influence or "Low").capitalize()
            stance_str = (neighbor.noted_stance or "Unknown").capitalize()
            entity_str = "Resident" if neighbor.entity_category == "Resident" else "Entity"

            legend.append(
                LegendEntry(
                    marker_char=marker_char.upper(),
                    label_text=f"{entity_str} ({influence_str})",
                    full_name=f"{entity_str} — {influence_str} Influence — {stance_str}",
                    color=color,
                    influence=neighbor.community_influence,
                    stance=neighbor.noted_stance,
                    is_adjacent=neighbor.owns_adjacent_parcel == "Yes",
                )
            )

        return labels, legend

    def _offset_overlapping_markers(
        self, labels: List[ParcelLabel], threshold_deg: float = 0.0008
    ) -> List[Tuple[ParcelLabel, float, float]]:
        """
        Detect and offset overlapping markers.

        Args:
            labels: List of ParcelLabel objects
            threshold_deg: Distance threshold in degrees (~90 meters at mid-latitudes)

        Returns:
            List of (label, adjusted_lon, adjusted_lat) tuples
        """
        if not labels:
            return []

        # Start with original positions
        positions = [(label, label.lon, label.lat) for label in labels]

        # Check each pair for overlap
        offset_amount = 0.0006  # ~67 meters at mid-latitudes

        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                label_i, lon_i, lat_i = positions[i]
                label_j, lon_j, lat_j = positions[j]

                # Calculate distance
                dist = math.sqrt((lon_i - lon_j) ** 2 + (lat_i - lat_j) ** 2)

                if dist < threshold_deg:
                    # Markers overlap - offset them in opposite directions
                    if dist > 0:
                        # Calculate direction from j to i
                        dx = (lon_i - lon_j) / dist
                        dy = (lat_i - lat_j) / dist
                    else:
                        # Exactly same position - offset along 45 degrees
                        dx = 0.707
                        dy = 0.707

                    # Offset marker i in direction away from j
                    new_lon_i = lon_i + dx * offset_amount
                    new_lat_i = lat_i + dy * offset_amount
                    positions[i] = (label_i, new_lon_i, new_lat_i)

                    # Offset marker j in opposite direction
                    new_lon_j = lon_j - dx * offset_amount
                    new_lat_j = lat_j - dy * offset_amount
                    positions[j] = (label_j, new_lon_j, new_lat_j)

        return positions

    def build_marker_overlay(self, labels: List[ParcelLabel]) -> str:
        """
        Build Mapbox marker overlay string.

        Uses numbered pins: pin-l-{char}+{color}({lon},{lat})
        Automatically offsets overlapping markers.

        Args:
            labels: List of ParcelLabel objects

        Returns:
            Comma-separated marker overlay string
        """
        markers = []

        # Get adjusted positions for overlapping markers
        adjusted_positions = self._offset_overlapping_markers(labels)

        for label, lon, lat in adjusted_positions:
            # Mapbox marker format: pin-{size}-{label}+{color}({lon},{lat})
            # size: s (small), l (large)
            # label: single alphanumeric character
            marker = f"pin-l-{label.marker_char}+{label.color}({lon:.6f},{lat:.6f})"
            markers.append(marker)

        return ",".join(markers)

    def format_legend_html(self, legend: List[LegendEntry]) -> str:
        """
        Format legend entries as HTML for PDF inclusion.

        Args:
            legend: List of LegendEntry objects

        Returns:
            HTML string for legend
        """
        if not legend:
            return ""

        rows = []
        for entry in legend:
            influence_badge = ""
            if entry.influence:
                influence_badge = f'<span class="influence-{entry.influence.lower()}">{entry.influence}</span>'

            stance_badge = ""
            if entry.stance and entry.stance not in ["neutral", "unknown"]:
                stance_badge = f'<span class="stance-{entry.stance}">{entry.stance.title()}</span>'

            adjacent_badge = ""
            if entry.is_adjacent:
                adjacent_badge = '<span class="adjacent">Adjacent</span>'

            row = f"""
            <tr>
                <td class="marker" style="color: #{entry.color}; font-weight: bold;">{entry.marker_char}</td>
                <td class="description">{entry.full_name}</td>
                <td class="badges">{influence_badge} {stance_badge} {adjacent_badge}</td>
            </tr>
            """
            rows.append(row)

        return f"""
        <table class="map-legend-table">
            <thead>
                <tr>
                    <th>#</th>
                    <th>Description</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows)}
            </tbody>
        </table>
        """
