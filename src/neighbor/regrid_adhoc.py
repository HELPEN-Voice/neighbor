#!/usr/bin/env python3
"""
Ad hoc Regrid data extraction script for testing the neighbor pipeline.

This script fetches parcel data from Regrid API and saves it to CSV/JSON formats
that match what the neighbor pipeline expects, allowing for offline testing.

Uses expanding radii starting at 0.25 miles to ensure nearest parcels first,
stopping when max_parcels (default 50) unique parcels are accumulated.
This ensures billing optimization while guaranteeing nearest-neighbor ordering.

Usage:
    python regrid_adhoc.py --coords 44.8951,-90.4420
    python regrid_adhoc.py --pin 018.0508.000 --county-path /us/wi/clark/green-grove
    python regrid_adhoc.py --coords 44.8951,-90.4420 --max-parcels 100 --initial-radius 0.5
"""

import requests
import pandas as pd
import json
import argparse
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from collections import OrderedDict


def guess_entity_type(name: str) -> str:
    """
    Simple heuristic to guess if an owner name is a person or organization.
    Matches the logic in ../utils/entity.py
    """
    if not name:
        return "unknown"

    name_lower = name.lower()

    # Organization indicators
    org_indicators = [
        "llc",
        "inc",
        "corp",
        "company",
        "trust",
        "bank",
        "group",
        "partners",
        "association",
        "foundation",
        "holdings",
        "properties",
        "estates",
        "investments",
        "capital",
        "ventures",
        "development",
        "realty",
        "land",
        "farms",
        "ranch",
        "energy",
        "solar",
        "wind",
        "church",
        "county",
        "city",
        "state",
        "township",
        "village",
        "school",
        "district",
        "authority",
        "commission",
        "board",
        "&",
        "and sons",
        "and daughters",
        "brothers",
        "sisters",
    ]

    for indicator in org_indicators:
        if indicator in name_lower:
            return "organization"

    # Check for common business suffixes
    if name_lower.endswith((".com", ".org", ".net", ".gov", ".edu")):
        return "organization"

    # Default to person
    return "person"


def get_name_key(name: str) -> str:
    """
    Get a normalized key for name comparison (first and last name only).
    This is used to identify potential duplicates.
    Examples:
        "John H. Smith" -> "john smith"
        "John Smith" -> "john smith"
        "Mary Ann Johnson Jr." -> "mary johnson jr."
    """
    if not name:
        return ""

    parts = name.split()
    if len(parts) <= 2:
        # Already just first and last (or single name)
        return name.lower()

    # Check for suffixes to preserve
    suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}

    # If last part is a suffix, keep first name + second-to-last name + suffix
    if parts[-1].lower() in suffixes and len(parts) >= 3:
        # e.g., "John Henry Smith Jr." -> "john smith jr."
        return f"{parts[0]} {parts[-2]} {parts[-1]}".lower()

    # Otherwise, just first and last
    # e.g., "John Henry Smith" -> "john smith"
    return f"{parts[0]} {parts[-1]}".lower()


def choose_most_complete_name(name1: str, name2: str) -> str:
    """
    Choose the most complete version of a name (with middle name/initial).
    Examples:
        ("John Smith", "John H. Smith") -> "John H. Smith"
        ("Jane Smith", "Jane Helen Smith") -> "Jane Helen Smith"
    """
    # Count meaningful parts (non-empty after stripping periods)
    parts1 = [p for p in name1.split() if p.replace(".", "").strip()]
    parts2 = [p for p in name2.split() if p.replace(".", "").strip()]

    # Return the one with more parts (more complete)
    if len(parts2) > len(parts1):
        return name2
    elif len(parts1) > len(parts2):
        return name1

    # If same number of parts, prefer the one with longer middle part
    # (full name over initial)
    if len(parts1) >= 3 and len(parts2) >= 3:
        middle1 = parts1[1].replace(".", "")
        middle2 = parts2[1].replace(".", "")
        if len(middle2) > len(middle1):
            return name2

    return name1  # Default to first if truly identical


