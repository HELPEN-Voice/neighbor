# src/ii_agent/tools/neighbor/__init__.py
from .orchestrator.neighbor_orchestrator import NeighborOrchestrator
from .engines.base import ResearchEvent


class NeighborAgent:
    """
    Public interface (mirrors DiligenceAgent style).
    """

    def __init__(self):
        self._orch = NeighborOrchestrator()

    async def screen(
        self,
        *,
        location: str | None = None,  # "lat,lon" for coordinates
        pin: str | None = None,  # PIN for parcel-based search
        county_path: str | None = None,  # County path for PIN search
        radius_mi: float | None = None,
        neighbors: list[str] | None = None,  # bypass parcel API
        county: str | None = None,
        state: str | None = None,
        city: str | None = None,
        entity_type_map: dict[str, str] | None = None,
        on_event=None,  # optional streaming/tracing callback
        save_regrid_json: bool = True,  # Save Regrid results to JSON
    ):
        return await self._orch.screen(
            location=location,
            pin=pin,
            county_path=county_path,
            radius_mi=radius_mi or 0.5,
            neighbors=neighbors,
            county=county,
            state=state,
            city=city,
            entity_type_map=entity_type_map,
            on_event=on_event,
            save_regrid_json=save_regrid_json,
        )
