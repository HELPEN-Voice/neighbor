# src/ii_agent/tools/neighbor/orchestrator/neighbor_orchestrator.py
import asyncio, time
import json
import sys, subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal, Callable
from ..config.settings import settings
from ..models.schemas import NeighborResult, NeighborProfile
from ..agents.neighbor_finder import NeighborFinder
from ..utils.entity import guess_entity_type
from ..utils.db_connector import NeighborDBConnector

# Engines
from ..engines.base import ResearchEngine, ResearchEvent
from ..engines.responses_engine import DeepResearchResponsesEngine
from ..engines.agents_sdk_engine import AgentsSDKEngine


def _engine_factory() -> ResearchEngine:
    if settings.ENGINE_TYPE == "agentsdk":
        return AgentsSDKEngine()
    return DeepResearchResponsesEngine()


class NeighborOrchestrator:
    def __init__(self, engine: ResearchEngine | None = None):
        self.finder = NeighborFinder()
        self.engine = engine or _engine_factory()

    async def _chunk(self, items: List[str], n: int) -> List[List[str]]:
        return [items[i : i + n] for i in range(0, len(items), n)]

    def _save_regrid_to_json(
        self, resolved: List[Dict[str, Any]], output_dir: Path = None
    ) -> Dict[str, Path]:
        """Save Regrid results to JSON files, separated by entity type."""
        if output_dir is None:
            output_dir = Path(__file__).parent.parent / "neighbor_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Split by entity type
        people_data = [r for r in resolved if r.get("entity_type") == "person"]
        orgs_data = [r for r in resolved if r.get("entity_type") == "organization"]

        # Save to JSON files
        files_saved = {}

        if people_data:
            people_file = output_dir / "regrid_people.json"
            with open(people_file, "w") as f:
                json.dump(
                    {"entity_type": "person", "neighbors": people_data}, f, indent=2
                )
            files_saved["people"] = people_file
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Saved {len(people_data)} people to {people_file}"
            )

        if orgs_data:
            orgs_file = output_dir / "regrid_organizations.json"
            with open(orgs_file, "w") as f:
                json.dump(
                    {"entity_type": "organization", "neighbors": orgs_data}, f, indent=2
                )
            files_saved["organizations"] = orgs_file
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Saved {len(orgs_data)} organizations to {orgs_file}"
            )

        # Also save combined data for reference
        all_file = output_dir / "regrid_all.json"
        with open(all_file, "w") as f:
            json.dump({"total": len(resolved), "neighbors": resolved}, f, indent=2)
        files_saved["all"] = all_file

        return files_saved

    async def screen(
        self,
        *,
        location: str | None = None,  # "lat,lon" for coordinates
        pin: str | None = None,  # PIN for parcel-based search
        county_path: str | None = None,  # County path for PIN search
        radius_mi: float = settings.DEFAULT_RADIUS_MILES,
        neighbors: List[str] | None = None,  # skip parcel API
        county: str | None = None,
        state: str | None = None,
        city: str | None = None,
        entity_type_map: Dict[str, Literal["person", "organization"]] | None = None,
        on_event: Optional[Callable[[ResearchEvent], None]] = None,
        save_regrid_json: bool = True,  # Save Regrid results to JSON
    ) -> Dict[str, Any]:
        t0 = time.time()

        # Generate unique run_id for this neighbor screening
        run_id = str(uuid.uuid4())
        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🆔 Generated run_id: {run_id}"
        )

        # 1) Determine target parcel and get adjacent parcels
        target_parcel_info = None
        adjacent_pins = set()

        if pin:  # PIN-based search
            target_parcel_info = await self.finder.get_target_parcel(
                search_mode="PIN", pin=pin, county_path=county_path
            )
            if not target_parcel_info:
                return NeighborResult(
                    neighbors=[],
                    location_context="Target parcel not found",
                    success=False,
                ).dict()

            # Get adjacent parcels for the target
            adjacent_pins = await self.finder.get_adjacent_parcels(
                target_parcel_info["geometry"], target_parcel_info["pin"]
            )

            # Use target parcel's coordinates for neighbor search
            lat = target_parcel_info["lat"]
            lon = target_parcel_info["lon"]

        elif location:  # Coordinate-based search
            lat, lon = [float(x) for x in location.split(",")]
            # Get target parcel info from coordinates
            target_parcel_info = await self.finder.get_target_parcel(
                search_mode="COORDS", lat=lat, lon=lon
            )
            if target_parcel_info:
                # Get adjacent parcels for the target
                adjacent_pins = await self.finder.get_adjacent_parcels(
                    target_parcel_info["geometry"], target_parcel_info["pin"]
                )

        # 2) Resolve neighbors
        resolved: List[Dict[str, Any]] = []
        if neighbors:
            # Manual neighbor list provided
            for name in neighbors:
                etype = (entity_type_map or {}).get(name) or guess_entity_type(name)
                resolved.append(
                    {
                        "name": name,
                        "entity_type": etype,
                        "pins": [],
                        "owns_adjacent_parcel": "No",  # Default to No for manual entries
                    }
                )
        elif lat and lon:
            # Use the new expansion method to find neighbors
            resolved = await self.finder.find_by_location_with_expansion(
                lat=lat,
                lon=lon,
                initial_radius_mi=radius_mi,
                target_count=settings.MAX_NEIGHBORS,
                adjacent_pins=adjacent_pins,
            )
        else:
            return NeighborResult(
                neighbors=[],
                location_context="No location or neighbors provided",
                success=False,
            ).dict()

        # Cap to limit
        resolved = resolved[: settings.MAX_NEIGHBORS]
        if not resolved:
            return NeighborResult(
                neighbors=[], location_context="No neighbors", success=True
            ).dict()

        # Save Regrid results to JSON files if requested
        if save_regrid_json:
            saved_files = self._save_regrid_to_json(resolved)
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Regrid data saved to: {saved_files}"
            )

        # 3) Split by entity type - include name, PINs, and owns_adjacent_parcel
        people = [
            {
                "name": r["name"],
                "pins": r.get("pins", []),
                "owns_adjacent_parcel": r.get("owns_adjacent_parcel", "No"),
            }
            for r in resolved
            if r.get("entity_type") == "person"
        ]
        orgs = [
            {
                "name": r["name"],
                "pins": r.get("pins", []),
                "owns_adjacent_parcel": r.get("owns_adjacent_parcel", "No"),
            }
            for r in resolved
            if r.get("entity_type") == "organization"
        ]

        # 4) Batch
        batches: List[tuple[list[str], str]] = []
        for group, etype in ((people, "person"), (orgs, "organization")):
            for chunk in await self._chunk(group, settings.BATCH_SIZE):
                batches.append((chunk, etype))

        context = {
            "county": county,
            "state": state,
            "city": city,
            "radius_mi": radius_mi,
        }
        sem = asyncio.Semaphore(settings.CONCURRENCY_LIMIT)

        async def run(names: List[str], etype: str):
            async with sem:
                return await self.engine.run_batch(
                    names, context, etype, on_event=on_event
                )

        # 5) Fire concurrently
        tasks = [run(names, etype) for names, etype in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 6) Merge results
        merged: List[Dict[str, Any]] = []
        flat_citations: List[Dict[str, Any]] = []
        overview_summaries: List[str] = []
        saved_filepaths: List[str] = []  # Track saved deep research files
        for r in results:
            if isinstance(r, Exception):
                if on_event:
                    on_event(
                        {
                            "type": "error",
                            "batch_size": 0,
                            "entity_type": "person",
                            "message": str(r),
                            "meta": {},
                        }
                    )
                continue
            merged.extend(r.get("neighbors", []))
            flat_citations.extend(r.get("annotations", []))
            if r.get("overview_summary"):
                overview_summaries.append(r["overview_summary"])
            if r.get("saved_filepath"):
                saved_filepaths.append(r["saved_filepath"])

        # 7) Validate & finalize
        # Helper function to normalize names for matching
        def normalize_for_matching(name):
            """Normalize name to handle dots, commas, and case variations."""
            if not name:
                return ""

            # Convert to lowercase for case-insensitive matching
            normalized = name.lower()

            # Remove all dots (handles "M." vs "M" and "Jr." vs "Jr")
            normalized = normalized.replace(".", "")

            # Remove commas and extra spaces
            normalized = normalized.replace(",", " ")

            # Collapse multiple spaces to single space
            normalized = " ".join(normalized.split())

            return normalized

        # Create a mapping of normalized names to adjacency info from original resolved data
        adjacency_map = {}
        for r in resolved:
            original_name = r["name"]
            normalized_name = normalize_for_matching(original_name)
            adjacency_map[normalized_name] = r.get("owns_adjacent_parcel", "No")

        # Add owns_adjacent_parcel field from resolved data to merged profiles
        for profile in merged:
            name = profile.get("name", "")
            normalized_profile_name = normalize_for_matching(name)

            # Look up using normalized name
            if normalized_profile_name in adjacency_map:
                profile["owns_adjacent_parcel"] = adjacency_map[normalized_profile_name]
            else:
                # Default to "No" if no match found
                profile["owns_adjacent_parcel"] = "No"

        # Original validation code continues below
        # Ensure entity_type set on each profile (model may omit)
        for p in merged:
            p.setdefault(
                "entity_type",
                "person"
                if p.get("approx_age_bracket", "unknown") != "unknown"
                else "unknown",
            )

        # Citation validation: Downgrade uncited assertions to "unknown"
        # Decision fields that require citations for non-unknown values
        DECISION_FIELDS = {
            "stance": "unknown",
            "signal": "unknown",
            "influence_level": "unknown",
            "risk_level": "unknown",
            "profile_summary": None,
            "engagement_recommendation": None,
            "residency_status": "unknown",
            "approx_age_bracket": "unknown",
            "org_classification": "unknown",
            "org_local_presence": "unknown",
            "confidence": "medium",  # Default to medium if uncited
        }

        # List fields that should be emptied if no citations
        DECISION_LIST_FIELDS = [
            "flags",
            "behavioral_indicators",
            "financial_stress_signals",
            "coalition_predictors",
            "tenure_signals",
            "household_public_signals",
        ]

        # Nested fields that need special handling
        for p in merged:
            # Check if this neighbor has any citations
            has_citations = bool(p.get("citations"))

            if not has_citations:
                # Downgrade string/enum fields to their safe defaults
                for field, default_value in DECISION_FIELDS.items():
                    if field in p and p[field] not in [
                        None,
                        default_value,
                        "unknown",
                        "",
                    ]:
                        p[field] = default_value

                # Empty list fields that make assertions
                for field in DECISION_LIST_FIELDS:
                    if field in p and p[field]:
                        p[field] = []

                # Handle nested influence field
                if p.get("influence"):
                    if isinstance(p["influence"], dict):
                        p["influence"]["selected"] = []
                        p["influence"]["formal_roles"] = []
                        p["influence"]["informal_roles"] = []
                        p["influence"]["economic_footprint"] = []
                        p["influence"]["affiliations"] = []
                        p["influence"]["network_notes"] = []

                # Note: Keep factual non-assertion fields like name, pins, entity_type

        # Combine overview summaries if multiple batches produced them
        combined_overview = " ".join(overview_summaries) if overview_summaries else None

        # Validate neighbors and skip invalid ones
        validated_neighbors = []
        for p in merged:
            try:
                validated_neighbors.append(NeighborProfile(**p))
            except Exception as e:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⚠️  Skipping invalid neighbor: {p.get('name', 'unknown')} - {str(e)}"
                )
                continue

        final = NeighborResult(
            neighbors=validated_neighbors,
            location_context=f"Neighbors within {radius_mi} mi of {location or (county + ', ' + state if county and state else 'unknown')}",
            overview_summary=combined_overview,
            success=True,
        ).dict()

        final["runtime_minutes"] = round((time.time() - t0) / 60.0, 2)
        final["citations_flat"] = flat_citations
        final["deep_research_files"] = saved_filepaths  # Add paths to saved files
        final["city"] = city  # Include city for HTML generation
        final["county"] = county  # Include county for HTML generation
        final["state"] = state  # Include state for HTML generation
        final["run_id"] = run_id  # Include unique run_id for this screening

        # Save the final merged result with adjacency data
        output_dir = Path(__file__).parent.parent / "neighbor_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        final_output_path = output_dir / "neighbor_final_merged.json"

        with open(final_output_path, "w", encoding="utf-8") as f:
            json.dump(final, f, indent=2, ensure_ascii=False)

        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 💾 Saved final merged output to: {final_output_path.name}"
        )

        # Save location information separately for HTML generation
        location_data = {}
        if pin:
            location_data["pin"] = pin
            if county_path:
                location_data["county_path"] = county_path
        elif location:
            location_data["coords"] = location

        location_file = output_dir / "location.json"
        with open(location_file, "w", encoding="utf-8") as f:
            json.dump(location_data, f, indent=2, ensure_ascii=False)

        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 💾 Saved location data to: {location_file.name}"
        )

        # Save neighbor stakeholders to database
        try:
            db = NeighborDBConnector()
            if db.conn:
                # Convert NeighborProfile objects to dicts for database insertion
                neighbors_for_db = [
                    n.dict() if hasattr(n, "dict") else n for n in validated_neighbors
                ]
                db.save_neighbor_stakeholders(
                    run_id=run_id,
                    neighbors=neighbors_for_db,
                    location_context=final.get("location_context"),
                    location=location,
                    pin=pin
                    or (target_parcel_info.get("pin") if target_parcel_info else None),
                    county=county,
                    state=state,
                    city=city,
                    county_path=county_path,
                    adjacent_pins=adjacent_pins,
                )
                db.close()
            else:
                print("⚠️ Database connection not available, skipping stakeholder save")
        except Exception as e:
            print(f"⚠️ Failed to save neighbors to database: {e}")
            # Don't fail the entire operation if database save fails

        return final
