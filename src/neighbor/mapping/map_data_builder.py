"""Build GeoJSON features for map rendering."""

import re
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass

from ..models.schemas import NeighborProfile
from .styles import STYLES, get_style_for_neighbor, ParcelStyle


def normalize_pin(pin: str) -> str:
    """Normalize PIN by collapsing multiple whitespace to single space.

    Regrid returns PINs like "04  22000400000000" (2 spaces), but LLMs
    normalize to "04 22000400000000" (1 space). This ensures lookups match.
    """
    if not pin:
        return ""
    return re.sub(r'\s+', ' ', pin.strip())


@dataclass
class MapFeature:
    """A parcel feature ready for map rendering."""

    geometry: Dict[str, Any]
    style: ParcelStyle
    label: str
    neighbor_id: Optional[str]
    pin: str
    is_target: bool
    is_adjacent: bool
    influence: Optional[str] = None
    stance: Optional[str] = None


class MapDataBuilder:
    """Build map data from neighbor screening results."""

    def __init__(
        self,
        target_parcel: Dict[str, Any],
        raw_parcels: List[Dict[str, Any]],
        neighbor_profiles: List[NeighborProfile],
    ):
        """
        Initialize the map data builder.

        Args:
            target_parcel: Target parcel info with geometry
            raw_parcels: Raw parcel features from Regrid
            neighbor_profiles: Enriched neighbor profiles
        """
        self.target_parcel = target_parcel
        self.raw_parcels = raw_parcels
        self.neighbor_profiles = neighbor_profiles

        # Build PIN → geometry lookup
        self.pin_to_geometry = self._build_pin_geometry_map()

        # Build PIN → neighbor profile lookup
        self.pin_to_neighbor = self._build_pin_neighbor_map()

    def _build_pin_geometry_map(self) -> Dict[str, Dict[str, Any]]:
        """Map parcel numbers to their geometries.

        PINs are normalized to handle whitespace variations between
        Regrid format and LLM-processed format.
        """
        result = {}

        for parcel in self.raw_parcels:
            props = parcel.get("properties", {})
            fields = props.get("fields", {})

            # Try multiple locations for PIN
            pin = (
                fields.get("parcelnumb")
                or props.get("parcelnumb")
                or fields.get("apn")
                or props.get("apn")
            )

            geometry = parcel.get("geometry")

            if pin and geometry:
                result[normalize_pin(pin)] = geometry

        return result

    def _build_pin_neighbor_map(self) -> Dict[str, NeighborProfile]:
        """Map parcel numbers to neighbor profiles.

        PINs are normalized to handle whitespace variations.
        """
        result = {}

        for neighbor in self.neighbor_profiles:
            for pin in neighbor.pins:
                result[normalize_pin(pin)] = neighbor

        return result

    def should_highlight(self, neighbor: NeighborProfile) -> bool:
        """
        Determine if a neighbor should be highlighted on the map.

        Criteria:
        - High, Medium, or Low community influence

        Args:
            neighbor: NeighborProfile to check

        Returns:
            True if neighbor should be highlighted
        """
        return neighbor.community_influence in ["High", "Medium", "Low"]

    def build_map_features(self) -> Tuple[List[MapFeature], Dict[str, Any]]:
        """
        Build list of features to render on map.

        Features are sorted by influence for proper z-order (borders):
        Low → Medium → High → Target (highest influence on top)

        Returns:
            Tuple of (features list, stats dict)
        """
        features = []
        target_feature = None
        processed_pins: Set[str] = set()

        stats = {
            "total_neighbors": len(self.neighbor_profiles),
            "highlighted": 0,
            "skipped_no_geometry": 0,
            "skipped_not_highlighted": 0,
            "by_influence": {"High": 0, "Medium": 0, "Low": 0},
            "by_stance": {"support": 0, "oppose": 0, "neutral": 0},
            "adjacent_highlighted": 0,
            "target_included": False,
        }

        # 1. Create target parcel feature (added last for z-order)
        target_geometry = self.target_parcel.get("geometry")
        target_pin = self.target_parcel.get("pin", "")
        if target_geometry:
            target_feature = MapFeature(
                geometry=target_geometry,
                style=STYLES["target"],
                label="TARGET",
                neighbor_id=None,
                pin=target_pin,
                is_target=True,
                is_adjacent=False,
            )
            stats["target_included"] = True
            # Mark target PIN as processed so it's not rendered again as a neighbor
            if target_pin:
                processed_pins.add(target_pin)

        # 2. Process each neighbor
        for neighbor in self.neighbor_profiles:
            if not self.should_highlight(neighbor):
                stats["skipped_not_highlighted"] += 1
                continue

            # Get style for this neighbor
            style = get_style_for_neighbor(
                influence=neighbor.community_influence,
                stance=neighbor.noted_stance,
                is_adjacent=neighbor.owns_adjacent_parcel == "Yes",
            )

            if not style:
                continue

            stats["highlighted"] += 1

            # Track by category
            if neighbor.community_influence in ["High", "Medium", "Low"]:
                stats["by_influence"][neighbor.community_influence] += 1

            if neighbor.noted_stance in ["support", "oppose", "neutral"]:
                stats["by_stance"][neighbor.noted_stance] += 1

            if neighbor.owns_adjacent_parcel == "Yes":
                stats["adjacent_highlighted"] += 1

            # Add feature for each PIN owned by this neighbor
            for pin in neighbor.pins:
                norm_pin = normalize_pin(pin)
                if norm_pin in processed_pins:
                    continue

                geometry = self.pin_to_geometry.get(norm_pin)
                if not geometry:
                    stats["skipped_no_geometry"] += 1
                    continue

                processed_pins.add(norm_pin)

                features.append(
                    MapFeature(
                        geometry=geometry,
                        style=style,
                        label=neighbor.name,
                        neighbor_id=neighbor.neighbor_id,
                        pin=pin,
                        is_target=False,
                        is_adjacent=neighbor.owns_adjacent_parcel == "Yes",
                        influence=neighbor.community_influence,
                        stance=neighbor.noted_stance,
                    )
                )

        # 3. Sort features by influence for z-order: Low → Medium → High
        influence_z_order = {"Low": 0, "Medium": 1, "High": 2}
        features.sort(key=lambda f: influence_z_order.get(f.influence, -1))

        # 4. Add target last so it's always on top
        if target_feature:
            features.append(target_feature)

        return features, stats

    def to_geojson_features(self, features: List[MapFeature]) -> List[Dict[str, Any]]:
        """
        Convert MapFeatures to GeoJSON features with SimpleStyle properties.

        Args:
            features: List of MapFeature objects

        Returns:
            List of GeoJSON Feature dicts
        """
        geojson_features = []

        for feat in features:
            # Get SimpleStyle properties from style
            style_props = feat.style.to_simplestyle()

            # Add custom properties for labeling and reference
            style_props.update(
                {
                    "label": feat.label,
                    "is_target": feat.is_target,
                    "is_adjacent": feat.is_adjacent,
                    "neighbor_id": feat.neighbor_id,
                    "pin": feat.pin,
                    "influence": feat.influence,
                    "stance": feat.stance,
                }
            )

            geojson_features.append(
                {
                    "type": "Feature",
                    "properties": style_props,
                    "geometry": feat.geometry,
                }
            )

        return geojson_features

    def get_all_geometries(self, features: List[MapFeature]) -> List[Dict[str, Any]]:
        """
        Extract all geometries from features for bounding box calculation.

        Args:
            features: List of MapFeature objects

        Returns:
            List of geometry dicts
        """
        return [f.geometry for f in features if f.geometry]
