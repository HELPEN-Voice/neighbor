"""Mapbox Static Images API client."""

import json
import urllib.parse
import logging
from typing import List, Dict, Any, Literal, Optional
from dataclasses import dataclass
from pathlib import Path

import httpx

from .geometry_utils import (
    simplify_geometry,
    reduce_coordinate_precision,
    geometry_to_polyline,
    estimate_geojson_url_length,
)

logger = logging.getLogger(__name__)


@dataclass
class MapGenerationResult:
    """Result of map generation attempt."""

    success: bool
    image_path: Optional[str]
    image_url: Optional[str]
    strategy_used: Literal["geojson", "polyline", "none"]
    error_message: Optional[str]
    parcels_rendered: int
    url_length: int


class MapboxClient:
    """Client for Mapbox Static Images API."""

    BASE_URL = "https://api.mapbox.com/styles/v1"
    MAX_URL_LENGTH = 8192  # Mapbox CDN limit
    SAFE_URL_LENGTH = 6000  # Conservative threshold for GeoJSON
    POLYLINE_URL_THRESHOLD = 7500  # Threshold for polyline strategy

    def __init__(
        self,
        access_token: str,
        style: str = "satellite-streets-v12",
        username: str = "mapbox",
        timeout: float = 60.0,
    ):
        """
        Initialize Mapbox client.

        Args:
            access_token: Mapbox public access token
            style: Mapbox style ID
            username: Mapbox username (default "mapbox" for standard styles)
            timeout: HTTP request timeout in seconds
        """
        self.access_token = access_token
        self.style = style
        self.username = username
        self.http_client = httpx.Client(timeout=timeout)

    def generate_static_map(
        self,
        geojson_features: List[Dict[str, Any]],
        marker_overlay: str = "",
        width: int = 800,
        height: int = 450,
        padding: int = 50,
        retina: bool = True,
        output_path: Optional[str] = None,
    ) -> MapGenerationResult:
        """
        Generate static map image with automatic strategy selection.

        Args:
            geojson_features: GeoJSON features with SimpleStyle properties
            marker_overlay: Optional marker overlay string for labels
            width: Image width in pixels
            height: Image height in pixels
            padding: Padding around features in pixels
            retina: Whether to generate @2x retina image
            output_path: Where to save the image (optional)

        Returns:
            MapGenerationResult with success status and details
        """
        if not geojson_features:
            return MapGenerationResult(
                success=False,
                image_path=None,
                image_url=None,
                strategy_used="none",
                error_message="No features to render",
                parcels_rendered=0,
                url_length=0,
            )

        # Strategy A: Try GeoJSON overlay first
        url = self._build_geojson_url(
            geojson_features, marker_overlay, width, height, padding, retina
        )
        logger.debug(f"GeoJSON URL length: {len(url)}")

        if len(url) <= self.SAFE_URL_LENGTH:
            return self._fetch_and_save(
                url, output_path, "geojson", len(geojson_features)
            )

        # Try simplification
        logger.info("URL too long, trying geometry simplification...")
        simplified_features = self._simplify_features(geojson_features)
        url = self._build_geojson_url(
            simplified_features, marker_overlay, width, height, padding, retina
        )
        logger.debug(f"Simplified URL length: {len(url)}")

        if len(url) <= self.SAFE_URL_LENGTH:
            return self._fetch_and_save(
                url, output_path, "geojson", len(geojson_features)
            )

        # Strategy B: Fall back to polyline encoding (no fill, outline only)
        logger.info("Simplification insufficient, trying polyline strategy...")
        url = self._build_polyline_url(
            geojson_features, marker_overlay, width, height, padding, retina
        )
        logger.debug(f"Polyline URL length: {len(url)}")

        if len(url) <= self.MAX_URL_LENGTH:
            return self._fetch_and_save(
                url, output_path, "polyline", len(geojson_features)
            )

        # All strategies failed
        return MapGenerationResult(
            success=False,
            image_path=None,
            image_url=None,
            strategy_used="none",
            error_message=f"URL too long even with polyline ({len(url)} chars). "
            f"Consider reducing number of parcels.",
            parcels_rendered=0,
            url_length=len(url),
        )

    def _build_geojson_url(
        self,
        features: List[Dict[str, Any]],
        marker_overlay: str,
        width: int,
        height: int,
        padding: int,
        retina: bool,
    ) -> str:
        """Build URL using GeoJSON overlay."""
        feature_collection = {"type": "FeatureCollection", "features": features}

        # Use compact JSON encoding
        geojson_str = json.dumps(feature_collection, separators=(",", ":"))
        encoded_geojson = urllib.parse.quote(geojson_str)

        retina_suffix = "@2x" if retina else ""

        # Combine overlays: geojson first (polygons), then markers on top
        overlay = f"geojson({encoded_geojson})"
        if marker_overlay:
            overlay = f"{overlay},{marker_overlay}"

        return (
            f"{self.BASE_URL}/{self.username}/{self.style}/static/"
            f"{overlay}/auto/{width}x{height}{retina_suffix}"
            f"?padding={padding}&access_token={self.access_token}"
        )

    def _build_polyline_url(
        self,
        features: List[Dict[str, Any]],
        marker_overlay: str,
        width: int,
        height: int,
        padding: int,
        retina: bool,
    ) -> str:
        """
        Build URL using polyline path overlay.

        Note: Polyline overlay doesn't support fill, only stroke.
        """
        paths = []

        for feat in features:
            geom = feat.get("geometry")
            props = feat.get("properties", {})

            if not geom:
                continue

            # Extract style from properties
            stroke = props.get("stroke", "#FF0000").replace("#", "")
            stroke_opacity = props.get("stroke-opacity", 1.0)
            stroke_width = props.get("stroke-width", 2)

            # For polyline, we can only do outline (no fill)
            # Format: path-{strokeWidth}+{strokeColor}-{strokeOpacity}({polyline})
            try:
                encoded_path = geometry_to_polyline(geom)
                safe_path = urllib.parse.quote(encoded_path)

                # Mapbox path format
                path_param = f"path-{stroke_width}+{stroke}-{stroke_opacity}({safe_path})"
                paths.append(path_param)
            except Exception as e:
                logger.warning(f"Failed to encode geometry as polyline: {e}")
                continue

        if not paths:
            # Fall back to markers only if no paths could be encoded
            overlay = marker_overlay if marker_overlay else ""
        else:
            overlay = ",".join(paths)
            if marker_overlay:
                overlay = f"{overlay},{marker_overlay}"

        retina_suffix = "@2x" if retina else ""

        return (
            f"{self.BASE_URL}/{self.username}/{self.style}/static/"
            f"{overlay}/auto/{width}x{height}{retina_suffix}"
            f"?padding={padding}&access_token={self.access_token}"
        )

    def _simplify_features(
        self,
        features: List[Dict[str, Any]],
        tolerance: float = 0.0001,
        precision: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Simplify all feature geometries to reduce URL size.

        Args:
            features: GeoJSON features
            tolerance: Douglas-Peucker simplification tolerance
            precision: Coordinate decimal places

        Returns:
            Features with simplified geometries
        """
        simplified = []

        for feat in features:
            new_feat = {
                "type": feat.get("type", "Feature"),
                "properties": feat.get("properties", {}),
            }

            geom = feat.get("geometry")
            if geom:
                # Apply simplification
                simplified_geom = simplify_geometry(geom, tolerance)
                # Reduce coordinate precision
                simplified_geom = reduce_coordinate_precision(simplified_geom, precision)
                new_feat["geometry"] = simplified_geom
            else:
                new_feat["geometry"] = geom

            simplified.append(new_feat)

        return simplified

    def _fetch_and_save(
        self,
        url: str,
        output_path: Optional[str],
        strategy: str,
        parcel_count: int,
    ) -> MapGenerationResult:
        """
        Fetch image from URL and optionally save to disk.

        Args:
            url: Mapbox Static Images API URL
            output_path: Where to save the image (optional)
            strategy: Strategy used ("geojson" or "polyline")
            parcel_count: Number of parcels rendered

        Returns:
            MapGenerationResult
        """
        try:
            logger.info(f"Fetching map using {strategy} strategy...")
            response = self.http_client.get(url)
            response.raise_for_status()

            # Check content type
            content_type = response.headers.get("content-type", "")
            if "image" not in content_type:
                return MapGenerationResult(
                    success=False,
                    image_path=None,
                    image_url=url,
                    strategy_used=strategy,
                    error_message=f"Unexpected content type: {content_type}",
                    parcels_rendered=0,
                    url_length=len(url),
                )

            # Save to file if path provided
            if output_path:
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(response.content)
                logger.info(f"Map saved to: {output_path}")

            return MapGenerationResult(
                success=True,
                image_path=output_path,
                image_url=url,
                strategy_used=strategy,
                error_message=None,
                parcels_rendered=parcel_count,
                url_length=len(url),
            )

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}"
            try:
                error_body = e.response.text[:500]
                error_msg = f"{error_msg}: {error_body}"
            except Exception:
                pass

            logger.error(f"Mapbox API error: {error_msg}")

            return MapGenerationResult(
                success=False,
                image_path=None,
                image_url=url,
                strategy_used=strategy,
                error_message=error_msg,
                parcels_rendered=0,
                url_length=len(url),
            )

        except httpx.TimeoutException:
            logger.error("Mapbox API timeout")
            return MapGenerationResult(
                success=False,
                image_path=None,
                image_url=url,
                strategy_used=strategy,
                error_message="Request timed out",
                parcels_rendered=0,
                url_length=len(url),
            )

        except Exception as e:
            logger.error(f"Mapbox API error: {e}")
            return MapGenerationResult(
                success=False,
                image_path=None,
                image_url=url,
                strategy_used=strategy,
                error_message=str(e),
                parcels_rendered=0,
                url_length=len(url),
            )

    def close(self):
        """Close HTTP client."""
        self.http_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