def get_target_parcel(
    api_token: str,
    search_mode: str,
    lat: float = None,
    lon: float = None,
    pin: str = None,
    county_path: str = None,
) -> Optional[Dict]:
    """
    Identifies a single target parcel by either coordinates or PIN.
    Returns dict with pin, geometry, lat, lon, county_path.
    """
    print(f"🔍 Identifying target parcel using mode: {search_mode}...")

    try:
        if search_mode == "COORDS":
            url = "https://app.regrid.com/api/v2/parcels/point"
            params = {"token": api_token, "lat": lat, "lon": lon, "limit": 1}
            response = requests.get(url, params=params)
        elif search_mode == "PIN":
            url = "https://app.regrid.com/api/v2/parcels/apn"
            params = {"token": api_token, "parcelnumb": pin}
            if county_path:
                params["path"] = county_path
            response = requests.get(url, params=params)
        else:
            print("❌ Invalid search mode.")
            return None

        response.raise_for_status()
        data = response.json()
        features = data.get("parcels", {}).get("features", [])

        if not features:
            print("❌ Target parcel could not be found with the provided input.")
            return None

        target = features[0]
        fields = target.get("properties", {}).get("fields", {})
        context = target.get("properties", {}).get("context", {})

        target_info = {
            "pin": fields.get("parcelnumb"),
            "geometry": target.get("geometry"),
            "lat": fields.get("lat"),
            "lon": fields.get("lon"),
            "county_path": context.get("path"),
            "county": context.get("name"),
            "state": context.get("state"),
        }

        if not all(
            [
                target_info["pin"],
                target_info["geometry"],
                target_info["lat"],
                target_info["lon"],
            ]
        ):
            print("⚠️ Found parcel but it is missing critical information.")
            return None

        print(f"✅ Successfully identified target parcel. PIN: {target_info['pin']}")
        print(f"   Location: {target_info['county']}, {target_info['state']}")
        return target_info

    except requests.exceptions.HTTPError as http_err:
        print(f"❌ HTTP error occurred: {http_err}")
        return None
    except Exception as e:
        print(f"❌ Error occurred: {e}")
        return None


def get_adjacent_parcels(
    api_token: str, target_geometry: Dict, target_pin: str
) -> Set[str]:
    """
    Finds PINs for all parcels adjacent to the target geometry.
    """
    print(f"\n🔍 Finding parcels adjacent to target PIN: {target_pin}...")

    try:
        url = "https://app.regrid.com/api/v2/parcels/area"
        # Use POST to avoid 414 Request-URI Too Large with complex geometries
        params = {"token": api_token}
        payload = {"geojson": target_geometry}

        response = requests.post(url, params=params, json=payload)
        response.raise_for_status()
        data = response.json()

        features = data.get("parcels", {}).get("features", [])
        adjacent_pins = set()

        for parcel in features:
            pin = parcel.get("properties", {}).get("fields", {}).get("parcelnumb")
            if pin and pin != target_pin:
                adjacent_pins.add(pin)

        print(f"✅ Found {len(adjacent_pins)} adjacent parcels.")
        return adjacent_pins

    except Exception as e:
        print(f"⚠️ Error finding adjacent parcels: {e}")
        return set()


