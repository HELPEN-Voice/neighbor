# src/neighbor/services/local_valuation.py
"""
Local Cluster Valuation Service

Calculates community wealth and land value proxies from Regrid parcel data.
Uses median values (robust to outliers) from a small sample (n<=50).

See SPEC-002_Local_Cluster_Valuation.md for full specification.
"""

import statistics
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from ..config.assessment_ratios import normalize_to_market_value


@dataclass
class WealthProxy:
    """Community wealth proxy based on structure/improvement values."""
    valid_samples: int
    median_structure_value: Optional[float]
    formatted: str
    risk_level: str  # HIGH, MEDIUM, LOW
    risk_class: str  # CSS class: risk-high, risk-medium, risk-low


@dataclass
class LandValueProxy:
    """Land value proxy based on per-acre land values."""
    valid_samples: int
    median_value_per_acre: Optional[float]
    formatted: str
    risk_level: str
    risk_class: str


@dataclass
class LocalClusterBenchmark:
    """Complete benchmark output for a location."""
    run_id: str
    coordinates: str
    state_code: str
    parcels_analyzed: int
    community_wealth_proxy: WealthProxy
    land_value_proxy: LandValueProxy
    # New aggregate metrics
    total_property_value: Optional[float]
    total_land_value: Optional[float]
    final_radius_miles: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "run_id": self.run_id,
            "coordinates": self.coordinates,
            "state_code": self.state_code,
            "parcels_analyzed": self.parcels_analyzed,
            "community_wealth_proxy": asdict(self.community_wealth_proxy),
            "land_value_proxy": asdict(self.land_value_proxy),
            "total_property_value": self.total_property_value,
            "total_land_value": self.total_land_value,
            "final_radius_miles": self.final_radius_miles,
            "template_variables": self.get_template_variables(),
        }

    def get_template_variables(self) -> Dict[str, str]:
        """Get variables ready for HTML template insertion."""
        return {
            "median_structure_value": self.community_wealth_proxy.formatted,
            "wealth_risk_level": self.community_wealth_proxy.risk_level,
            "wealth_risk_class": self.community_wealth_proxy.risk_class,
            "wealth_valid_samples": str(self.community_wealth_proxy.valid_samples),
            "median_land_value_acre": self.land_value_proxy.formatted,
            "land_risk_level": self.land_value_proxy.risk_level,
            "land_risk_class": self.land_value_proxy.risk_class,
            "land_valid_samples": str(self.land_value_proxy.valid_samples),
            "parcels_analyzed": str(self.parcels_analyzed),
            "total_property_value": f"${self.total_property_value:,.0f}" if self.total_property_value else "N/A",
            "total_land_value": f"${self.total_land_value:,.0f}" if self.total_land_value else "N/A",
            "final_radius_miles": f"{self.final_radius_miles:.2f}" if self.final_radius_miles else "N/A",
        }


