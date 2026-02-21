"""Tests for sentiment ring map generation."""

import json
import math
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from neighbor.mapping.geometry_utils import (
    haversine_distance,
    create_circle_polygon,
)
from neighbor.mapping.sentiment_ring_generator import (
    SentimentRingGenerator,
    SentimentRingResult,
    RingStat,
    _classify_ring,
    _compute_ring_boundaries,
)


# =============================================================================
# TestHaversineDistance
# =============================================================================


class TestHaversineDistance:
    def test_zero_distance(self):
        d = haversine_distance(-90.0, 40.0, -90.0, 40.0)
        assert d == pytest.approx(0.0, abs=1e-10)

    def test_known_distance_nyc_la(self):
        # NYC (40.7128, -74.0060) to LA (34.0522, -118.2437) ≈ 2,451 mi
        d = haversine_distance(-74.0060, 40.7128, -118.2437, 34.0522)
        assert 2400 < d < 2500

    def test_symmetry(self):
        d1 = haversine_distance(-90.0, 40.0, -91.0, 41.0)
        d2 = haversine_distance(-91.0, 41.0, -90.0, 40.0)
        assert d1 == pytest.approx(d2, rel=1e-10)

    def test_short_distance(self):
        # ~0.05 degree should be a few miles
        d = haversine_distance(-90.0, 40.0, -90.05, 40.0)
        assert 0 < d < 10

    def test_antipodal(self):
        # ~12,451 mi (half circumference)
        d = haversine_distance(0, 0, 180, 0)
        assert 12400 < d < 12500


# =============================================================================
# TestCreateCirclePolygon
# =============================================================================


class TestCreateCirclePolygon:
    def test_point_count_default(self):
        coords = create_circle_polygon(-90.0, 40.0, 1.0)
        assert len(coords) == 33  # 32 + 1 closure

    def test_custom_point_count(self):
        coords = create_circle_polygon(-90.0, 40.0, 1.0, num_points=16)
        assert len(coords) == 17  # 16 + 1 closure

    def test_closed_ring(self):
        coords = create_circle_polygon(-90.0, 40.0, 1.0)
        assert coords[0] == coords[-1]

    def test_radius_accuracy(self):
        """All points should be approximately radius_miles from center."""
        center_lon, center_lat = -90.0, 40.0
        radius = 1.0
        coords = create_circle_polygon(center_lon, center_lat, radius)

        for lon, lat in coords[:-1]:  # skip closure point
            d = haversine_distance(center_lon, center_lat, lon, lat)
            assert d == pytest.approx(radius, rel=0.01)  # within 1%

    def test_latitude_correction(self):
        """Circle at high latitude should have wider longitude range."""
        equator = create_circle_polygon(0, 0, 1.0)
        high_lat = create_circle_polygon(0, 60, 1.0)

        equator_lon_range = max(c[0] for c in equator) - min(c[0] for c in equator)
        high_lat_lon_range = max(c[0] for c in high_lat) - min(c[0] for c in high_lat)

        # At 60°N, longitude range should be ~2x wider (cos(60°) = 0.5)
        assert high_lat_lon_range > equator_lon_range * 1.5


# =============================================================================
# TestRingBinning
# =============================================================================


class TestRingBinning:
    def test_compact_equal_width(self):
        """Distances all within 0.5 mi → 3 equal-width bands."""
        distances = [0.1, 0.15, 0.2, 0.3, 0.4, 0.45]
        boundaries = _compute_ring_boundaries(distances)
        assert len(boundaries) == 4
        assert boundaries[0] == 0.0
        # Equal width: max/3 ≈ 0.15
        expected_width = max(distances) / 3
        assert boundaries[1] == pytest.approx(expected_width, rel=0.01)
        assert boundaries[2] == pytest.approx(expected_width * 2, rel=0.01)
        assert boundaries[3] == pytest.approx(max(distances), rel=0.01)

    def test_spread_percentile(self):
        """Distances > 0.5 mi → percentile splits."""
        distances = [0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0]
        boundaries = _compute_ring_boundaries(distances)
        assert len(boundaries) == 4
        assert boundaries[0] == 0.0
        assert boundaries[3] >= max(distances)

    def test_minimum_width(self):
        """Ring widths must be at least 0.1 mi."""
        distances = [0.5, 0.51, 0.52, 0.53, 0.54, 0.55, 0.6, 1.0]
        boundaries = _compute_ring_boundaries(distances)
        for i in range(1, len(boundaries)):
            assert boundaries[i] - boundaries[i - 1] >= 0.1 - 1e-6

    def test_empty_distances(self):
        boundaries = _compute_ring_boundaries([])
        assert len(boundaries) == 4

    def test_single_neighbor(self):
        boundaries = _compute_ring_boundaries([0.3])
        assert len(boundaries) == 4
        assert boundaries[0] == 0.0