def get_closest_landowners(
    api_token: str,
    lat: float,
    lon: float,
    max_parcels: int = 50,
    adjacent_pins: Set[str] = None,
    initial_radius_mi: float = 0.25,
) -> List[Dict]:
    """
    Fetches parcels for the closest distinct landowners to a central point.
    Returns a list of owner dictionaries matching the pipeline format.

    Uses expanding radii starting at 0.25 miles to ensure nearest parcels first,
    stopping when max_parcels unique parcels are accumulated.

    Args:
        api_token: Regrid API token
        lat: Latitude of center point
        lon: Longitude of center point
        max_parcels: Maximum number of parcels to fetch (billing optimization)
        adjacent_pins: Set of PINs that are adjacent to target parcel
        initial_radius_mi: Starting radius in miles (doubles each iteration)
    """
    base_url = "https://app.regrid.com/api/v2/parcels/point"
    max_radius_meters = 32000  # ~20 miles max
    radius_mi = initial_radius_mi
    radius_meters = radius_mi * 1609.34

    # Track unique parcels across expansions (by PIN)
    all_parcels = {}  # pin -> parcel feature
    all_owners = OrderedDict()

    print(f"\n🔍 Fetching up to {max_parcels} nearest parcels using expanding radii...")

    while len(all_parcels) < max_parcels and radius_meters <= max_radius_meters:
        # Request enough to potentially fill remaining quota
        remaining = max_parcels - len(all_parcels)
        request_limit = min(remaining + 20, 100)

        params = {
            "lat": lat,
            "lon": lon,
            "radius": int(radius_meters),
            "limit": request_limit,
            "token": api_token,
        }

        print(f"   Searching {radius_mi:.2f} mi radius ({int(radius_meters)}m)...")

        try:
            response = requests.get(base_url, params=params)
            response.raise_for_status()
            data = response.json()
            parcels = data.get("parcels", {}).get("features", [])

            if not parcels:
                print(f"      No parcels found, expanding radius...")
                # Use 1.5x multiplier up to 2 miles, then 2x after
                if radius_mi < 2.0:
                    radius_mi *= 1.5
                else:
                    radius_mi *= 2
                radius_meters = radius_mi * 1609.34
                continue

            # Add new unique parcels (by PIN)
            new_count = 0
            for parcel in parcels:
                if len(all_parcels) >= max_parcels:
                    break
                pin = parcel.get("properties", {}).get("fields", {}).get("parcelnumb")
                if pin and pin not in all_parcels:
                    all_parcels[pin] = parcel
                    new_count += 1

            print(f"      Found {new_count} new parcels (total: {len(all_parcels)})")

            if len(all_parcels) >= max_parcels:
                print(f"   Reached {max_parcels} parcel limit")
                break

            # Expand radius for next iteration
            # Use 1.5x multiplier up to 2 miles, then 2x after
            if radius_mi < 2.0:
                radius_mi *= 1.5
            else:
                radius_mi *= 2
            radius_meters = radius_mi * 1609.34

        except Exception as e:
            print(f"⚠️ Error fetching from Regrid: {e}")
            break

    if not all_parcels:
        print("   No parcels found within maximum search radius.")
        return []

    print(f"   Accumulated {len(all_parcels)} unique parcels")
    parcels = list(all_parcels.values())

    # Process parcels to extract unique owners
    for parcel in parcels:
        properties = parcel.get("properties", {})
        fields = properties.get("fields", {})
        enhanced = properties.get("enhanced_ownership", [])

        # Extract owner name - try enhanced ownership first
        owner_name = None
        if enhanced and len(enhanced) > 0:
            eo = enhanced[0]
            if eo.get("eo_owner"):
                owner_name = str(eo["eo_owner"])
            elif eo.get("eo_ownerfirst") and eo.get("eo_ownerlast"):
                owner_name = f"{eo['eo_ownerfirst']} {eo['eo_ownerlast']}"

        # Fallback to regular fields
        if not owner_name:
            for field in [
                "owner",
                "owner1",
                "ownername",
                "ownname1",
                "owner_name",
            ]:
                if fields.get(field):
                    value = str(fields[field]).strip()
                    if value and value.lower() not in [
                        "null",
                        "none",
                        "",
                        "unknown",
                        "unavailable",
                    ]:
                        owner_name = value
                        break

        if not owner_name:
            continue

        # Clean and title case
        owner_name = owner_name.strip().title()

        # Get a normalized key for comparison
        name_key = get_name_key(owner_name)

        # Extract PIN
        pin = (
            fields.get("parcelnumb")
            or fields.get("parcelnumb_no_formatting")
            or fields.get("ll_uuid")
            or ""
        )

        # Add to owners dict using name key
        if name_key not in all_owners:
            all_owners[name_key] = {
                "name": owner_name,
                "entity_type": guess_entity_type(owner_name),
                "pins": [],
                "owns_adjacent_parcel": "No",
                "acres": 0.0,
                "assessed_value": 0.0,
                "mailing_address": None,
                "name_variations": set(),
            }
        else:
            # Owner already exists - update with most complete name
            existing_name = all_owners[name_key]["name"]
            most_complete_name = choose_most_complete_name(
                existing_name, owner_name
            )
            all_owners[name_key]["name"] = most_complete_name

        # Track original name variation
        all_owners[name_key]["name_variations"].add(owner_name)

        # Add PIN if not already present
        if pin and pin not in all_owners[name_key]["pins"]:
            all_owners[name_key]["pins"].append(pin)

            # Check if adjacent
            if adjacent_pins and pin in adjacent_pins:
                all_owners[name_key]["owns_adjacent_parcel"] = "Yes"

            # Accumulate acres and value
            all_owners[name_key]["acres"] += float(
                fields.get("ll_gisacre", 0) or 0
            )
            all_owners[name_key]["assessed_value"] += float(
                fields.get("parval", 0) or 0
            )

            # Get mailing address (only set once)
            if not all_owners[name_key]["mailing_address"]:
                mailing_parts = [
                    fields.get("mailadd"),
                    fields.get("mail_city"),
                    fields.get("mail_state2"),
                ]
                mailing_address = ", ".join(filter(None, mailing_parts))
                if fields.get("mail_zip"):
                    mailing_address += f" {fields.get('mail_zip')}"
                if mailing_address:
                    all_owners[name_key]["mailing_address"] = mailing_address

    # Convert to list (return all distinct owners found from the parcels)
    result = []
    for owner_data in list(all_owners.values()):
        # Log merged name variations if any
        if len(owner_data.get("name_variations", set())) > 1:
            variations = owner_data["name_variations"]
            final_name = owner_data["name"]
            print(f"  Merged name variations into '{final_name}': {variations}")

        # Remove name_variations from final output (it's a set and not JSON serializable)
        clean_owner_data = {
            k: v for k, v in owner_data.items() if k != "name_variations"
        }
        result.append(clean_owner_data)

    print(f"✅ Extracted {len(result)} distinct owners from {len(parcels)} parcels")
    return result


