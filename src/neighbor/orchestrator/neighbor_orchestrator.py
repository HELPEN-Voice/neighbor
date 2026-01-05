# src/ii_agent/tools/neighbor/orchestrator/neighbor_orchestrator.py
import asyncio, time
import json
import re
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
from ..services.local_valuation import LocalValuationService

# Engines
from ..engines.base import ResearchEngine, ResearchEvent
from ..engines.responses_engine import DeepResearchResponsesEngine
from ..engines.agents_sdk_engine import AgentsSDKEngine


# =============================================================================
# Smart Caching / Resume Helper Functions
# =============================================================================

def get_vr_files_for_run(dr_files: List[str]) -> List[str]:
    """Convert dr_* paths to vr_* paths."""
    return [f.replace("/dr_", "/vr_") for f in dr_files]


def delete_html_outputs(base_dir: Path = None):
    """Delete all HTML files in neighbor_html_outputs/"""
    if base_dir is None:
        base_dir = Path(__file__).parent.parent
    html_dir = base_dir / "neighbor_html_outputs"
    if html_dir.exists():
        for f in html_dir.glob("*.html"):
            f.unlink()
        print(f"   🧹 Deleted HTML outputs")


def delete_pdf_outputs(base_dir: Path = None):
    """Delete individual and combined PDFs."""
    if base_dir is None:
        base_dir = Path(__file__).parent.parent
    for pdf_dir in [base_dir / "individual_pdf_reports", base_dir / "combined_pdf_reports"]:
        if pdf_dir.exists():
            for f in pdf_dir.glob("*.pdf"):
                f.unlink()
    print(f"   🧹 Deleted PDF outputs")


def load_verified_profiles(vr_files: List[str]) -> List[Dict]:
    """Load and combine all verified profiles from vr_*.json files."""
    all_profiles = []
    for filepath in vr_files:
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
            all_profiles.extend(data.get("neighbors", []))
        except Exception as e:
            print(f"   ⚠️ Failed to load {filepath}: {e}")
    return all_profiles