class LocalValuationService:
    """
    Calculates local cluster valuation benchmarks from Regrid parcel data.

    Designed for small sample sizes (n<=50) using median values and
    aggressive filtering to separate residential from agricultural signals.
    """

    # Wealth risk thresholds (median structure value)
    WEALTH_HIGH_THRESHOLD = 500000  # > $500k = HIGH risk (wealthy neighbors, litigation budget)
    WEALTH_MEDIUM_THRESHOLD = 250000  # > $250k = MEDIUM risk

    # Land value risk thresholds (median $/acre)
    LAND_HIGH_THRESHOLD = 15000  # > $15k/acre = HIGH (prime farmland or development pressure)
    LAND_MEDIUM_THRESHOLD = 5000  # > $5k/acre = MEDIUM

    def __init__(self, state_code: str):
        """
        Initialize the service for a specific state.

        Args:
            state_code: Two-letter state abbreviation for assessment ratio lookups
        """
        self.state_code = state_code.upper()

    def calculate_benchmark(
        self,
        parcels: List[Dict[str, Any]],
        run_id: str,
        coordinates: str,
        final_radius_miles: Optional[float] = None,
    ) -> LocalClusterBenchmark:
        """
        Calculate the full local cluster benchmark from parcel data.

        Args:
            parcels: List of raw parcel dictionaries from Regrid API
            run_id: Unique identifier for this pipeline run
            coordinates: "lat,lon" string for the target location
            final_radius_miles: The final search radius used to collect parcels

        Returns:
            LocalClusterBenchmark with wealth and land proxies
        """
        wealth_proxy = self._calculate_wealth_proxy(parcels)
        land_proxy = self._calculate_land_proxy(parcels)

        # Calculate aggregate totals
        total_property_value, total_land_value = self._calculate_aggregates(parcels)

        return LocalClusterBenchmark(
            run_id=run_id,
            coordinates=coordinates,
            state_code=self.state_code,
            parcels_analyzed=len(parcels),
            community_wealth_proxy=wealth_proxy,
            land_value_proxy=land_proxy,
            total_property_value=total_property_value,
            total_land_value=total_land_value,
            final_radius_miles=final_radius_miles,
        )

    def _calculate_wealth_proxy(self, parcels: List[Dict[str, Any]]) -> WealthProxy:
        """
        Calculate community wealth proxy from structure/improvement values.

        Filters:
        - Excludes parcels with improvval < $10,000 (vacant land, sheds)
        - Excludes parcels with acres > 50 (large farms skew residential average)

        Uses: Median of normalized improvement values
        """
        valid_values = []

        for parcel in parcels:
            fields = parcel.get("properties", {}).get("fields", {})

            # Get improvement value and acreage
            improvval = self._safe_float(fields.get("improvval"))
            acres = self._safe_float(fields.get("ll_gisacre"))
            parvaltype = fields.get("parvaltype", "")

            # Filter: must have meaningful improvements, not large farm
            if improvval is None or improvval < 10000:
                continue
            if acres is not None and acres > 50:
                continue

            # Normalize to market value
            market_value = normalize_to_market_value(
                improvval, self.state_code, parvaltype
            )

            if market_value > 0:
                valid_values.append(market_value)

        # Calculate median
        if valid_values:
            median_value = statistics.median(valid_values)
            risk_level, risk_class = self._classify_wealth_risk(median_value)
            formatted = f"${median_value:,.0f}"
        else:
            median_value = None
            risk_level = "UNKNOWN"
            risk_class = "risk-unknown"
            formatted = "N/A"

        return WealthProxy(
            valid_samples=len(valid_values),
            median_structure_value=median_value,
            formatted=formatted,
            risk_level=risk_level,
            risk_class=risk_class,
        )

    def _calculate_land_proxy(self, parcels: List[Dict[str, Any]]) -> LandValueProxy:
        """
        Calculate land value proxy ($/acre) for agricultural assessment.

        Filters:
        - Excludes parcels with acres < 2.0 (residential yards)
        - Excludes parcels with landval null/zero

        Uses: Median of normalized (land value / acres)
        """
        valid_values = []

        for parcel in parcels:
            fields = parcel.get("properties", {}).get("fields", {})

            # Get land value and acreage
            landval = self._safe_float(fields.get("landval"))
            acres = self._safe_float(fields.get("ll_gisacre"))
            parvaltype = fields.get("parvaltype", "")

            # Filter: must have land value and be > 2 acres
            if landval is None or landval <= 0:
                continue
            if acres is None or acres < 2.0:
                continue

            # Normalize to market value
            market_landval = normalize_to_market_value(
                landval, self.state_code, parvaltype
            )

            # Calculate per-acre value
            value_per_acre = market_landval / acres

            if value_per_acre > 0:
                valid_values.append(value_per_acre)

        # Calculate median
        if valid_values:
            median_value = statistics.median(valid_values)
            risk_level, risk_class = self._classify_land_risk(median_value)
            formatted = f"${median_value:,.0f}/acre"
        else:
            median_value = None
            risk_level = "UNKNOWN"
            risk_class = "risk-unknown"
            formatted = "N/A"

        return LandValueProxy(
            valid_samples=len(valid_values),
            median_value_per_acre=median_value,
            formatted=formatted,
            risk_level=risk_level,
            risk_class=risk_class,
        )

    def _classify_wealth_risk(self, median_value: float) -> tuple:
        """Classify wealth risk level based on median structure value."""
        if median_value >= self.WEALTH_HIGH_THRESHOLD:
            return ("HIGH", "risk-high")
        elif median_value >= self.WEALTH_MEDIUM_THRESHOLD:
            return ("MEDIUM", "risk-medium")
        else:
            return ("LOW", "risk-low")

    def _classify_land_risk(self, median_value: float) -> tuple:
        """Classify land value risk based on median $/acre."""
        if median_value >= self.LAND_HIGH_THRESHOLD:
            return ("HIGH", "risk-high")
        elif median_value >= self.LAND_MEDIUM_THRESHOLD:
            return ("MEDIUM", "risk-medium")
        else:
            return ("LOW", "risk-low")

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        """Safely convert a value to float, returning None if invalid."""
        if value is None:
            return None
        try:
            result = float(value)
            return result if result >= 0 else None
        except (ValueError, TypeError):
            return None

    def _calculate_aggregates(
        self, parcels: List[Dict[str, Any]]
    ) -> tuple[Optional[float], Optional[float]]:
        """
        Calculate aggregate totals for property and land values.

        Returns:
            Tuple of (total_property_value, total_land_value) normalized to market value
        """
        total_property = 0.0
        total_land = 0.0
        property_count = 0
        land_count = 0

        for parcel in parcels:
            fields = parcel.get("properties", {}).get("fields", {})
            parvaltype = fields.get("parvaltype", "")

            # Sum improvement values (property/structure value)
            improvval = self._safe_float(fields.get("improvval"))
            if improvval is not None and improvval > 0:
                market_value = normalize_to_market_value(
                    improvval, self.state_code, parvaltype
                )
                total_property += market_value
                property_count += 1

            # Sum land values
            landval = self._safe_float(fields.get("landval"))
            if landval is not None and landval > 0:
                market_landval = normalize_to_market_value(
                    landval, self.state_code, parvaltype
                )
                total_land += market_landval
                land_count += 1

        return (
            total_property if property_count > 0 else None,
            total_land if land_count > 0 else None,
        )