def save_outputs(
    owners_data: List[Dict], target_info: Dict, output_dir: Path
) -> Dict[str, Path]:
    """
    Save data in multiple formats that match what the neighbor pipeline expects.
    Returns dict of saved file paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    saved_files = {}

    # 1. Save CSV format (for easy human review and potential pipeline input)
    csv_data = []
    for owner in owners_data:
        csv_data.append(
            {
                "Name": owner["name"],
                "Entity Type": owner["entity_type"],
                "Owned Parcels (PINs)": ", ".join(owner["pins"]),
                "Owns Adjacent Parcel": owner["owns_adjacent_parcel"],
                "Total Acres": round(owner["acres"], 2),
                "Total Assessed Value": round(owner["assessed_value"], 2),
                "Mailing Address": owner.get("mailing_address", "N/A"),
            }
        )

    csv_file = output_dir / "regrid_adhoc_results.csv"
    df = pd.DataFrame(csv_data)
    df.to_csv(csv_file, index=False)
    saved_files["csv"] = csv_file
    print(f"\n📄 Saved CSV to: {csv_file}")

    # 2. Save JSON formats matching pipeline structure
    # Split by entity type as the pipeline does
    people_data = [o for o in owners_data if o["entity_type"] == "person"]
    orgs_data = [o for o in owners_data if o["entity_type"] == "organization"]

    # Save people JSON
    if people_data:
        people_file = output_dir / "regrid_people.json"
        with open(people_file, "w") as f:
            json.dump({"entity_type": "person", "neighbors": people_data}, f, indent=2)
        saved_files["people_json"] = people_file
        print(f"📄 Saved people JSON to: {people_file}")

    # Save organizations JSON
    if orgs_data:
        orgs_file = output_dir / "regrid_organizations.json"
        with open(orgs_file, "w") as f:
            json.dump(
                {"entity_type": "organization", "neighbors": orgs_data}, f, indent=2
            )
        saved_files["organizations_json"] = orgs_file
        print(f"📄 Saved organizations JSON to: {orgs_file}")

    # Save combined JSON
    all_file = output_dir / "regrid_all.json"
    with open(all_file, "w") as f:
        json.dump(
            {
                "total": len(owners_data),
                "neighbors": owners_data,
                "target_parcel": target_info,
                "generated": datetime.now().isoformat(),
            },
            f,
            indent=2,
        )
    saved_files["all_json"] = all_file
    print(f"📄 Saved combined JSON to: {all_file}")

    return saved_files


def main():
    parser = argparse.ArgumentParser(
        description="Ad hoc Regrid data extraction for neighbor pipeline testing"
    )

    # Search mode arguments
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--coords", type=str, help='Coordinates as "lat,lon" (e.g., "44.8951,-90.4420")'
    )
    group.add_argument("--pin", type=str, help='Parcel PIN (e.g., "018.0508.000")')

    # Additional arguments
    parser.add_argument(
        "--county-path",
        type=str,
        help='County path for PIN search (e.g., "/us/wi/clark/green-grove")',
    )
    parser.add_argument(
        "--max-parcels",
        type=int,
        default=50,
        help="Maximum parcels to fetch from Regrid API (default: 50, billing optimization)",
    )
    parser.add_argument(
        "--initial-radius",
        type=float,
        default=0.25,
        help="Starting search radius in miles, doubles each iteration (default: 0.25)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./neighbor_adhoc_outputs",
        help="Output directory for results (default: ./neighbor_adhoc_outputs)",
    )
    parser.add_argument(
        "--token", type=str, help="Regrid API token (or set REGRID_API_KEY env var)"
    )

    args = parser.parse_args()

    # Get API token
    api_token = args.token or os.getenv("REGRID_API_KEY")
    if not api_token:
        print(
            "❌ Error: Regrid API token required. Provide via --token or REGRID_API_KEY env var"
        )
        return 1

    # Determine search mode and parameters
    if args.coords:
        try:
            lat, lon = map(float, args.coords.split(","))
            search_mode = "COORDS"
            target_info = get_target_parcel(api_token, "COORDS", lat=lat, lon=lon)
        except ValueError:
            print("❌ Error: Invalid coordinates format. Use 'lat,lon'")
            return 1
    else:  # args.pin
        search_mode = "PIN"
        target_info = get_target_parcel(
            api_token, "PIN", pin=args.pin, county_path=args.county_path
        )

    if not target_info:
        print("❌ Failed to identify target parcel")
        return 1

    # Get adjacent parcels
    adjacent_pins = get_adjacent_parcels(
        api_token, target_info["geometry"], target_info["pin"]
    )

    # Get closest landowners
    owners_data = get_closest_landowners(
        api_token,
        target_info["lat"],
        target_info["lon"],
        max_parcels=args.max_parcels,
        adjacent_pins=adjacent_pins,
        initial_radius_mi=args.initial_radius,
    )

    if not owners_data:
        print("❌ No landowners found")
        return 1

    # Save outputs
    output_dir = Path(args.output_dir)
    saved_files = save_outputs(owners_data, target_info, output_dir)

    # Print summary
    print("\n" + "=" * 60)
    print("✅ AD HOC REGRID EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"Target Parcel: {target_info['pin']}")
    print(f"Location: {target_info['county']}, {target_info['state']}")
    print(f"Total Owners Found: {len(owners_data)}")

    # Count by type
    people_count = sum(1 for o in owners_data if o["entity_type"] == "person")
    org_count = sum(1 for o in owners_data if o["entity_type"] == "organization")
    print(f"  - People: {people_count}")
    print(f"  - Organizations: {org_count}")

    # Adjacent parcel owners
    adjacent_owners = [
        o["name"] for o in owners_data if o["owns_adjacent_parcel"] == "Yes"
    ]
    print(f"\nOwners with Adjacent Parcels: {len(adjacent_owners)}")
    for owner in adjacent_owners[:5]:  # Show first 5
        print(f"  - {owner}")
    if len(adjacent_owners) > 5:
        print(f"  ... and {len(adjacent_owners) - 5} more")

    print(f"\n📁 Output Directory: {output_dir.absolute()}")
    print("\nThese files can be used as input to the neighbor pipeline for testing")
    print("without consuming additional Regrid API tokens.")

    return 0


if __name__ == "__main__":
    exit(main())
