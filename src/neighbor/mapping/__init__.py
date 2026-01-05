"""Neighbor map visualization module.

Generates static map images showing target parcels and highlighted
neighbor parcels based on community influence and development stance.
"""

from .map_generator import NeighborMapGenerator, NeighborMapResult
from .map_data_builder import MapDataBuilder, MapFeature
from .mapbox_client import MapboxClient, MapGenerationResult
from .labeling import LabelGenerator, ParcelLabel
from .styles import STYLES, get_style_for_neighbor, ParcelStyle
from .geometry_utils import (
    simplify_geometry,
    reduce_coordinate_precision,
    geometry_to_polyline,
    get_centroid,
    get_bounding_box,
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
    "simplify_geometry",
    "reduce_coordinate_precision",
    "geometry_to_polyline",
    "get_centroid",
    "get_bounding_box",
]