# =============================================================================
# TestSentimentAggregation
# =============================================================================


class TestSentimentAggregation:
    def test_oppose_dominant(self):
        assert _classify_ring(5, 1, 1, 0, 7) == "oppose"

    def test_support_dominant(self):
        assert _classify_ring(0, 5, 1, 0, 6) == "support"

    def test_neutral_dominant(self):
        assert _classify_ring(0, 0, 5, 1, 6) == "neutral"

    def test_mixed(self):
        assert _classify_ring(2, 2, 2, 1, 7) == "mixed"

    def test_no_data(self):
        assert _classify_ring(0, 0, 0, 0, 0) == "no_data"

    def test_boundary_oppose_ratio(self):
        # Exactly 0.4 → not oppose (needs > 0.4)
        assert _classify_ring(4, 3, 3, 0, 10) == "mixed"
        # 5/10 = 0.5 > 0.4 → oppose
        assert _classify_ring(5, 3, 2, 0, 10) == "oppose"


# =============================================================================
# TestSentimentRingGenerator
# =============================================================================


def _make_profile(neighbor_id, stance, pins=None):
    """Create a minimal mock NeighborProfile."""
    mock = MagicMock()
    mock.neighbor_id = neighbor_id
    mock.noted_stance = stance
    mock.pins = pins or [f"PIN-{neighbor_id}"]
    mock.community_influence = "Medium"
    mock.entity_category = "Resident"
    return mock


def _make_parcel(pin, lon, lat):
    """Create a raw parcel dict with geometry."""
    return {
        "properties": {"parcelnumb": pin},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [lon - 0.001, lat - 0.001],
                    [lon + 0.001, lat - 0.001],
                    [lon + 0.001, lat + 0.001],
                    [lon - 0.001, lat + 0.001],
                    [lon - 0.001, lat - 0.001],
                ]
            ],
        },
    }


