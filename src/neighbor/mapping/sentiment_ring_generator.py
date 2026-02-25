"""Sentiment ring map generator.

Replaces per-parcel maps with concentric ring zones showing aggregate
community sentiment by distance band.  No individual parcels are rendered,
eliminating PII re-identification via county GIS.
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.schemas import NeighborProfile
from ..utils.pin import normalize_pin
from .geometry_utils import (
    get_centroid,
    haversine_distance,
    create_circle_polygon,
    reduce_coordinate_precision,
)
from .mapbox_client import MapboxClient, MapGenerationResult

logger = logging.getLogger(__name__)

# ── Sentiment ring colors ────────────────────────────────────────────
# Each tuple: (hex fill, fill-opacity, stroke hex, label)
_RING_STYLES: Dict[str, dict] = {
    "oppose":  {"fill": "#DC2626", "fill-opacity": 0.25, "stroke": "#DC2626"},
    "support": {"fill": "#16A34A", "fill-opacity": 0.20, "stroke": "#16A34A"},
    "mixed":   {"fill": "#F59E0B", "fill-opacity": 0.20, "stroke": "#F59E0B"},
    "neutral": {"fill": "#94A3B8", "fill-opacity": 0.15, "stroke": "#94A3B8"},
    "no_data": {"fill": "#94A3B8", "fill-opacity": 0.10, "stroke": "#94A3B8"},
}

# Target parcel style
_TARGET_STYLE = {
    "fill": "#FFD700",
    "fill-opacity": 0.55,
    "stroke": "#B8860B",
    "stroke-opacity": 1.0,
    "stroke-width": 3,
}


@dataclass
class RingStat:
    """Aggregate statistics for a single distance ring."""

    ring: int           # 1, 2, 3
    inner_mi: float
    outer_mi: float
    count: int
    oppose: int
    support: int
    neutral: int
    unknown: int
    sentiment: str      # "oppose" | "support" | "mixed" | "neutral" | "no_data"

    # Influence × stance cross-tabulation
    high_oppose: int = 0
    high_support: int = 0
    high_neutral: int = 0
    high_unknown: int = 0
    medium_oppose: int = 0
    medium_support: int = 0
    medium_neutral: int = 0
    medium_unknown: int = 0


@dataclass
class SentimentRingResult:
    """Result of sentiment ring map generation."""

    success: bool
    image_path: Optional[str]
    ring_stats: List[Dict[str, Any]]
    metadata: Dict[str, Any]


def _classify_ring(oppose: int, support: int, neutral: int, unknown: int, total: int) -> str:
    """Classify a ring's sentiment from its neighbor counts."""
    if total == 0:
        return "no_data"
    oppose_ratio = oppose / total
    support_ratio = support / total
    neutral_ratio = neutral / total

    if oppose_ratio > 0.4:
        return "oppose"
    if support_ratio > 0.4:
        return "support"
    if neutral_ratio > 0.4:
        return "neutral"
    return "mixed"


def _compute_ring_boundaries(distances: List[float]) -> List[float]:
    """Return 4 boundary values [0, b1, b2, b3] defining 3 rings.

    - If max distance <= 0.5 mi: 3 equal-width bands.
    - Otherwise: 33rd / 67th percentile splits with 0.1 mi minimum width.
    """
    if not distances:
        return [0.0, 0.25, 0.5, 0.75]

    max_d = max(distances)
    if max_d <= 0.5:
        band = max_d / 3.0
        return [0.0, round(band, 4), round(band * 2, 4), round(max_d, 4)]

    sorted_d = sorted(distances)
    n = len(sorted_d)
    p33 = sorted_d[max(0, int(n * 0.33) - 1)]
    p67 = sorted_d[max(0, int(n * 0.67) - 1)]

    # Enforce minimum ring width of 0.1 mi
    min_width = 0.1
    b1 = max(p33, min_width)
    b2 = max(p67, b1 + min_width)
    b3 = max(max_d, b2 + min_width)

    return [0.0, round(b1, 4), round(b2, 4), round(b3, 4)]