def load_unverified_profiles(dr_files: List[str]) -> List[Dict]:
    """Load and combine all unverified profiles from dr_*.json files."""
    all_profiles = []
    for filepath in dr_files:
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
            all_profiles.extend(data.get("neighbors", []))
        except Exception as e:
            print(f"   ⚠️ Failed to load {filepath}: {e}")
    return all_profiles


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

        # Check cache - smart caching / resume behavior
        output_dir = Path(__file__).parent.parent / "neighbor_outputs"
        dr_output_dir = Path(__file__).parent.parent / "deep_research_outputs"
        cache_file = output_dir / "neighbor_final_merged.json"

        # Cache detection: check what files exist for these coordinates
        cached_dr_files = []
        cached_vr_files = []
        cache_coords_match = False
        resolved = None  # Will be loaded from cache if resuming

        if cache_file.exists() and location:
            try:
                with open(cache_file, "r") as f:
                    cached = json.load(f)
                cached_context = cached.get("location_context", "")

                # Extract coordinates from cached context
                if cached_context:
                    coord_match = re.search(r"([-\d.]+),\s*([-\d.]+)", cached_context)
                    if coord_match:
                        cached_lat = float(coord_match.group(1))
                        cached_lon = float(coord_match.group(2))
                        req_lat, req_lon = [float(x.strip()) for x in location.split(",")]

                        # Check if coordinates match
                        if abs(cached_lat - req_lat) < 1e-6 and abs(cached_lon - req_lon) < 1e-6:
                            cache_coords_match = True

                            # Get dr_* files from cached data
                            cached_dr_files = cached.get("deep_research_files", [])
                            if cached_dr_files:
                                # Verify dr_* files exist
                                cached_dr_files = [f for f in cached_dr_files if Path(f).exists()]

                            # Check for corresponding vr_* files
                            if cached_dr_files:
                                cached_vr_files = get_vr_files_for_run(cached_dr_files)
                                cached_vr_files = [f for f in cached_vr_files if Path(f).exists()]
            except Exception as e:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⚠️ Cache check failed: {e}, proceeding with fresh run")
                cache_coords_match = False

        # Determine run mode based on cached files
        has_dr = bool(cached_dr_files)
        has_vr = bool(cached_vr_files) and len(cached_vr_files) == len(cached_dr_files)

        # Scenario 3: Have both dr_* and vr_* files → skip research, just regenerate outputs
        # Delete HTML/PDFs so they get regenerated by the caller
        if cache_coords_match and has_dr and has_vr:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Found complete cached & verified data")
            print(f"   Using {len(cached_dr_files)} cached dr_*.json files")
            print(f"   Using {len(cached_vr_files)} cached vr_*.json files")
            print(f"   Skipping OpenAI + Verification stages")
            print(f"   Neighbors: {len(cached.get('neighbors', []))}")

            # Delete old outputs so caller regenerates them
            delete_html_outputs()
            delete_pdf_outputs()

            print(f"   Returning cached data for HTML/PDF regeneration")

            # Return the cached final result
            return cached

        # Scenario 2: Have dr_* but no vr_* files → skip OpenAI, run verification only
        # This scenario sets up variables and falls through to the dedupe/save logic
        skip_openai = False
        if cache_coords_match and has_dr and not has_vr and settings.ENABLE_VERIFICATION:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 📂 Found existing deep research files, resuming verification...")
            print(f"   Skipping OpenAI stage - using {len(cached_dr_files)} cached dr_*.json files")
            skip_openai = True

            # Import verification manager
            from ..agents.verification_manager_neighbor import NeighborVerificationManager

            verification_context = {"county": county, "state": state, "city": city}
            verification_manager = NeighborVerificationManager()

            verified_result = await verification_manager.verify_all(
                dr_filepaths=cached_dr_files,
                context=verification_context,
                concurrency_limit=settings.VERIFICATION_CONCURRENCY,
            )

            # Set up variables for the rest of the flow
            merged = verified_result["verified_profiles"]
            saved_filepaths = cached_dr_files + verified_result.get("vr_filepaths", [])
            flat_citations = []
            overview_summaries = []

            # Generate run_id for this resumed run
            run_id = str(uuid.uuid4())
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🆔 Generated run_id: {run_id}")

            # These may be needed later
            target_parcel_info = None
            adjacent_pins = set()

            # Parse lat/lon from location for later use
            if location:
                lat, lon = [float(x) for x in location.split(",")]
            else:
                lat, lon = None, None

            stats = verified_result["stats"]
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Verification complete:")
            print(f"   Files processed: {stats['files_processed']}")
            print(f"   Files succeeded: {stats['files_succeeded']}")
            print(f"   Total profiles verified: {stats['total_profiles_verified']}")

            if verified_result.get("errors"):
                for err in verified_result["errors"]:
                    print(f"   ⚠️ Error in {err['file']}: {err['error']}")

            # Load resolved data for adjacency mapping
            regrid_file = output_dir / "regrid_all.json"
            if regrid_file.exists():
                with open(regrid_file, "r") as f:
                    regrid_data = json.load(f)
                resolved = regrid_data.get("neighbors", [])
            else:
                resolved = []

        # Scenario 1: Fresh run - no cached data, need to run full pipeline
        if not skip_openai:
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
            resolved = []
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
            merged = []
            flat_citations = []
            overview_summaries = []
            saved_filepaths = []  # Track saved deep research files
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

            # 6.25) VERIFICATION STAGE (Gemini Deep Research)
            # Run verification on individual dr_*.json files before merge/dedupe
            if settings.ENABLE_VERIFICATION and saved_filepaths:
                print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔬 Starting Verification Stage...")
                print(f"   Processing {len(saved_filepaths)} deep research files...")

                from ..agents.verification_manager_neighbor import NeighborVerificationManager

                verification_context = {"county": county, "state": state, "city": city}
                verification_manager = NeighborVerificationManager()

                verified_result = await verification_manager.verify_all(
                    dr_filepaths=saved_filepaths,
                    context=verification_context,
                    concurrency_limit=settings.VERIFICATION_CONCURRENCY,
                )

                # Replace merged with verified profiles
                merged = verified_result["verified_profiles"]

                # Track verified file paths
                saved_filepaths.extend(verified_result.get("vr_filepaths", []))

                stats = verified_result["stats"]
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Verification complete:")
                print(f"   Files processed: {stats['files_processed']}")
                print(f"   Files succeeded: {stats['files_succeeded']}")
                print(f"   Total profiles verified: {stats['total_profiles_verified']}")

                if verified_result.get("errors"):
                    for err in verified_result["errors"]:
                        print(f"   ⚠️ Error in {err['file']}: {err['error']}")

        # 6.5) Deduplicate neighbors with similar names (Levenshtein distance <= 2)
        def dedupe_neighbors(neighbors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            """
            Deduplicate neighbors with similar names (Levenshtein distance <= 2).
            Rules:
            - Combine PINs from all duplicates
            - Stance priority: oppose > neutral > support > unknown
            - Keep stance, community_influence, approach_recommendations from winning entry
            - Use lowest confidence: low < medium < high
            """

            def levenshtein_distance(s1: str, s2: str) -> int:
                """Calculate Levenshtein distance between two strings."""
                if len(s1) < len(s2):
                    return levenshtein_distance(s2, s1)
                if len(s2) == 0:
                    return len(s1)

                previous_row = range(len(s2) + 1)
                for i, c1 in enumerate(s1):
                    current_row = [i + 1]
                    for j, c2 in enumerate(s2):
                        insertions = previous_row[j + 1] + 1
                        deletions = current_row[j] + 1
                        substitutions = previous_row[j] + (c1 != c2)
                        current_row.append(min(insertions, deletions, substitutions))
                    previous_row = current_row
                return previous_row[-1]

            STANCE_PRIORITY = {"oppose": 3, "neutral": 2, "support": 1, "unknown": 0}
            CONFIDENCE_PRIORITY = {"low": 1, "medium": 2, "high": 3}  # Lower = keep
            MAX_DISTANCE = 2

            # Split into residents and entities - only dedupe residents
            residents = []
            entities = []
            for n in neighbors:
                category = n.get("entity_category", "").lower()
                if category in ["resident", "individual", "person"]:
                    residents.append(n)
                else:
                    entities.append(n)

            # Group residents by similar names (Levenshtein distance <= 2)
            # Each group is a list of neighbors; we track a representative name for matching
            groups: List[List[Dict[str, Any]]] = []
            group_names: List[str] = []  # Representative name for each group

            for n in residents:
                name = n.get("name", "").strip().lower()
                if not name:
                    continue

                # Find if this name matches any existing group
                matched_group_idx = None
                for idx, rep_name in enumerate(group_names):
                    if levenshtein_distance(name, rep_name) <= MAX_DISTANCE:
                        matched_group_idx = idx
                        break

                if matched_group_idx is not None:
                    groups[matched_group_idx].append(n)
                else:
                    # Create new group
                    groups.append([n])
                    group_names.append(name)

            deduped = []
            for group in groups:
                if len(group) == 1:
                    deduped.append(group[0])
                    continue

                # Multiple entries with similar names - merge them
                names_in_group = [e.get("name", "") for e in group]
                print(
                    f"[DEDUP] Found {len(group)} similar entries: {names_in_group}, merging..."
                )

                # Combine all PINs
                all_pins = []
                for entry in group:
                    pins = entry.get("pins", [])
                    if isinstance(pins, list):
                        all_pins.extend(pins)
                    elif pins:
                        all_pins.append(pins)
                # Remove duplicates while preserving order
                seen_pins = set()
                unique_pins = []
                for pin in all_pins:
                    if pin not in seen_pins:
                        seen_pins.add(pin)
                        unique_pins.append(pin)

                # Find entry with highest stance priority (most hostile)
                def get_stance_priority(entry):
                    stance = entry.get("noted_stance", "unknown") or "unknown"
                    return STANCE_PRIORITY.get(stance.lower(), 0)

                winning_entry = max(group, key=get_stance_priority)

                # Find lowest confidence
                def get_confidence_priority(entry):
                    conf = entry.get("confidence", "medium") or "medium"
                    return CONFIDENCE_PRIORITY.get(conf.lower(), 2)

                lowest_conf_entry = min(group, key=get_confidence_priority)
                lowest_confidence = lowest_conf_entry.get("confidence", "medium")

                # Start with the winning entry (has best stance) and modify
                merged_entry = winning_entry.copy()
                merged_entry["pins"] = unique_pins
                merged_entry["confidence"] = lowest_confidence
                # Keep only the winning entry's claims (don't combine)

                print(
                    f"[DEDUP] Merged into '{merged_entry.get('name')}': {len(unique_pins)} PINs, stance={merged_entry.get('noted_stance')}, confidence={lowest_confidence}"
                )
                deduped.append(merged_entry)

            # Return deduped residents + untouched entities
            return deduped + entities

        merged = dedupe_neighbors(merged)

        # 7) Validate & finalize
        # Helper function to normalize names for matching
        def normalize_for_matching(name):
            """Normalize name to handle dots, commas, case, and name order variations."""
            if not name:
                return ""

            # Convert to lowercase for case-insensitive matching
            normalized = name.lower()

            # Remove all dots (handles "M." vs "M" and "Jr." vs "Jr")
            normalized = normalized.replace(".", "")

            # Remove commas and extra spaces
            normalized = normalized.replace(",", " ")

            # Collapse multiple spaces to single space and split into tokens
            tokens = normalized.split()

            # Sort tokens to handle name order differences
            # "worley austin p" and "austin p worley" both become "austin p worley"
            tokens.sort()

            return " ".join(tokens)

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
            json.dump(final, f, indent=2, ensure_ascii=False, default=str)

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

        # Calculate and save local cluster valuation benchmark
        try:
            if self.finder.raw_parcels and state:
                print(f"\n📊 Calculating local cluster valuation benchmark...")
                valuation_service = LocalValuationService(state_code=state)
                benchmark = valuation_service.calculate_benchmark(
                    parcels=self.finder.raw_parcels,
                    run_id=run_id,
                    coordinates=location or f"{lat},{lon}" if lat and lon else "",
                    final_radius_miles=self.finder.final_radius_miles,
                )
                benchmark_dict = benchmark.to_dict()

                # Save benchmark to JSON file
                benchmark_file = output_dir / "local_cluster_benchmark.json"
                with open(benchmark_file, "w") as f:
                    json.dump(benchmark_dict, f, indent=2)
                print(f"💾 Saved local cluster benchmark to: {benchmark_file.name}")

                # Save to database
                db = NeighborDBConnector()
                if db.conn:
                    db.save_local_cluster_benchmark(run_id=run_id, benchmark_data=benchmark_dict)
                    db.close()

                # Log summary
                wealth = benchmark.community_wealth_proxy
                land = benchmark.land_value_proxy
                print(f"   Community Wealth Proxy: {wealth.formatted} ({wealth.risk_level}) - {wealth.valid_samples} samples")
                print(f"   Land Value Proxy: {land.formatted} ({land.risk_level}) - {land.valid_samples} samples")
                print(f"   Total Property Value: ${benchmark.total_property_value:,.0f}" if benchmark.total_property_value else "   Total Property Value: N/A")
                print(f"   Total Land Value: ${benchmark.total_land_value:,.0f}" if benchmark.total_land_value else "   Total Land Value: N/A")
                print(f"   Final Search Radius: {benchmark.final_radius_miles:.2f} mi" if benchmark.final_radius_miles else "   Final Search Radius: N/A")
            else:
                print("⚠️ Skipping valuation benchmark - no raw parcels or state available")
        except Exception as e:
            print(f"⚠️ Failed to calculate valuation benchmark: {e}")
            # Don't fail the entire operation if valuation fails

        return final
