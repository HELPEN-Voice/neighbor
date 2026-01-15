"""Main map generation orchestrator for neighbor visualization."""

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from pathlib import Path

from ..models.schemas import NeighborProfile
from .map_data_builder import MapDataBuilder, MapFeature
from .mapbox_client import MapboxClient, MapGenerationResult
from .labeling import LabelGenerator, ParcelLabel, LegendEntry

logger = logging.getLogger(__name__)


@dataclass
class NeighborMapResult:
    """Complete result of neighbor map generation."""

    success: bool
    image_path: Optional[str]
    thumbnail_path: Optional[str]
    legend_html: str
    metadata: Dict[str, Any]
    labels: List[Dict[str, Any]]
    generation_result: Optional[MapGenerationResult]


class NeighborMapGenerator:
    """Generate map visualizations for neighbor screening results."""

    def __init__(
        self,
        target_parcel: Dict[str, Any],
        raw_parcels: List[Dict[str, Any]],
        neighbor_profiles: List[NeighborProfile],
        mapbox_token: str,
        output_dir: Optional[str] = None,
        style: str = "satellite-streets-v12",
        width: int = 800,
        height: int = 450,
        padding: int = 50,
        retina: bool = True,
    ):
        """
        Initialize the map generator.

        Args:
            target_parcel: Target parcel info with geometry
            raw_parcels: Raw parcel features from Regrid
            neighbor_profiles: Enriched neighbor profiles
            mapbox_token: Mapbox public access token
            output_dir: Directory for output files
            style: Mapbox style ID
            width: Image width in pixels
            height: Image height in pixels
            padding: Padding around features
            retina: Whether to generate @2x images
        """
        self.target_parcel = target_parcel
        self.raw_parcels = raw_parcels
        self.neighbor_profiles = neighbor_profiles
        self.mapbox_token = mapbox_token
        self.output_dir = output_dir or self._get_default_output_dir()
        self.style = style
        self.width = width
        self.height = height
        self.padding = padding
        self.retina = retina

        # Ensure output directory exists
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

    def _get_default_output_dir(self) -> str:
        """Get default output directory."""
        base = Path(__file__).parent.parent / "neighbor_map_outputs"
        return str(base)

    def generate(self, run_id: Optional[str] = None) -> NeighborMapResult:
        """
        Generate map image for neighbor screening results.

        Args:
            run_id: Unique identifier for this run (used in filenames)

        Returns:
            NeighborMapResult with paths and metadata
        """
        run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        logger.info(f"Starting map generation for run: {run_id}")

        # Step 1: Build map data
        logger.info("Building map features...")
        builder = MapDataBuilder(
            target_parcel=self.target_parcel,
            raw_parcels=self.raw_parcels,
            neighbor_profiles=self.neighbor_profiles,
        )

        features, stats = builder.build_map_features()
        logger.info(
            f"Built {len(features)} features "
            f"({stats['highlighted']} highlighted, "
            f"{stats['skipped_no_geometry']} skipped due to missing geometry)"
        )

        if not features:
            logger.warning("No features to render on map")
            return NeighborMapResult(
                success=False,
                image_path=None,
                thumbnail_path=None,
                legend_html="",
                metadata={"error": "No features to render", "stats": stats},
                labels=[],
                generation_result=None,
            )

        # Step 2: Convert to GeoJSON features
        geojson_features = builder.to_geojson_features(features)

        # Step 3: Generate labels and legend
        logger.info("Generating labels...")
        label_generator = LabelGenerator()
        labels, legend = label_generator.generate_labels_for_features(
            geojson_features, builder.pin_to_neighbor
        )
        logger.info(f"Generated {len(labels)} labels")

        # Build marker overlay string
        marker_overlay = label_generator.build_marker_overlay(labels)

        # Generate legend HTML
        legend_html = label_generator.format_legend_html(legend)

        # Step 4: Generate map image
        logger.info("Generating map image...")
        full_path = os.path.join(self.output_dir, f"{run_id}_map_full.png")

        with MapboxClient(
            access_token=self.mapbox_token,
            style=self.style,
        ) as client:
            result = client.generate_static_map(
                geojson_features=geojson_features,
                marker_overlay=marker_overlay,
                width=self.width,
                height=self.height,
                padding=self.padding,
                retina=self.retina,
                output_path=full_path,
            )

            # Generate thumbnail if main succeeded
            thumb_path = None
            if result.success:
                logger.info("Generating thumbnail...")
                thumb_path = os.path.join(self.output_dir, f"{run_id}_map_thumb.png")
                client.generate_static_map(
                    geojson_features=geojson_features,
                    marker_overlay=marker_overlay,
                    width=400,
                    height=300,
                    padding=30,
                    retina=False,
                    output_path=thumb_path,
                )

        # Step 5: Build metadata
        metadata = {
            "run_id": run_id,
            "generated_at": datetime.now().isoformat(),
            "stats": stats,
            "strategy_used": result.strategy_used,
            "url_length": result.url_length,
            "parcels_rendered": result.parcels_rendered,
            "labels_count": len(labels),
            "settings": {
                "width": self.width,
                "height": self.height,
                "style": self.style,
                "retina": self.retina,
                "padding": self.padding,
            },
        }

        if result.error_message:
            metadata["error"] = result.error_message

        # Save metadata to JSON
        metadata_path = os.path.join(self.output_dir, f"{run_id}_map_metadata.json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

        # Save legend data to JSON for HTML template use
        # Field names must match diligence template expectations: marker_char, text, full_name, etc.
        legend_data_path = os.path.join(self.output_dir, f"{run_id}_map_legend.json")
        legend_data = [
            {
                "marker_char": entry.marker_char,
                "text": entry.label_text,
                "full_name": entry.full_name,
                "color": entry.color,
                "influence": entry.influence,
                "stance": entry.stance,
                "is_adjacent": entry.is_adjacent,
                "is_target": False,  # Legend entries are neighbors, not the target parcel
            }
            for entry in legend
        ]
        with open(legend_data_path, "w") as f:
            json.dump(legend_data, f, indent=2)

        # Convert labels to dicts for return
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

        if result.success:
            logger.info(f"Map generated successfully: {full_path}")
        else:
            logger.error(f"Map generation failed: {result.error_message}")

        return NeighborMapResult(
            success=result.success,
            image_path=result.image_path,
            thumbnail_path=thumb_path,
            legend_html=legend_html,
            metadata=metadata,
            labels=labels_data,
            generation_result=result,
        )


def generate_neighbor_map(
    target_parcel: Dict[str, Any],
    raw_parcels: List[Dict[str, Any]],
    neighbor_profiles: List[NeighborProfile],
    mapbox_token: str,
    output_dir: Optional[str] = None,
    run_id: Optional[str] = None,
    **kwargs,
) -> NeighborMapResult:
    """
    Convenience function to generate a neighbor map.

    Args:
        target_parcel: Target parcel info with geometry
        raw_parcels: Raw parcel features from Regrid
        neighbor_profiles: Enriched neighbor profiles
        mapbox_token: Mapbox public access token
        output_dir: Directory for output files
        run_id: Unique identifier for this run
        **kwargs: Additional arguments passed to NeighborMapGenerator

    Returns:
        NeighborMapResult
    """
    generator = NeighborMapGenerator(
        target_parcel=target_parcel,
        raw_parcels=raw_parcels,
        neighbor_profiles=neighbor_profiles,
        mapbox_token=mapbox_token,
        output_dir=output_dir,
        **kwargs,
    )
    return generator.generate(run_id=run_id)