class SentimentRingGenerator:
    """Generate a sentiment ring map image."""

    def __init__(
        self,
        target_parcel: Dict[str, Any],
        neighbor_profiles: List[NeighborProfile],
        raw_parcels: List[Dict[str, Any]],
        mapbox_token: str,
        output_dir: Optional[str] = None,
        style: str = "satellite-streets-v12",
        width: int = 800,
        height: int = 450,
        padding: int = 50,
        retina: bool = True,
    ):
        self.target_parcel = target_parcel
        self.neighbor_profiles = neighbor_profiles
        self.raw_parcels = raw_parcels
        self.mapbox_token = mapbox_token
        self.output_dir = output_dir or str(
            Path(__file__).parent.parent / "neighbor_map_outputs"
        )
        self.style = style
        self.width = width
        self.height = height
        self.padding = padding
        self.retina = retina

        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

    # ── Parcel lookup ────────────────────────────────────────────────

    def _build_pin_geometry_lookup(self) -> Dict[str, Dict[str, Any]]:
        """Map normalized PIN -> raw parcel geometry from Regrid data."""
        lookup: Dict[str, Dict[str, Any]] = {}
        for parcel in self.raw_parcels:
            props = parcel.get("properties", {})
            fields = props.get("fields", {})
            pin = (
                fields.get("parcelnumb")
                or props.get("parcelnumb")
                or fields.get("apn")
                or props.get("apn")
                or props.get("pin")
                or ""
            )
            geom = parcel.get("geometry")
            if pin and geom:
                lookup[normalize_pin(pin)] = geom
        return lookup

    # ── Main entry point ─────────────────────────────────────────────

    def generate(self, run_id: Optional[str] = None) -> SentimentRingResult:
        """Generate a sentiment ring map.

        Returns:
            SentimentRingResult with image path, ring stats, and metadata.
        """
        run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        logger.info(f"Generating sentiment ring map for run: {run_id}")

        # 1. Target centroid
        target_geom = self.target_parcel.get("geometry")
        if not target_geom:
            return SentimentRingResult(
                success=False,
                image_path=None,
                ring_stats=[],
                metadata={"error": "Target parcel has no geometry"},
            )

        center_lon, center_lat = get_centroid(target_geom)

        # 2. Compute distances from target centroid to each neighbor
        pin_geom = self._build_pin_geometry_lookup()
        neighbor_distances: List[tuple] = []  # (profile, distance_mi)

        for profile in self.neighbor_profiles:
            pins = profile.pins or []
            best_dist: Optional[float] = None

            for pin_val in pins:
                geom = pin_geom.get(normalize_pin(pin_val))
                if not geom:
                    continue
                try:
                    nlon, nlat = get_centroid(geom)
                    d = haversine_distance(center_lon, center_lat, nlon, nlat)
                    if best_dist is None or d < best_dist:
                        best_dist = d
                except Exception:
                    continue

            if best_dist is not None:
                neighbor_distances.append((profile, best_dist))

        logger.info(
            f"Computed distances for {len(neighbor_distances)}/{len(self.neighbor_profiles)} neighbors"
        )

        # 3. Ring boundaries
        distances = [d for _, d in neighbor_distances]
        boundaries = _compute_ring_boundaries(distances)

        # 4. Bin neighbors into rings
        ring_bins: Dict[int, List[NeighborProfile]] = {1: [], 2: [], 3: []}
        for profile, dist in neighbor_distances:
            if dist <= boundaries[1]:
                ring_bins[1].append(profile)
            elif dist <= boundaries[2]:
                ring_bins[2].append(profile)
            else:
                ring_bins[3].append(profile)

        # 5. Compute ring stats (flat + influence × stance cross-tab)
        ring_stats: List[RingStat] = []
        for ring_num in (1, 2, 3):
            profiles_in_ring = ring_bins[ring_num]
            oppose = sum(1 for p in profiles_in_ring if (p.noted_stance or "").lower() == "oppose")
            support = sum(1 for p in profiles_in_ring if (p.noted_stance or "").lower() == "support")
            neutral = sum(1 for p in profiles_in_ring if (p.noted_stance or "").lower() == "neutral")
            unknown = len(profiles_in_ring) - oppose - support - neutral
            sentiment = _classify_ring(oppose, support, neutral, unknown, len(profiles_in_ring))

            # Influence × stance cross-tabulation
            def _count(influence: str, stance: str) -> int:
                return sum(
                    1 for p in profiles_in_ring
                    if (p.community_influence or "").lower() == influence.lower()
                    and (p.noted_stance or "unknown").lower() == stance
                )

            ring_stats.append(RingStat(
                ring=ring_num,
                inner_mi=round(boundaries[ring_num - 1], 2),
                outer_mi=round(boundaries[ring_num], 2),
                count=len(profiles_in_ring),
                oppose=oppose,
                support=support,
                neutral=neutral,
                unknown=unknown,
                sentiment=sentiment,
                high_oppose=_count("High", "oppose"),
                high_support=_count("High", "support"),
                high_neutral=_count("High", "neutral"),
                high_unknown=_count("High", "unknown"),
                medium_oppose=_count("Medium", "oppose"),
                medium_support=_count("Medium", "support"),
                medium_neutral=_count("Medium", "neutral"),
                medium_unknown=_count("Medium", "unknown"),
            ))

        # 6. Build GeoJSON features
        features: List[Dict[str, Any]] = []

        # Rings (outermost first so inner rings layer on top)
        for rs in reversed(ring_stats):
            style = _RING_STYLES[rs.sentiment]
            outer_ring = create_circle_polygon(center_lon, center_lat, rs.outer_mi)

            if rs.inner_mi > 0:
                # Donut polygon: outer ring + inner hole
                inner_ring = create_circle_polygon(center_lon, center_lat, rs.inner_mi)
                # GeoJSON Polygon with hole: [outer, hole]
                # Inner ring must be wound opposite direction (clockwise for holes)
                inner_ring_reversed = list(reversed(inner_ring))
                geom = {
                    "type": "Polygon",
                    "coordinates": [outer_ring, inner_ring_reversed],
                }
            else:
                geom = {"type": "Polygon", "coordinates": [outer_ring]}

            geom = reduce_coordinate_precision(geom, 5)

            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "fill": style["fill"],
                    "fill-opacity": style["fill-opacity"],
                    "stroke": style["stroke"],
                    "stroke-opacity": 0.6,
                    "stroke-width": 1,
                },
            })

        # Target parcel polygon
        target_geom_simplified = reduce_coordinate_precision(
            target_geom, 5
        )
        features.append({
            "type": "Feature",
            "geometry": target_geom_simplified,
            "properties": _TARGET_STYLE,
        })

        # 7. Marker: single "T" pin at target centroid
        marker_overlay = f"pin-l-t+FFD700({center_lon:.6f},{center_lat:.6f})"

        # 8. Render via Mapbox (GeoJSON strategy for filled polygons)
        image_path = os.path.join(self.output_dir, f"{run_id}_ring_map.png")

        with MapboxClient(
            access_token=self.mapbox_token,
            style=self.style,
        ) as client:
            map_result: MapGenerationResult = client.generate_static_map(
                geojson_features=features,
                marker_overlay=marker_overlay,
                width=self.width,
                height=self.height,
                padding=self.padding,
                retina=self.retina,
                output_path=image_path,
                strategy="geojson",
            )

        # 9. Build ring_stats dicts and metadata
        ring_stats_dicts = [asdict(rs) for rs in ring_stats]

        metadata = {
            "run_id": run_id,
            "generated_at": datetime.now().isoformat(),
            "center_lon": center_lon,
            "center_lat": center_lat,
            "boundaries_mi": boundaries,
            "total_neighbors_mapped": len(neighbor_distances),
            "total_neighbors": len(self.neighbor_profiles),
            "strategy_used": map_result.strategy_used if map_result else "none",
            "url_length": map_result.url_length if map_result else 0,
            "settings": {
                "width": self.width,
                "height": self.height,
                "style": self.style,
                "retina": self.retina,
            },
        }

        if map_result and map_result.error_message:
            metadata["error"] = map_result.error_message

        # Save metadata + ring stats to JSON
        meta_path = os.path.join(self.output_dir, f"{run_id}_ring_metadata.json")
        with open(meta_path, "w") as f:
            json.dump({"ring_stats": ring_stats_dicts, "metadata": metadata}, f, indent=2)

        success = map_result.success if map_result else False
        if success:
            logger.info(f"Sentiment ring map generated: {image_path}")
        else:
            logger.error(
                f"Sentiment ring map failed: "
                f"{map_result.error_message if map_result else 'unknown'}"
            )

        return SentimentRingResult(
            success=success,
            image_path=map_result.image_path if map_result else None,
            ring_stats=ring_stats_dicts,
            metadata=metadata,
        )
