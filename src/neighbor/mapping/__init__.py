"""Neighbor map visualization module.

Generates static map images showing aggregate community sentiment
via concentric ring zones — no individual parcels are rendered.
"""

from .map_generator import NeighborMapGenerator, NeighborMapResult
from .map_data_builder import MapDataBuilder, MapFeature
from .mapbox_client import MapboxClient, MapGenerationResult
from .labeling import LabelGenerator, ParcelLabel
from .styles import STYLES, get_style_for_neighbor, ParcelStyle
from .sentiment_ring_generator import (
    SentimentRingGenerator,
    SentimentRingResult,
    RingStat,
)
from .geometry_utils import (
    simplify_geometry,
    reduce_coordinate_precision,
    geometry_to_polyline,
    get_centroid,
    get_bounding_box,
    haversine_distance,
    create_circle_polygon,
)

__all__ = [
    "NeighborMapGenerator",
    "NeighborMapResult",
    "MapDataBuilder",
    "MapFeature",
    "MapboxClient",
    "MapGenerationResult",
    "LabelGenerator",
    "ParcelLabel",
    "STYLES",
    "get_style_for_neighbor",
    "ParcelStyle",
    "SentimentRingGenerator",
    "SentimentRingResult",
    "RingStat",
    "simplify_geometry",
    "reduce_coordinate_precision",
    "geometry_to_polyline",
    "get_centroid",
    "get_bounding_box",
    "haversine_distance",
    "create_circle_polygon",
]
