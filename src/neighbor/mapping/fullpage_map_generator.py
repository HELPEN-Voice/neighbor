"""Full-page map generation for neighbor screens.

This module generates a large full-page map that includes ALL neighbors
regardless of influence level. It is completely separate from the existing
map_generator.py to avoid affecting other workflows.
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from pathlib import Path

from ..models.schemas import NeighborProfile
from .map_data_builder import MapDataBuilder
from .mapbox_client import MapboxClient, MapGenerationResult
from .geometry_utils import get_centroid
from .styles import get_marker_color

logger = logging.getLogger(__name__)


@dataclass
class FullPageLabel:
    """A label for the full-page map."""
    text: str
    full_name: str
    lon: float
    lat: float
    marker_number: int
    marker_char: str
    color: str
    is_target: bool = False
    is_adjacent: bool = False
    influence: Optional[str] = None
    stance: Optional[str] = None
    pin: str = ""


@dataclass
class FullPageMapResult:
    """Result of full-page map generation."""
    success: bool
    image_path: Optional[str]
    labels: List[Dict[str, Any]]
    metadata: Dict[str, Any]


class FullPageLabelGenerator:
    """Generate labels for full-page map - includes ALL influence levels."""

    def __init__(self, max_label_length: int = 8):
        self.max_label_length = max_label_length

    def _get_marker_char(self, number: int) -> str:
        """Convert marker number to character."""
        if number == 0:
            return "t"
        if number <= 9:
            return str(number)
        if number <= 35:
            return chr(ord("a") + number - 10)
        return str(number % 10)

    def _extract_last_name(self, name: str) -> str:
        """Extract last name from full name."""
        if not name:
            return "UNKNOWN"
        for suffix in [" JR", " SR", " II", " III", " IV"]:
            name = name.replace(suffix, "").replace(suffix.lower(), "")
        parts = name.split()
        if not parts:
            return "UNKNOWN"
        if "," in name:
            return parts[0].replace(",", "").upper()[:self.max_label_length]
        if len(parts) >= 2:
            return parts[-1].upper()[:self.max_label_length]
        return parts[0].upper()[:self.max_label_length]

    def _abbreviate_org(self, name: str) -> str:
        """Abbreviate organization name."""
        if not name:
            return "ORG"
        for suffix in [" LLC", " INC", " CORP", " LTD", " CO", " LP", " LLP"]:
            name = name.replace(suffix, "").replace(suffix.lower(), "")
        parts = name.upper().split()
        if parts:
            return parts[0][:self.max_label_length]
        return name.upper()[:self.max_label_length]

    def _get_label_text(self, neighbor: Optional[NeighborProfile], pin: str) -> str:
        """Generate label text for a neighbor parcel."""
        if not neighbor:
            clean = pin.replace("-", "").replace(".", "").replace(" ", "")
            return clean[-6:] if len(clean) > 6 else clean
        if neighbor.entity_category == "Resident":
            return self._extract_last_name(neighbor.name)
        return self._abbreviate_org(neighbor.name)

    def generate_labels_for_features(
        self,
        features: List[Dict[str, Any]],
        neighbor_lookup: Dict[str, NeighborProfile],
    ) -> List[FullPageLabel]:
        """
        Generate labels for ALL features including Low influence.

        Numbers are assigned per unique owner name.
        """
        labels = []

        # First pass: collect unique owners (ALL influence levels)
        owner_to_neighbor: Dict[str, NeighborProfile] = {}

        for feat in features:
            props = feat.get("properties", {})
            is_target = props.get("is_target", False)
            if is_target:
                continue

            pin = props.get("pin", "")
            neighbor = neighbor_lookup.get(pin) if pin else None

            # Include ALL neighbors (no influence filtering)
            if neighbor and neighbor.name not in owner_to_neighbor:
                owner_to_neighbor[neighbor.name] = neighbor

        # Sort owners by influence (High first, then Medium, then Low)
        influence_order = {"High": 0, "Medium": 1, "Low": 2, "Unknown": 3}
        sorted_owners = sorted(
            owner_to_neighbor.items(),
            key=lambda x: influence_order.get(x[1].community_influence, 3)
        )
        owner_to_number: Dict[str, int] = {}
        for i, (name, _) in enumerate(sorted_owners, start=1):
            owner_to_number[name] = i

        # Second pass: generate labels
        for feat in features:
            geometry = feat.get("geometry")
            props = feat.get("properties", {})

            is_target = props.get("is_target", False)
            pin = props.get("pin", "")
            is_adjacent = props.get("is_adjacent", False)

            neighbor = neighbor_lookup.get(pin) if pin else None

            if not geometry:
                continue

            try:
                lon, lat = get_centroid(geometry)
            except Exception:
                continue

            if is_target:
                labels.append(FullPageLabel(
                    text="TARGET",
                    full_name=f"PIN: {pin}",
                    lon=lon,
                    lat=lat,
                    marker_number=0,
                    marker_char="t",
                    color="FFD700",
                    is_target=True,
                    is_adjacent=False,
                    influence=None,
                    stance=None,
                    pin=pin,
                ))
            elif neighbor:
                marker_num = owner_to_number.get(neighbor.name, 0)
                marker_char = self._get_marker_char(marker_num)
                color = get_marker_color(neighbor.community_influence, neighbor.noted_stance)
                label_text = self._get_label_text(neighbor, pin)

                labels.append(FullPageLabel(
                    text=label_text,
                    full_name=neighbor.name,
                    lon=lon,
                    lat=lat,
                    marker_number=marker_num,
                    marker_char=marker_char,
                    color=color,
                    is_target=False,
                    is_adjacent=is_adjacent,
                    influence=neighbor.community_influence,
                    stance=neighbor.noted_stance,
                    pin=pin,
                ))

        return labels

    def build_marker_overlay(self, labels: List[FullPageLabel]) -> str:
        """Build Mapbox marker overlay string."""
        markers = []
        for label in labels:
            marker = f"pin-l-{label.marker_char}+{label.color}({label.lon:.6f},{label.lat:.6f})"
            markers.append(marker)
        return ",".join(markers)


class FullPageMapGenerator:
    """Generate full-page map for neighbor screens."""

    def __init__(
        self,
        target_parcel: Dict[str, Any],
        raw_parcels: List[Dict[str, Any]],
        neighbor_profiles: List[NeighborProfile],
        mapbox_token: str,
        output_dir: Optional[str] = None,
        style: str = "satellite-streets-v12",
        username: str = "mapbox",
        width: int = 1920,
        height: int = 1080,
        padding: int = 80,
        retina: bool = True,
    ):
        self.target_parcel = target_parcel
        self.raw_parcels = raw_parcels
        self.neighbor_profiles = neighbor_profiles
        self.mapbox_token = mapbox_token
        self.output_dir = output_dir or self._get_default_output_dir()
        self.style = style
        self.username = username
        self.width = width
        self.height = height
        self.padding = padding
        self.retina = retina

        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

    def _get_default_output_dir(self) -> str:
        base = Path(__file__).parent.parent / "neighbor_map_outputs"
        return str(base)

    def generate(self, run_id: Optional[str] = None) -> FullPageMapResult:
        """Generate full-page map image."""
        run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        logger.info(f"Starting full-page map generation for run: {run_id}")

        # Build map data
        builder = MapDataBuilder(
            target_parcel=self.target_parcel,
            raw_parcels=self.raw_parcels,
            neighbor_profiles=self.neighbor_profiles,
        )

        features, stats = builder.build_map_features()
        logger.info(f"Built {len(features)} features for full-page map")

        if not features:
            return FullPageMapResult(
                success=False,
                image_path=None,
                labels=[],
                metadata={"error": "No features to render"},
            )

        # Convert to GeoJSON
        geojson_features = builder.to_geojson_features(features)

        # Generate labels (ALL influence levels)
        label_generator = FullPageLabelGenerator()
        labels = label_generator.generate_labels_for_features(
            geojson_features, builder.pin_to_neighbor
        )
        logger.info(f"Generated {len(labels)} labels for full-page map")

        # Build marker overlay
        marker_overlay = label_generator.build_marker_overlay(labels)

        # Generate map image
        fullpage_path = os.path.join(self.output_dir, f"{run_id}_map_fullpage.png")

        with MapboxClient(
            access_token=self.mapbox_token,
            style=self.style,
            username=self.username,
        ) as client:
            result = client.generate_static_map(
                geojson_features=geojson_features,
                marker_overlay=marker_overlay,
                width=self.width,
                height=self.height,
                padding=self.padding,
                retina=self.retina,
                output_path=fullpage_path,
            )

        # Build metadata
        metadata = {
            "run_id": run_id,
            "generated_at": datetime.now().isoformat(),
            "stats": stats,
            "strategy_used": result.strategy_used if result else None,
            "labels_count": len(labels),
            "settings": {
                "width": self.width,
                "height": self.height,
                "style": self.style,
                "retina": self.retina,
                "padding": self.padding,
            },
        }

        # Convert labels to dicts
        labels_data = [
            {
                "text": label.text,
                "full_name": label.full_name,
                "lon": label.lon,
                "lat": label.lat,
                "marker_char": label.marker_char,
                "color": label.color,
                "is_target": label.is_target,
                "is_adjacent": label.is_adjacent,
                "influence": label.influence,
                "stance": label.stance,
                "pin": label.pin,
            }
            for label in labels
        ]

        if result and result.success:
            logger.info(f"Full-page map generated: {fullpage_path}")
        else:
            logger.error("Full-page map generation failed")

        return FullPageMapResult(
            success=result.success if result else False,
            image_path=result.image_path if result else None,
            labels=labels_data,
            metadata=metadata,
        )


def generate_fullpage_neighbor_map(
    target_parcel: Dict[str, Any],
    raw_parcels: List[Dict[str, Any]],
    neighbor_profiles: List[NeighborProfile],
    mapbox_token: str,
    output_dir: Optional[str] = None,
    run_id: Optional[str] = None,
    **kwargs,
) -> FullPageMapResult:
    """Convenience function to generate a full-page neighbor map."""
    generator = FullPageMapGenerator(
        target_parcel=target_parcel,
        raw_parcels=raw_parcels,
        neighbor_profiles=neighbor_profiles,
        mapbox_token=mapbox_token,
        output_dir=output_dir,
        **kwargs,
    )
    return generator.generate(run_id=run_id)
