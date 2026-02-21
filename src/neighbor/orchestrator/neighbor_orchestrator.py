# src/ii_agent/tools/neighbor/orchestrator/neighbor_orchestrator.py
import asyncio, time
import json
import os
import re
import sys, subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal, Callable
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables from .env file
load_dotenv()
from ..config.settings import settings
from ..models.schemas import NeighborResult, NeighborProfile
from ..agents.neighbor_finder import NeighborFinder
from ..utils.entity import guess_entity_type
from ..utils.db_connector import NeighborDBConnector
from ..services.local_valuation import LocalValuationService
from ..utils.aggregator import aggregate_neighbors
from ..agents.verification_manager_neighbor import NeighborVerificationManager
from ..mapping.map_generator import NeighborMapGenerator
from ..mapping.fullpage_map_generator import FullPageMapGenerator

# Engines
from ..engines.base import ResearchEngine, ResearchEvent
from ..engines.responses_engine import DeepResearchResponsesEngine
from ..engines.agents_sdk_engine import AgentsSDKEngine


# =============================================================================
# Overview Synthesis with Gemini 3 Flash
# =============================================================================

async def synthesize_overview(
    batch_overviews: List[str],
    neighbors: List[Dict[str, Any]],
    location_context: str,
) -> str:
    """
    Synthesize a coherent overview from multiple batch summaries using Gemini 3 Flash.

    The batch overviews from Deep Research often contradict each other because each
    batch only sees its own subset of neighbors. This function uses an LLM to create
    a unified summary that accurately reflects the actual neighbor data.

    Args:
        batch_overviews: List of overview strings from individual Deep Research batches
        neighbors: List of all merged neighbor profiles (to calculate actual counts)
        location_context: Location description for context

    Returns:
        A synthesized overview string that accurately reflects the full dataset
    """
    if not batch_overviews:
        return None

    # Calculate actual counts from merged neighbors
    high_influence = sum(1 for n in neighbors if (n.get("community_influence") or "").lower() == "high")
    medium_influence = sum(1 for n in neighbors if (n.get("community_influence") or "").lower() == "medium")
    low_influence = sum(1 for n in neighbors if (n.get("community_influence") or "").lower() in ["low", "unknown", ""])

    oppose_count = sum(1 for n in neighbors if (n.get("noted_stance") or "").lower() == "oppose")
    support_count = sum(1 for n in neighbors if (n.get("noted_stance") or "").lower() == "support")
    neutral_count = sum(1 for n in neighbors if (n.get("noted_stance") or "").lower() == "neutral")
    unknown_count = len(neighbors) - oppose_count - support_count - neutral_count

    residents = sum(1 for n in neighbors if (n.get("entity_category") or n.get("entity_type") or "").lower() in ["resident", "individual", "trust", "estate"])
    organizations = len(neighbors) - residents

    # Build the synthesis prompt
    prompt = f"""You are summarizing neighbor screening results for a land development project.

LOCATION: {location_context}

ACTUAL COUNTS (use these exact numbers - they are authoritative):
- Total neighbors profiled: {len(neighbors)}
- Residents/Individuals: {residents}
- Organizations/Entities: {organizations}
- High influence: {high_influence}
- Medium influence: {medium_influence}
- Low/Unknown influence: {low_influence}
- Oppose stance: {oppose_count}
- Support stance: {support_count}
- Neutral stance: {neutral_count}
- Unknown stance: {unknown_count}

BATCH SUMMARIES (from separate research batches - may contain inaccuracies about totals):
{chr(10).join(f"Batch {i+1}: {summary}" for i, summary in enumerate(batch_overviews))}

TASK:
Write a 2-4 sentence overview that:
1. Uses the ACTUAL COUNTS above (not the batch summaries' counts which may be wrong)
2. Synthesizes the qualitative insights from the batch summaries (types of neighbors, key concerns, notable entities)
3. Highlights any high-influence neighbors or opposition risks
4. Is factual and concise - no speculation
5. DO NOT mention any individual neighbor by name, parcel ID, or address - use only aggregate descriptions

Return ONLY the overview text, no preamble or explanation."""

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY or GOOGLE_API_KEY must be set for overview synthesis. "
            "Add it to your .env file."
        )

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,
        ),
    )

    synthesized = response.text.strip() if response.text else ""
    if not synthesized:
        raise RuntimeError("Gemini 3 Flash returned empty response for overview synthesis")

    print(f"   ✅ Synthesized overview with Gemini 3 Flash ({len(synthesized)} chars)")
    return synthesized


# =============================================================================
# Smart Caching / Resume Helper Functions
# =============================================================================

def get_vr_files_for_run(dr_files: List[str]) -> List[str]:
    """Convert dr_* paths to vr_* paths."""
    return [f.replace("/dr_", "/vr_") for f in dr_files]


