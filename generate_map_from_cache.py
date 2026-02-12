"""Generate neighbor map from cached data (no API calls except Mapbox Static Images)."""

import json
import sys
from pathlib import Path

# Add src to path so neighbor module is importable
sys.path.insert(0, str(Path(__file__).parent / "src"))

from neighbor.mapping.map_generator import NeighborMapGenerator
from neighbor.models.schemas import NeighborProfile
from neighbor.config.settings import settings


def main():
    output_dir = Path(__file__).parent / "src" / "neighbor" / "neighbor_outputs"
    merged_file = output_dir / "neighbor_final_merged.json"
    raw_parcels_file = output_dir / "raw_parcels.json"

    if not merged_file.exists():
        print(f"❌ {merged_file} not found")
        return
    if not raw_parcels_file.exists():
        print(f"❌ {raw_parcels_file} not found")
        return
    if not settings.MAPBOX_ACCESS_TOKEN:
        print("❌ MAPBOX_ACCESS_TOKEN not set")
        return

    with open(merged_file) as f:
        merged = json.load(f)
    with open(raw_parcels_file) as f:
        raw_parcels = json.load(f)

    target_parcel_info = merged.get("target_parcel_info")
    if not target_parcel_info:
        print("❌ No target_parcel_info in merged data")
        return

    neighbors = merged.get("neighbors", [])
    profiles = []
    for n in neighbors:
        try:
            profiles.append(NeighborProfile(**n))
        except Exception as e:
            print(f"⚠️ Skipping neighbor {n.get('name', '?')}: {e}")

    print(f"📍 Target: PIN {target_parcel_info.get('pin')} ({target_parcel_info.get('lat')}, {target_parcel_info.get('lon')})")
    print(f"📦 Raw parcels: {len(raw_parcels)}")
    print(f"👥 Neighbor profiles: {len(profiles)}")

    generator = NeighborMapGenerator(
        target_parcel=target_parcel_info,
        raw_parcels=raw_parcels,
        neighbor_profiles=profiles,
        mapbox_token=settings.MAPBOX_ACCESS_TOKEN,
        output_dir=str(output_dir.parent / "neighbor_map_outputs"),
        style=settings.MAPBOX_STYLE,
        width=settings.MAP_WIDTH,
        height=settings.MAP_HEIGHT,
        padding=settings.MAP_PADDING,
        retina=settings.MAP_RETINA,
    )

    run_id = merged.get("run_id", "manual")
    result = generator.generate(run_id=run_id)

    if result.success:
        print(f"\n✅ Map generated successfully")
        print(f"   Image: {result.image_path}")
        print(f"   Thumbnail: {result.thumbnail_path}")
        print(f"   Strategy: {result.generation_result.strategy_used}")
        print(f"   Parcels rendered: {result.generation_result.parcels_rendered}")
        print(f"   Labels: {len(result.labels)}")

        # Update neighbor_final_merged.json with map data
        merged["map_image_path"] = result.image_path
        merged["map_thumbnail_path"] = result.thumbnail_path
        merged["map_legend_html"] = result.legend_html
        merged["map_labels"] = result.labels
        merged["map_metadata"] = result.metadata

        with open(merged_file, "w") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False, default=str)
        print(f"   💾 Updated {merged_file.name} with map data")
    else:
        err = result.generation_result.error_message if result.generation_result else "Unknown"
        print(f"\n❌ Map generation failed: {err}")


if __name__ == "__main__":
    main()
