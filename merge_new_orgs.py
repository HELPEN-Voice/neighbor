#!/usr/bin/env python3
"""
Merge the newly retrieved organizations into neighbor_final_merged.json
"""

import json
from pathlib import Path

# Paths
base = Path(__file__).parent / "src/neighbor"
merged_file = base / "neighbor_outputs/neighbor_final_merged.json"
new_orgs_file = (
    base / "deep_research_outputs/dr_organizations_20251106_164255_4179.json"
)

# Load files
with open(merged_file, "r") as f:
    merged = json.load(f)

with open(new_orgs_file, "r") as f:
    new_orgs_data = json.load(f)

print(f"Current neighbors: {len(merged['neighbors'])}")
print(f"New organizations to add: {len(new_orgs_data['neighbors'])}")

# Add the new organizations to the merged file
for org in new_orgs_data["neighbors"]:
    # Check if already exists by name
    if not any(n["name"] == org["name"] for n in merged["neighbors"]):
        merged["neighbors"].append(org)
        print(f"  ✓ Added: {org['name']}")
    else:
        print(f"  ⚠ Skipped (already exists): {org['name']}")

print(f"\nTotal neighbors after merge: {len(merged['neighbors'])}")

# Save back
with open(merged_file, "w") as f:
    json.dump(merged, f, indent=2)

print(f"\n✅ Merged file updated: {merged_file}")