def get_batch_cache_path(output_dir: Path, entity_type: str, batch_idx: int, total_batches: int) -> Path:
    """Get deterministic cache path for a batch result."""
    # Use plural form for consistency: persons, organizations
    etype_plural = "persons" if entity_type == "person" else "organizations"
    return output_dir / f"batch_{etype_plural}_{batch_idx + 1}-{total_batches}.json"


def load_cached_batch(cache_path: Path) -> Optional[Dict[str, Any]]:
    """Load a cached batch result if it exists and is valid."""
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
        # Validate it has the expected structure
        if "neighbors" in data and isinstance(data["neighbors"], list):
            return data
    except Exception as e:
        print(f"   ⚠️ Failed to load cached batch {cache_path.name}: {e}")
    return None


def save_batch_result(cache_path: Path, result: Dict[str, Any], batch_idx: int, total_batches: int, entity_type: str) -> None:
    """Save a batch result to cache."""
    try:
        cache_data = {
            "batch_idx": batch_idx,
            "total_batches": total_batches,
            "entity_type": entity_type,
            "neighbors": result.get("neighbors", []),
            "annotations": result.get("annotations", []),
            "overview_summary": result.get("overview_summary"),
            "saved_filepath": result.get("saved_filepath"),
            "cached_at": datetime.now().isoformat(),
        }
        with open(cache_path, "w") as f:
            json.dump(cache_data, f, indent=2)
        print(f"   💾 Cached batch result: {cache_path.name}")
    except Exception as e:
        print(f"   ⚠️ Failed to cache batch {cache_path.name}: {e}")


def delete_batch_caches(base_dir: Path = None):
    """Delete all batch cache files in neighbor_outputs/."""
    if base_dir is None:
        base_dir = Path(__file__).parent.parent
    output_dir = base_dir / "neighbor_outputs"
    if output_dir.exists():
        deleted = 0
        for f in output_dir.glob("batch_*.json"):
            try:
                subprocess.run(["trash", str(f)], check=False)
                deleted += 1
            except OSError:
                pass
        if deleted:
            print(f"   🧹 Trashed {deleted} batch cache files")


def delete_html_outputs(base_dir: Path = None):
    """Delete all HTML files in neighbor_html_outputs/"""
    if base_dir is None:
        base_dir = Path(__file__).parent.parent
    html_dir = base_dir / "neighbor_html_outputs"
    if html_dir.exists():
        for f in html_dir.glob("*.html"):
            subprocess.run(["trash", str(f)], check=False)
        print(f"   🧹 Trashed HTML outputs")


def delete_pdf_outputs(base_dir: Path = None):
    """Delete individual and combined PDFs."""
    if base_dir is None:
        base_dir = Path(__file__).parent.parent
    for pdf_dir in [base_dir / "individual_pdf_reports", base_dir / "combined_pdf_reports"]:
        if pdf_dir.exists():
            for f in pdf_dir.glob("*.pdf"):
                subprocess.run(["trash", str(f)], check=False)
    print(f"   🧹 Trashed PDF outputs")


def delete_map_outputs(base_dir: Path = None):
    """Delete all files in neighbor_map_outputs/"""
    if base_dir is None:
        base_dir = Path(__file__).parent.parent
    map_dir = base_dir / "neighbor_map_outputs"
    if map_dir.exists():
        for f in map_dir.glob("*"):
            if f.is_file():
                subprocess.run(["trash", str(f)], check=False)
        print(f"   🧹 Trashed map outputs")


