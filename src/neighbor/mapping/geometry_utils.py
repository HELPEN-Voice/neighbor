"""Geometry processing utilities for map generation."""

import json
import math
from typing import List, Tuple, Dict, Any

try:
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union

    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

try:
    import polyline as pl

    POLYLINE_AVAILABLE = True
except ImportError:
    POLYLINE_AVAILABLE = False


_EARTH_RADIUS_MI = 3958.8  # Mean Earth radius in miles


def haversine_distance(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """
    Great-circle distance between two points in miles.

    Args:
        lon1, lat1: First point (degrees)
        lon2, lat2: Second point (degrees)

    Returns:
        Distance in miles
    """
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def create_circle_polygon(
    center_lon: float,
    center_lat: float,
    radius_miles: float,
    num_points: int = 32,
) -> List[List[float]]:
    """
    Create a circle polygon as a closed coordinate ring using haversine projection.

    Handles latitude-dependent longitude scaling so circles don't distort at
    higher latitudes.

    Args:
        center_lon: Center longitude (degrees)
        center_lat: Center latitude (degrees)
        radius_miles: Circle radius in miles
        num_points: Number of vertices (excluding closure point)

    Returns:
        List of [lon, lat] pairs forming a closed ring (first == last)
    """
    coords = []
    lat_r = math.radians(center_lat)
    # Angular radius in radians on the sphere
    angular_radius = radius_miles / _EARTH_RADIUS_MI

    for i in range(num_points):
        angle = 2 * math.pi * i / num_points
        # Offset in radians of latitude / longitude
        dlat = angular_radius * math.cos(angle)
        # Scale longitude offset by cos(latitude)
        dlon = angular_radius * math.sin(angle) / max(math.cos(lat_r), 1e-10)

        coords.append([
            round(center_lon + math.degrees(dlon), 6),
            round(center_lat + math.degrees(dlat), 6),
        ])

    # Close the ring
    coords.append(coords[0])
    return coords


def simplify_geometry(
    geojson: Dict[str, Any], tolerance: float = 0.0001
) -> Dict[str, Any]:
    """
    Simplify geometry using Douglas-Peucker algorithm.

    Args:
        geojson: GeoJSON geometry object (Polygon or MultiPolygon)
        tolerance: Simplification tolerance (~0.0001 = ~10m at equator)

    Returns:
        Simplified GeoJSON geometry
    """
    if not SHAPELY_AVAILABLE:
        return geojson

    try:
        geom = shape(geojson)
        simplified = geom.simplify(tolerance, preserve_topology=True)
        return mapping(simplified)
    except Exception:
        return geojson


def reduce_coordinate_precision(
    geojson: Dict[str, Any], precision: int = 5
) -> Dict[str, Any]:
    """
    Reduce coordinate precision to save URL space.

    Args:
        geojson: GeoJSON geometry object
        precision: Decimal places to keep (5 = ~1m accuracy)

    Returns:
        GeoJSON with reduced precision coordinates
    """

    def round_coords(coords):
        if isinstance(coords[0], (list, tuple)):
            return [round_coords(c) for c in coords]
        return [round(coords[0], precision), round(coords[1], precision)]

    result = geojson.copy()
    result["coordinates"] = round_coords(geojson["coordinates"])
    return result


def geometry_to_polyline(geojson: Dict[str, Any]) -> str:
    """
    Convert GeoJSON polygon to Google polyline encoding.

    Note: GeoJSON uses (lon, lat), polyline uses (lat, lon).

    Args:
        geojson: GeoJSON Polygon geometry

    Returns:
        Encoded polyline string

    Raises:
        ImportError: If polyline library not available
        ValueError: If geometry type not supported
    """
    if not POLYLINE_AVAILABLE:
        raise ImportError("polyline library required: pip install polyline")

    geom_type = geojson.get("type", "")

    if geom_type == "Polygon":
        coords = geojson["coordinates"][0]  # Outer ring
    elif geom_type == "MultiPolygon":
        # Use first polygon's outer ring
        coords = geojson["coordinates"][0][0]
    else:
        raise ValueError(f"Unsupported geometry type: {geom_type}")

    # Convert lon,lat to lat,lon for polyline encoding
    lat_lon_coords = [(coord[1], coord[0]) for coord in coords]

    return pl.encode(lat_lon_coords, 5)


def get_centroid(geometry: Dict[str, Any]) -> Tuple[float, float]:
    """
    Get centroid coordinates for label placement.

    Args:
        geometry: GeoJSON geometry object

    Returns:
        (lon, lat) tuple
    """
    if SHAPELY_AVAILABLE:
        try:
            geom = shape(geometry)
            centroid = geom.centroid
            return (centroid.x, centroid.y)
        except Exception:
            pass

    # Fallback: calculate simple centroid from coordinates
    return _simple_centroid(geometry)


def _simple_centroid(geometry: Dict[str, Any]) -> Tuple[float, float]:
    """
    Calculate simple centroid without Shapely.

    Args:
        geometry: GeoJSON geometry object

    Returns:
        (lon, lat) tuple
    """
    geom_type = geometry.get("type", "")

    if geom_type == "Polygon":
        coords = geometry["coordinates"][0]  # Outer ring
    elif geom_type == "MultiPolygon":
        coords = geometry["coordinates"][0][0]  # First polygon outer ring
    elif geom_type == "Point":
        return tuple(geometry["coordinates"][:2])
    else:
        # Default to first coordinate
        coords = geometry.get("coordinates", [[0, 0]])
        if isinstance(coords[0], list) and isinstance(coords[0][0], list):
            coords = coords[0]
        if isinstance(coords[0], list):
            coords = coords

    # Calculate centroid as average of all coordinates
    if not coords:
        return (0.0, 0.0)

    sum_lon = sum(c[0] for c in coords)
    sum_lat = sum(c[1] for c in coords)
    n = len(coords)

    return (sum_lon / n, sum_lat / n)


def get_bounding_box(
    geometries: List[Dict[str, Any]],
) -> Tuple[float, float, float, float]:
    """
    Calculate bounding box for list of geometries.

    Args:
        geometries: List of GeoJSON geometry objects

    Returns:
        (min_lon, min_lat, max_lon, max_lat)
    """
    if SHAPELY_AVAILABLE and geometries:
        try:
            shapes = [shape(g) for g in geometries]
            combined = unary_union(shapes)
            return combined.bounds
        except Exception:
            pass

    # Fallback: calculate from coordinates
    return _simple_bounding_box(geometries)


def _simple_bounding_box(
    geometries: List[Dict[str, Any]],
) -> Tuple[float, float, float, float]:
    """
    Calculate bounding box without Shapely.

    Returns:
        (min_lon, min_lat, max_lon, max_lat)
    """
    min_lon = float("inf")
    min_lat = float("inf")
    max_lon = float("-inf")
    max_lat = float("-inf")

    def update_bounds(coords):
        nonlocal min_lon, min_lat, max_lon, max_lat
        for coord in coords:
            if isinstance(coord[0], (list, tuple)):
                update_bounds(coord)
            else:
                lon, lat = coord[0], coord[1]
                min_lon = min(min_lon, lon)
                min_lat = min(min_lat, lat)
                max_lon = max(max_lon, lon)
                max_lat = max(max_lat, lat)

    for geom in geometries:
        coords = geom.get("coordinates", [])
        update_bounds(coords)

    if min_lon == float("inf"):
        return (0, 0, 0, 0)

    return (min_lon, min_lat, max_lon, max_lat)


def estimate_geojson_url_length(features: List[Dict[str, Any]]) -> int:
    """
    Estimate URL length for GeoJSON overlay.

    Args:
        features: List of GeoJSON features

    Returns:
        Estimated URL length in characters
    """
    fc = {"type": "FeatureCollection", "features": features}
    # URL encoding roughly doubles size, plus base URL ~200 chars
    return len(json.dumps(fc, separators=(",", ":"))) * 2 + 200


def validate_geometry(geometry: Dict[str, Any]) -> bool:
    """
    Validate that a geometry object is usable.

    Args:
        geometry: GeoJSON geometry object

    Returns:
        True if geometry is valid
    """
    if not geometry:
        return False

    geom_type = geometry.get("type")
    coords = geometry.get("coordinates")

    if not geom_type or not coords:
        return False

    if geom_type not in ["Point", "Polygon", "MultiPolygon", "LineString"]:
        return False

    # Check coordinates are not empty
    if geom_type == "Point":
        return len(coords) >= 2
    elif geom_type == "Polygon":
        return len(coords) > 0 and len(coords[0]) >= 3
    elif geom_type == "MultiPolygon":
        return len(coords) > 0 and len(coords[0]) > 0 and len(coords[0][0]) >= 3

    return True
