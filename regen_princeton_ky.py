import json
import subprocess
from pathlib import Path
import sys

root = Path.cwd()
sys.path.insert(0, str(root / "src"))

from neighbor.mapping.map_generator import NeighborMapGenerator
from neighbor.mapping.fullpage_map_generator import FullPageMapGenerator
from neighbor.models.schemas import NeighborProfile
from neighbor.config.settings import settings

commit = "9e4696d"
merged_path = "src/neighbor/neighbor_outputs/neighbor_final_merged.json"
raw_parcels_path = "src/neighbor/neighbor_outputs/raw_parcels.json"


def read_git_json(path: str):
    res = subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git show failed for {path}: {res.stderr.strip()}")
    return json.loads(res.stdout)

if not settings.MAPBOX_ACCESS_TOKEN:
    raise SystemExit("MAPBOX_ACCESS_TOKEN not set in environment/.env")

merged = read_git_json(merged_path)
raw_parcels = read_git_json(raw_parcels_path)

target_parcel_info = merged.get("target_parcel_info")
if not target_parcel_info:
    raise SystemExit("target_parcel_info missing from merged JSON")

profiles = []
for n in merged.get("neighbors", []):
    try:
        profiles.append(NeighborProfile(**n))
    except Exception as e:
        print(f"⚠️ Skipping neighbor {n.get('name','?')}: {e}")

output_dir = root / "map_regen_outputs" / "princeton_ky"
output_dir.mkdir(parents=True, exist_ok=True)
run_id = merged.get("run_id") or "princeton_ky"

print(f"Commit: {commit}")
print(f"Output dir: {output_dir}")
print(f"Run ID: {run_id}")
print(f"Raw parcels: {len(raw_parcels)}")
print(f"Neighbors: {len(profiles)}")

standard = NeighborMapGenerator(
    target_parcel=target_parcel_info,
    raw_parcels=raw_parcels,
    neighbor_profiles=profiles,
    mapbox_token=settings.MAPBOX_ACCESS_TOKEN,
    output_dir=str(output_dir),
    style=settings.MAPBOX_STYLE,
    width=settings.MAP_WIDTH,
    height=settings.MAP_HEIGHT,
    padding=settings.MAP_PADDING,
    retina=settings.MAP_RETINA,
)
standard_result = standard.generate(run_id=run_id)
if standard_result.success:
    print("✅ Standard map generated")
    print(f"   Full: {standard_result.image_path}")
    print(f"   Thumb: {standard_result.thumbnail_path}")
else:
    err = standard_result.generation_result.error_message if standard_result.generation_result else "Unknown"
    print(f"❌ Standard map failed: {err}")

fullpage = FullPageMapGenerator(
    target_parcel=target_parcel_info,
    raw_parcels=raw_parcels,
    neighbor_profiles=profiles,
    mapbox_token=settings.MAPBOX_ACCESS_TOKEN,
    output_dir=str(output_dir),
    style=settings.MAPBOX_STYLE,
    width=1280,
    height=720,
    padding=80,
    retina=True,
)
fullpage_result = fullpage.generate(run_id=run_id)
if fullpage_result.success:
    print("✅ Fullpage map generated")
    print(f"   Fullpage: {fullpage_result.image_path}")
else:
    print("❌ Fullpage map failed")