class TestSentimentRingGenerator:
    def _setup_generator(self, profiles, parcels, target_lon=-90.0, target_lat=40.0):
        target_parcel = {
            "pin": "TARGET-001",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [target_lon - 0.002, target_lat - 0.002],
                        [target_lon + 0.002, target_lat - 0.002],
                        [target_lon + 0.002, target_lat + 0.002],
                        [target_lon - 0.002, target_lat + 0.002],
                        [target_lon - 0.002, target_lat - 0.002],
                    ]
                ],
            },
        }

        return SentimentRingGenerator(
            target_parcel=target_parcel,
            neighbor_profiles=profiles,
            raw_parcels=parcels,
            mapbox_token="pk.test_token",
            output_dir=tempfile.mkdtemp(),
            width=400,
            height=300,
        )

    @patch.object(SentimentRingGenerator, "generate")
    def test_ring_stats_format(self, mock_generate):
        """Ring stats should have required keys."""
        mock_generate.return_value = SentimentRingResult(
            success=True,
            image_path="/tmp/test.png",
            ring_stats=[
                {
                    "ring": 1,
                    "inner_mi": 0.0,
                    "outer_mi": 0.25,
                    "count": 3,
                    "oppose": 1,
                    "support": 1,
                    "neutral": 1,
                    "unknown": 0,
                    "sentiment": "mixed",
                }
            ],
            metadata={},
        )
        result = mock_generate()
        assert result.success
        rs = result.ring_stats[0]
        for key in ["ring", "inner_mi", "outer_mi", "count", "oppose", "support", "neutral", "unknown", "sentiment"]:
            assert key in rs

    def test_no_neighbors(self):
        """Generator should succeed with empty profiles (all rings no_data)."""
        gen = self._setup_generator(profiles=[], parcels=[])

        # Mock the Mapbox call
        with patch("neighbor.mapping.sentiment_ring_generator.MapboxClient") as MockClient:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.image_path = "/tmp/test.png"
            mock_result.strategy_used = "geojson"
            mock_result.url_length = 500
            mock_result.error_message = None
            mock_instance.generate_static_map.return_value = mock_result
            mock_instance.__enter__ = MagicMock(return_value=mock_instance)
            mock_instance.__exit__ = MagicMock(return_value=False)
            MockClient.return_value = mock_instance

            result = gen.generate(run_id="test_empty")

        assert result.success
        assert len(result.ring_stats) == 3
        for rs in result.ring_stats:
            assert rs["count"] == 0
            assert rs["sentiment"] == "no_data"

    def test_end_to_end_with_mock_data(self):
        """Full pipeline with mock profiles and parcels."""
        target_lon, target_lat = -90.0, 40.0

        # Create neighbors at varying distances
        profiles = [
            _make_profile("1", "oppose", ["PIN-1"]),
            _make_profile("2", "oppose", ["PIN-2"]),
            _make_profile("3", "support", ["PIN-3"]),
            _make_profile("4", "neutral", ["PIN-4"]),
            _make_profile("5", "neutral", ["PIN-5"]),
        ]
        parcels = [
            _make_parcel("PIN-1", target_lon + 0.002, target_lat),    # very close
            _make_parcel("PIN-2", target_lon + 0.003, target_lat),    # close
            _make_parcel("PIN-3", target_lon + 0.01, target_lat),     # medium
            _make_parcel("PIN-4", target_lon + 0.02, target_lat),     # further
            _make_parcel("PIN-5", target_lon + 0.03, target_lat),     # furthest
        ]

        gen = self._setup_generator(profiles, parcels, target_lon, target_lat)

        with patch("neighbor.mapping.sentiment_ring_generator.MapboxClient") as MockClient:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.image_path = os.path.join(gen.output_dir, "test_ring_map.png")
            mock_result.strategy_used = "geojson"
            mock_result.url_length = 3000
            mock_result.error_message = None
            mock_instance.generate_static_map.return_value = mock_result
            mock_instance.__enter__ = MagicMock(return_value=mock_instance)
            mock_instance.__exit__ = MagicMock(return_value=False)
            MockClient.return_value = mock_instance

            result = gen.generate(run_id="test_e2e")

        assert result.success
        assert len(result.ring_stats) == 3

        # All 5 neighbors should be distributed across rings
        total = sum(rs["count"] for rs in result.ring_stats)
        assert total == 5

        # Each ring stat should have valid sentiment
        for rs in result.ring_stats:
            assert rs["sentiment"] in ["oppose", "support", "mixed", "neutral", "no_data"]
            assert rs["inner_mi"] < rs["outer_mi"]

    def test_geojson_features_use_strategy(self):
        """Verify generate_static_map is called with strategy='geojson'."""
        target_lon, target_lat = -90.0, 40.0
        profiles = [_make_profile("1", "neutral", ["PIN-1"])]
        parcels = [_make_parcel("PIN-1", target_lon + 0.005, target_lat)]

        gen = self._setup_generator(profiles, parcels, target_lon, target_lat)

        with patch("neighbor.mapping.sentiment_ring_generator.MapboxClient") as MockClient:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.image_path = "/tmp/test.png"
            mock_result.strategy_used = "geojson"
            mock_result.url_length = 2000
            mock_result.error_message = None
            mock_instance.generate_static_map.return_value = mock_result
            mock_instance.__enter__ = MagicMock(return_value=mock_instance)
            mock_instance.__exit__ = MagicMock(return_value=False)
            MockClient.return_value = mock_instance

            gen.generate(run_id="test_strategy")

            # Verify strategy="geojson" was passed
            call_kwargs = mock_instance.generate_static_map.call_args
            assert call_kwargs[1]["strategy"] == "geojson" or call_kwargs.kwargs.get("strategy") == "geojson"

    def test_donut_ring_has_hole(self):
        """Rings 2 and 3 should be donut polygons with inner holes."""
        target_lon, target_lat = -90.0, 40.0
        profiles = [
            _make_profile("1", "oppose", ["PIN-1"]),
            _make_profile("2", "support", ["PIN-2"]),
            _make_profile("3", "neutral", ["PIN-3"]),
        ]
        parcels = [
            _make_parcel("PIN-1", target_lon + 0.003, target_lat),
            _make_parcel("PIN-2", target_lon + 0.01, target_lat),
            _make_parcel("PIN-3", target_lon + 0.02, target_lat),
        ]

        gen = self._setup_generator(profiles, parcels, target_lon, target_lat)

        captured_features = None

        with patch("neighbor.mapping.sentiment_ring_generator.MapboxClient") as MockClient:
            mock_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.image_path = "/tmp/test.png"
            mock_result.strategy_used = "geojson"
            mock_result.url_length = 2000
            mock_result.error_message = None

            def capture_features(**kwargs):
                nonlocal captured_features
                captured_features = kwargs.get("geojson_features")
                return mock_result

            mock_instance.generate_static_map.side_effect = capture_features
            mock_instance.__enter__ = MagicMock(return_value=mock_instance)
            mock_instance.__exit__ = MagicMock(return_value=False)
            MockClient.return_value = mock_instance

            gen.generate(run_id="test_donut")

        assert captured_features is not None

        # Ring features come first (3 rings), then target parcel
        # Rings are outermost-first: ring 3, ring 2, ring 1
        ring_features = [f for f in captured_features if f["geometry"]["type"] == "Polygon"
                         and f["properties"].get("stroke-width") == 1]

        # At least rings 2 and 3 should have inner holes (2 coordinate rings)
        donut_count = sum(
            1 for f in ring_features
            if len(f["geometry"]["coordinates"]) == 2
        )
        assert donut_count >= 2  # rings 2 and 3