def _read_last_location(output_dir: Path) -> Optional[Dict[str, Any]]:
    """Read the location from the last run for cache invalidation."""
    loc_file = output_dir / ".last_location"
    if loc_file.exists():
        try:
            with open(loc_file, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _write_last_location(output_dir: Path, lat: float = None, lon: float = None, pin: str = None):
    """Save current location for cache comparison on next run."""
    loc_file = output_dir / ".last_location"
    data = {}
    if lat is not None and lon is not None:
        data["lat"] = lat
        data["lon"] = lon
    if pin:
        data["pin"] = pin
    with open(loc_file, "w") as f:
        json.dump(data, f)


def _location_matches_last(output_dir: Path, lat: float = None, lon: float = None, pin: str = None) -> bool:
    """Check if current location matches the last run.

    Returns True (no cleanup needed) when there is no previous run data.
    Returns False if stale caches exist but no tracking file is present
    (handles migration from before this tracking was added).
    """
    last = _read_last_location(output_dir)
    if not last:
        # No tracking file. If existing caches are present they're from before
        # tracking was added — treat as location change to force cleanup.
        has_existing_caches = (
            (output_dir / "neighbor_final_merged.json").exists()
            or bool(list(output_dir.glob("batch_*.json")))
        )
        return not has_existing_caches

    if pin and last.get("pin"):
        return pin == last["pin"]

    if (
        lat is not None
        and lon is not None
        and last.get("lat") is not None
        and last.get("lon") is not None
    ):
        return abs(lat - last["lat"]) < 1e-6 and abs(lon - last["lon"]) < 1e-6

    # Different search types (coords vs PIN) — treat as location change
    return False


def clean_all_outputs(base_dir: Path = None):
    """Full cleanup for a fresh run at a new location."""
    if base_dir is None:
        base_dir = Path(__file__).parent.parent

    output_dir = base_dir / "neighbor_outputs"
    dr_output_dir = base_dir / "deep_research_outputs"

    delete_batch_caches(base_dir)
    delete_html_outputs(base_dir)
    delete_pdf_outputs(base_dir)
    delete_map_outputs(base_dir)

    # Trash cached JSON files
    if output_dir.exists():
        for pattern in [
            "neighbor_final_merged.json",
            "regrid_*.json",
            "raw_parcels.json",
            "location.json",
            "local_cluster_benchmark.json",
        ]:
            for f in output_dir.glob(pattern):
                subprocess.run(["trash", str(f)], check=False)

    # Trash deep research outputs
    if dr_output_dir.exists():
        for f in dr_output_dir.glob("*.json"):
            subprocess.run(["trash", str(f)], check=False)

    print(f"   🧹 Cleaned all outputs for fresh run at new location")


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


def generate_fullpage_map(
    target_parcel: Dict[str, Any],
    raw_parcels: List[Dict[str, Any]],
    neighbor_profiles: List,
    output_dir: Path,
    run_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Generate a full-page map with all neighbors.

    Returns dict with map paths/metadata on success, None on failure.
    """
    print(f"\n🗺️  Generating full-page neighbor map...")
    fullpage_generator = FullPageMapGenerator(
        target_parcel=target_parcel,
        raw_parcels=raw_parcels,
        neighbor_profiles=neighbor_profiles,
        mapbox_token=settings.MAPBOX_ACCESS_TOKEN,
        output_dir=str(output_dir.parent / "neighbor_map_outputs"),
        style=settings.MAPBOX_STYLE,
        width=1280,
        height=720,
        padding=80,
        retina=True,
    )

    fullpage_result = fullpage_generator.generate(run_id=run_id)

    if fullpage_result.success:
        print(f"✅ Full-page map generated: {fullpage_result.image_path}")
        return {
            "fullpage_map_image_path": fullpage_result.image_path,
            "fullpage_map_labels": fullpage_result.labels,
            "fullpage_map_metadata": fullpage_result.metadata,
        }
    else:
        print(f"⚠️ Full-page map generation failed")
        return None


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
        output_dir.mkdir(parents=True, exist_ok=True)

        # ── Location change detection ──
        # If coordinates changed since last run, clean all stale caches so
        # we don't accidentally reuse another location's research data.
        if location:
            _req_lat, _req_lon = [float(x.strip()) for x in location.split(",")]
            if not _location_matches_last(output_dir, lat=_req_lat, lon=_req_lon):
                last = _read_last_location(output_dir)
                if last:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔄 New location detected, cleaning stale caches...")
                    print(f"   Last: ({last.get('lat')}, {last.get('lon')}, pin={last.get('pin')})")
                    print(f"   Current: ({_req_lat}, {_req_lon})")
                else:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔄 Stale caches detected without location tracking, cleaning...")
                clean_all_outputs()
            _write_last_location(output_dir, lat=_req_lat, lon=_req_lon)
        elif pin:
            if not _location_matches_last(output_dir, pin=pin):
                last = _read_last_location(output_dir)
                if last:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔄 New location detected, cleaning stale caches...")
                    print(f"   Last PIN: {last.get('pin')}, coords: ({last.get('lat')}, {last.get('lon')})")
                    print(f"   Current PIN: {pin}")
                else:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔄 Stale caches detected without location tracking, cleaning...")
                clean_all_outputs()
            _write_last_location(output_dir, pin=pin)

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

                            # Get dr_* files from cached data (filter out any vr_* files from old caches)
                            cached_dr_files = cached.get("deep_research_files", [])
                            if cached_dr_files:
                                # Filter to only dr_* files that exist
                                cached_dr_files = [f for f in cached_dr_files
                                                   if Path(f).exists() and ("/dr_" in f or "\\dr_" in f)]

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
            # Load profiled neighbors from vr_* files
            neighbors_from_vr = []
            for vr_file in cached_vr_files:
                try:
                    with open(vr_file, "r") as f:
                        vr_data = json.load(f)
                        neighbors_from_vr.extend(vr_data.get("neighbors", []))
                except Exception as e:
                    print(f"   ⚠️ Failed to load {vr_file}: {e}")

            # Use cached data - no threshold check, trust the cached files
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Found cached & verified data")
            print(f"   Using {len(cached_dr_files)} cached dr_*.json files")
            print(f"   Using {len(cached_vr_files)} cached vr_*.json files")
            print(f"   Skipping OpenAI + Verification stages")
            cached["neighbors"] = neighbors_from_vr
            print(f"   Neighbors: {len(neighbors_from_vr)}")

            # Delete old outputs so caller regenerates them
            delete_html_outputs()
            delete_pdf_outputs()
            delete_map_outputs()

            # Calculate adjacent parcels for cached data
            adjacent_pins = set()
            target_parcel_info = cached.get("target_parcel_info")
            if location and target_parcel_info:
                lat, lon = [float(x) for x in location.split(",")]
                adjacent_pins = await self.finder.get_adjacent_parcels(
                    target_parcel_info["geometry"], target_parcel_info.get("pin", "")
                )
                # Update neighbors with adjacency status
                for neighbor in cached.get("neighbors", []):
                    neighbor_pins = neighbor.get("pins", [])
                    if any(p in adjacent_pins for p in neighbor_pins):
                        neighbor["owns_adjacent_parcel"] = "Yes"
                    else:
                        neighbor["owns_adjacent_parcel"] = "No"

            # Load raw parcels from cache for valuation benchmark
            raw_parcels_file = output_dir / "raw_parcels.json"
            if raw_parcels_file.exists():
                try:
                    with open(raw_parcels_file, "r") as f:
                        self.finder.raw_parcels = json.load(f)
                except Exception:
                    pass

            # Generate map if we have the required data
            target_parcel_info = cached.get("target_parcel_info")

            if (
                settings.GENERATE_MAP
                and settings.MAPBOX_ACCESS_TOKEN
                and target_parcel_info
                and raw_parcels_file.exists()
            ):
                print(f"\n🗺️  Generating neighbor map visualization...")
                try:
                    # Load raw parcels from cache (use finder's loaded data)
                    raw_parcels = self.finder.raw_parcels or []
                    if not raw_parcels and raw_parcels_file.exists():
                        with open(raw_parcels_file, "r") as f:
                            raw_parcels = json.load(f)

                    # Convert cached neighbors to NeighborProfile objects
                    validated_neighbors = []
                    for p in cached.get("neighbors", []):
                        try:
                            validated_neighbors.append(NeighborProfile(**p))
                        except Exception:
                            pass

                    map_generator = NeighborMapGenerator(
                        target_parcel=target_parcel_info,
                        raw_parcels=raw_parcels,
                        neighbor_profiles=validated_neighbors,
                        mapbox_token=settings.MAPBOX_ACCESS_TOKEN,
                        output_dir=str(output_dir.parent / "neighbor_map_outputs"),
                        style=settings.MAPBOX_STYLE,
                        width=settings.MAP_WIDTH,
                        height=settings.MAP_HEIGHT,
                        padding=settings.MAP_PADDING,
                        retina=settings.MAP_RETINA,
                    )

                    run_id = cached.get("run_id", datetime.now().strftime("%Y%m%d_%H%M%S"))
                    map_result = map_generator.generate(run_id=run_id)

                    if map_result.success:
                        print(f"✅ Map generated: {map_result.image_path}")
                        print(
                            f"   Strategy: {map_result.generation_result.strategy_used}, "
                            f"Parcels: {map_result.generation_result.parcels_rendered}"
                        )
                        cached["map_image_path"] = map_result.image_path
                        cached["map_thumbnail_path"] = map_result.thumbnail_path
                        cached["map_legend_html"] = map_result.legend_html
                        cached["map_labels"] = map_result.labels
                        cached["map_metadata"] = map_result.metadata
                    else:
                        print(
                            f"⚠️ Map generation failed: {map_result.generation_result.error_message if map_result.generation_result else 'Unknown error'}"
                        )

                    # Generate full-page map (includes ALL neighbors regardless of influence)
                    fullpage_data = generate_fullpage_map(
                        target_parcel=target_parcel_info,
                        raw_parcels=raw_parcels,
                        neighbor_profiles=validated_neighbors,
                        output_dir=output_dir,
                        run_id=run_id,
                    )
                    if fullpage_data:
                        cached.update(fullpage_data)

                except Exception as e:
                    print(f"⚠️ Failed to generate map: {e}")
            elif not raw_parcels_file.exists():
                print("⚠️ Skipping map generation - raw_parcels.json not found (run without --no-clean first)")
            elif not settings.MAPBOX_ACCESS_TOKEN:
                print("⚠️ Skipping map generation - MAPBOX_ACCESS_TOKEN not set")

            # =====================================================================
            # AGGREGATION BOUNDARY — same as fresh path (line 1447)
            # Convert PII-bearing profiles to aggregate stats
            # =====================================================================
            merged_dicts = [n for n in cached.get("neighbors", [])]
            final = await aggregate_neighbors(
                profiles=merged_dicts,
                location_context=cached.get("location_context", ""),
                overview_summary=cached.get("overview_summary"),
                city=city,
                county=county,
                state=state,
                run_id=cached.get("run_id"),
                runtime_minutes=cached.get("runtime_minutes"),
                map_image_path=cached.get("map_image_path"),
                map_thumbnail_path=cached.get("map_thumbnail_path"),
                map_metadata=cached.get("map_metadata"),
            )

            # Save the PII-free aggregate result
            final_output_path = output_dir / "neighbor_final_merged.json"
            with open(final_output_path, "w", encoding="utf-8") as f:
                json.dump(final, f, indent=2, ensure_ascii=False, default=str)
            print(f"   Saved aggregate output to {final_output_path.name}")

            return final

        # Scenario 2: Have dr_* but no vr_* files → check if all batches complete
        # If batches are incomplete, fall through to batch processing to run missing ones
        skip_openai = False
        all_batches_complete = False

        if cache_coords_match and has_dr and not has_vr:
            # Check if all batches are cached (batch_*.json files)
            # Load regrid data to determine expected batch count
            regrid_file = output_dir / "regrid_all.json"
            if regrid_file.exists():
                try:
                    with open(regrid_file, "r") as f:
                        regrid_data = json.load(f)
                    regrid_neighbors = regrid_data.get("neighbors", [])

                    # Calculate expected batches
                    people_count = sum(1 for n in regrid_neighbors if n.get("entity_type") == "person")
                    org_count = sum(1 for n in regrid_neighbors if n.get("entity_type") == "organization")

                    from math import ceil
                    expected_person_batches = ceil(people_count / settings.BATCH_SIZE) if people_count > 0 else 0
                    expected_org_batches = ceil(org_count / settings.BATCH_SIZE) if org_count > 0 else 0

                    # Check for cached batch files
                    cached_person_batches = sum(1 for i in range(expected_person_batches)
                                                 if get_batch_cache_path(output_dir, "person", i, expected_person_batches).exists())
                    cached_org_batches = sum(1 for i in range(expected_org_batches)
                                              if get_batch_cache_path(output_dir, "organization", i, expected_org_batches).exists())

                    all_batches_complete = (cached_person_batches == expected_person_batches and
                                            cached_org_batches == expected_org_batches)

                    if not all_batches_complete:
                        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 📂 Found partial batch cache, will resume...")
                        print(f"   Person batches: {cached_person_batches}/{expected_person_batches}")
                        print(f"   Organization batches: {cached_org_batches}/{expected_org_batches}")
                        # Don't set skip_openai - fall through to batch processing
                        # But mark that we should use cached regrid data
                        resolved = regrid_neighbors
                except Exception as e:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⚠️ Failed to check batch cache: {e}")

        # Only skip OpenAI if ALL batches are complete OR we have vr_* files (legacy)
        if cache_coords_match and has_dr and not has_vr and settings.ENABLE_VERIFICATION and all_batches_complete:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 📂 Found existing deep research files, resuming verification...")
            print(f"   Skipping OpenAI stage - using {len(cached_dr_files)} cached dr_*.json files")
            skip_openai = True

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
                # Get target parcel and adjacent parcels for cached data
                target_parcel_info = await self.finder.get_target_parcel(
                    search_mode="COORDS", lat=lat, lon=lon
                )
                if target_parcel_info:
                    adjacent_pins = await self.finder.get_adjacent_parcels(
                        target_parcel_info["geometry"], target_parcel_info["pin"]
                    )
            else:
                lat, lon = None, None

            # Load raw parcels from cache for map generation
            raw_parcels_file = output_dir / "raw_parcels.json"
            if raw_parcels_file.exists():
                try:
                    with open(raw_parcels_file, "r") as f:
                        self.finder.raw_parcels = json.load(f)
                except Exception:
                    pass

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

                # Update adjacency status with fresh adjacent_pins data
                if adjacent_pins:
                    for neighbor in resolved:
                        neighbor_pins = neighbor.get("pins", [])
                        if any(p in adjacent_pins for p in neighbor_pins):
                            neighbor["owns_adjacent_parcel"] = "Yes"
                        else:
                            neighbor["owns_adjacent_parcel"] = "No"
            else:
                resolved = []

        # Scenario 1: Fresh run OR resume with partial batches
        if not skip_openai:
            # Generate unique run_id for this neighbor screening
            run_id = str(uuid.uuid4())
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🆔 Generated run_id: {run_id}"
            )

            # Check if we're resuming with cached regrid data (resolved was set in Scenario 2 check)
            # Also check regrid_all.json directly — Scenario 2 only triggers when
            # neighbor_final_merged.json exists, but on partial runs (some batches failed)
            # that file is never created. We still want to reuse batch caches.
            if resolved is None and location:
                regrid_file = output_dir / "regrid_all.json"
                if regrid_file.exists():
                    try:
                        with open(regrid_file, "r") as f:
                            regrid_data = json.load(f)
                        regrid_neighbors = regrid_data.get("neighbors", [])
                        # Check if any batch caches exist for this regrid data
                        existing_caches = list(output_dir.glob("batch_*.json"))
                        if regrid_neighbors and existing_caches:
                            resolved = regrid_neighbors
                            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 📂 Found regrid_all.json + {len(existing_caches)} batch caches, resuming partial run")
                    except Exception as e:
                        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⚠️ Failed to load regrid_all.json: {e}")

            resuming_with_cache = resolved is not None and len(resolved) > 0

            # Load raw parcels from cache when resuming (needed for map + valuation benchmark)
            if resuming_with_cache:
                raw_parcels_file = output_dir / "raw_parcels.json"
                if raw_parcels_file.exists():
                    try:
                        with open(raw_parcels_file, "r") as f:
                            self.finder.raw_parcels = json.load(f)
                        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 📂 Loaded {len(self.finder.raw_parcels)} raw parcels from cache")
                    except Exception:
                        pass

            # 1) Determine target parcel and get adjacent parcels
            target_parcel_info = None
            adjacent_pins = set()

            if resuming_with_cache:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 📂 Using cached Regrid data ({len(resolved)} neighbors)")
                # Still need target parcel info for adjacency
                if location:
                    lat, lon = [float(x) for x in location.split(",")]
                    target_parcel_info = await self.finder.get_target_parcel(
                        search_mode="COORDS", lat=lat, lon=lon
                    )
                    if target_parcel_info:
                        adjacent_pins = await self.finder.get_adjacent_parcels(
                            target_parcel_info["geometry"], target_parcel_info["pin"]
                        )
            elif pin:  # PIN-based search
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
                if not target_parcel_info:
                    print(f"❌ Failed to find target parcel at coordinates ({lat}, {lon})")
                    print("   Cannot proceed without target parcel. Check coordinates or Regrid coverage.")
                    return NeighborResult(
                        neighbors=[],
                        location_context=f"Target parcel not found at ({lat}, {lon})",
                        success=False,
                    ).model_dump()
                # Get adjacent parcels for the target
                adjacent_pins = await self.finder.get_adjacent_parcels(
                    target_parcel_info["geometry"], target_parcel_info["pin"]
                )

            # 2) Resolve neighbors (skip if resuming with cache)
            if resuming_with_cache:
                pass  # Already have resolved from cache
            elif neighbors:
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

            # Save Regrid results to JSON files if requested (skip when resuming with cache)
            if save_regrid_json and not resuming_with_cache:
                # Clear old batch caches since we have new Regrid data
                delete_batch_caches()

                saved_files = self._save_regrid_to_json(resolved)
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Regrid data saved to: {saved_files}"
                )

                # Also save raw parcels for map generation cache
                if self.finder.raw_parcels:
                    raw_parcels_file = output_dir / "raw_parcels.json"
                    # Trash old file before overwriting (preserves backup)
                    if raw_parcels_file.exists():
                        subprocess.run(["trash", str(raw_parcels_file)], check=False)
                    with open(raw_parcels_file, "w") as f:
                        json.dump(self.finder.raw_parcels, f)
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Raw parcels saved for map generation")

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

            # 4) Batch - create indexed batches for caching
            # Structure: (batch_idx, chunk_data, entity_type, total_batches_for_type)
            indexed_batches: List[tuple[int, list, str, int]] = []

            # Count total batches per type first
            people_chunks = await self._chunk(people, settings.BATCH_SIZE)
            org_chunks = await self._chunk(orgs, settings.BATCH_SIZE)

            for idx, chunk in enumerate(people_chunks):
                indexed_batches.append((idx, chunk, "person", len(people_chunks)))
            for idx, chunk in enumerate(org_chunks):
                indexed_batches.append((idx, chunk, "organization", len(org_chunks)))

            context = {
                "county": county,
                "state": state,
                "city": city,
                "radius_mi": radius_mi,
            }

            # 4.5) Check for cached batches
            cached_results = []
            batches_to_run = []

            for batch_idx, chunk, etype, total_for_type in indexed_batches:
                cache_path = get_batch_cache_path(output_dir, etype, batch_idx, total_for_type)
                cached = load_cached_batch(cache_path)
                if cached:
                    cached_results.append((batch_idx, etype, cached))
                    print(f"   ✅ Batch {batch_idx + 1}/{total_for_type} ({etype}s) loaded from cache")
                else:
                    batches_to_run.append((batch_idx, chunk, etype, total_for_type))

            total_batches = len(indexed_batches)
            print(f"\n📦 Batch processing: {total_batches} total batches ({len(people_chunks)} person, {len(org_chunks)} organization)")
            if cached_results:
                print(f"   📂 {len(cached_results)} cached, {len(batches_to_run)} to run")

            # 5) Fire uncached batches concurrently
            sem = asyncio.Semaphore(settings.CONCURRENCY_LIMIT)

            async def run(batch_idx: int, names: List[str], etype: str, total_for_type: int):
                async with sem:
                    try:
                        result = await self.engine.run_batch(
                            names, context, etype, on_event=on_event
                        )
                        # Cache successful results
                        neighbors_count = len(result.get("neighbors", [])) if result else 0
                        print(f"   [DEBUG] Batch {batch_idx + 1}/{total_for_type} ({etype}s) returned {neighbors_count} neighbors")
                        if result and not isinstance(result, Exception) and result.get("neighbors"):
                            cache_path = get_batch_cache_path(output_dir, etype, batch_idx, total_for_type)
                            print(f"   [DEBUG] Saving batch cache to: {cache_path}")
                            save_batch_result(cache_path, result, batch_idx, total_for_type, etype)
                        return (batch_idx, etype, result)
                    except Exception as e:
                        print(f"   ❌ Batch {batch_idx + 1}/{total_for_type} ({etype}s) FAILED: {e}")
                        raise

            if batches_to_run:
                tasks = [run(batch_idx, chunk, etype, total_for_type)
                         for batch_idx, chunk, etype, total_for_type in batches_to_run]
                new_results = await asyncio.gather(*tasks, return_exceptions=True)
            else:
                new_results = []
                print("   ✅ All batches cached, skipping API calls")

            # 6) Merge results (cached + new)
            merged = []
            flat_citations = []
            overview_summaries = []
            saved_filepaths = []  # Track saved deep research files

            # Process cached results
            for batch_idx, etype, cached in cached_results:
                merged.extend(cached.get("neighbors", []))
                flat_citations.extend(cached.get("annotations", []))
                if cached.get("overview_summary"):
                    overview_summaries.append(cached["overview_summary"])
                if cached.get("saved_filepath"):
                    saved_filepaths.append(cached["saved_filepath"])

            # Process new results
            completed_count = len(cached_results)
            failed_count = 0
            for r in new_results:
                if isinstance(r, Exception):
                    failed_count += 1
                    print(f"   ❌ Batch failed with exception: {r}")
                    if on_event:
                        on_event(
                            {
                                "type": "error",
                                "batch_size": 0,
                                "entity_type": "unknown",
                                "message": str(r),
                                "meta": {},
                            }
                        )
                    continue
                batch_idx, etype, result = r
                if result and not isinstance(result, Exception):
                    merged.extend(result.get("neighbors", []))
                    flat_citations.extend(result.get("annotations", []))
                    if result.get("overview_summary"):
                        overview_summaries.append(result["overview_summary"])
                    if result.get("saved_filepath"):
                        saved_filepaths.append(result["saved_filepath"])
                    completed_count += 1
                    print(f"   📦 Batch {batch_idx + 1} ({etype}s) completed [{completed_count}/{total_batches} total]")

            # Summary of batch processing
            if failed_count > 0:
                print(f"\n   ⚠️ Batch processing summary: {completed_count}/{total_batches} succeeded, {failed_count} failed")
            else:
                print(f"\n   ✅ All {total_batches} batches completed successfully")

            # 6.25) VERIFICATION STAGE (Gemini Deep Research)
            # Run verification on individual dr_*.json files before merge/dedupe
            if settings.ENABLE_VERIFICATION and saved_filepaths:
                print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔬 Starting Verification Stage...")
                print(f"   Processing {len(saved_filepaths)} deep research files...")

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

        # Synthesize overview from batch summaries using Gemini 3 Flash
        # This creates a coherent summary with accurate counts from merged data
        location_ctx = f"Neighbors within {radius_mi} mi of {location or (county + ', ' + state if county and state else 'unknown')}"
        if overview_summaries:
            print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 📝 Synthesizing overview from {len(overview_summaries)} batch summaries...")
            combined_overview = await synthesize_overview(
                batch_overviews=overview_summaries,
                neighbors=merged,
                location_context=location_ctx,
            )
        else:
            combined_overview = None

        # Validate neighbors in memory (still has PII — not persisted)
        validated_neighbors = []
        for p in merged:
            try:
                validated_neighbors.append(NeighborProfile(**p))
            except Exception as e:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⚠️  Skipping invalid neighbor: {p.get('neighbor_id', '?')} - {str(e)}"
                )
                continue

        # Generate neighbor map visualization BEFORE aggregation
        # (map needs validated_neighbors for parcel coloring, but labels are anonymized)
        output_dir = Path(__file__).parent.parent / "neighbor_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        map_image_path = None
        map_thumbnail_path = None
        map_metadata = None
        try:
            if (
                settings.GENERATE_MAP
                and settings.MAPBOX_ACCESS_TOKEN
                and target_parcel_info
                and self.finder.raw_parcels
            ):
                print(f"\n🗺️  Generating neighbor map visualization...")
                map_generator = NeighborMapGenerator(
                    target_parcel=target_parcel_info,
                    raw_parcels=self.finder.raw_parcels,
                    neighbor_profiles=validated_neighbors,
                    mapbox_token=settings.MAPBOX_ACCESS_TOKEN,
                    output_dir=str(output_dir.parent / "neighbor_map_outputs"),
                    style=settings.MAPBOX_STYLE,
                    width=settings.MAP_WIDTH,
                    height=settings.MAP_HEIGHT,
                    padding=settings.MAP_PADDING,
                    retina=settings.MAP_RETINA,
                )

                map_result = map_generator.generate(run_id=run_id)

                if map_result.success:
                    print(f"✅ Map generated: {map_result.image_path}")
                    print(
                        f"   Strategy: {map_result.generation_result.strategy_used}, "
                        f"Parcels: {map_result.generation_result.parcels_rendered}"
                    )
                    map_image_path = map_result.image_path
                    map_thumbnail_path = map_result.thumbnail_path
                    map_metadata = map_result.metadata
                else:
                    print(
                        f"⚠️ Map generation failed: {map_result.generation_result.error_message if map_result.generation_result else 'Unknown error'}"
                    )

                # Generate full-page map (includes ALL neighbors regardless of influence)
                generate_fullpage_map(
                    target_parcel=target_parcel_info,
                    raw_parcels=self.finder.raw_parcels,
                    neighbor_profiles=validated_neighbors,
                    output_dir=output_dir,
                    run_id=run_id,
                )

            elif not settings.MAPBOX_ACCESS_TOKEN:
                print("⚠️ Skipping map generation - MAPBOX_ACCESS_TOKEN not set")
            elif not target_parcel_info:
                print("⚠️ Skipping map generation - no target parcel info available")
        except Exception as e:
            print(f"⚠️ Failed to generate map: {e}")

        # =====================================================================
        # AGGREGATION BOUNDARY — Convert PII-bearing profiles to aggregate stats
        # After this point, no individual names/PINs/claims are persisted.
        # =====================================================================
        merged_dicts = [
            n.dict() if hasattr(n, "dict") else n for n in validated_neighbors
        ]
        final = await aggregate_neighbors(
            profiles=merged_dicts,
            location_context=location_ctx,
            overview_summary=combined_overview,
            city=city,
            county=county,
            state=state,
            run_id=run_id,
            runtime_minutes=round((time.time() - t0) / 60.0, 2),
            map_image_path=map_image_path,
            map_thumbnail_path=map_thumbnail_path,
            map_metadata=map_metadata,
        )

        # Save the PII-free aggregate result
        final_output_path = output_dir / "neighbor_final_merged.json"
        with open(final_output_path, "w", encoding="utf-8") as f:
            json.dump(final, f, indent=2, ensure_ascii=False, default=str)

        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 💾 Saved aggregate output to: {final_output_path.name}"
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

        # Save aggregate data to database (no individual PII)
        try:
            db = NeighborDBConnector()
            if db.conn:
                db.save_neighbor_aggregate(
                    run_id=run_id,
                    aggregate_data=final,
                    location=location,
                    pin=pin
                    or (target_parcel_info.get("pin") if target_parcel_info else None),
                    county=county,
                    state=state,
                    city=city,
                    county_path=county_path,
                )
                db.close()
            else:
                print("⚠️ Database connection not available, skipping aggregate save")
        except Exception as e:
            print(f"⚠️ Failed to save aggregate to database: {e}")

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

        # =====================================================================
        # PII CLEANUP — Delete all intermediate files containing personal data
        # =====================================================================
        self._cleanup_pii_files()

        return final

    def _cleanup_pii_files(self):
        """Delete all intermediate files containing PII.

        Called after aggregation to ensure no names, PINs, claims,
        or other personally identifiable information remains on disk.
        """
        output_dir = Path(__file__).parent.parent / "neighbor_outputs"
        dr_dir = Path(__file__).parent.parent / "deep_research_outputs"

        patterns_to_delete = [
            (output_dir, "regrid_people.json"),
            (output_dir, "regrid_organizations.json"),
            (output_dir, "regrid_all.json"),
            (output_dir, "raw_parcels.json"),
            (output_dir, "batch_*.json"),
            (dr_dir, "dr_*.json"),
            (dr_dir, "vr_*.json"),
            (dr_dir, "*.thinking.md"),
            (dr_dir, "*_debug*.md"),
            (dr_dir, "*_DEBUG_*.json"),
            (dr_dir, "gemini_raw_*.txt"),
        ]

        deleted_count = 0
        for directory, pattern in patterns_to_delete:
            if not directory.exists():
                continue
            for f in directory.glob(pattern):
                try:
                    f.unlink()
                    deleted_count += 1
                except OSError as e:
                    print(f"⚠️  Failed to delete {f.name}: {e}")

        if deleted_count:
            print(f"🧹 PII cleanup: deleted {deleted_count} intermediate files")
