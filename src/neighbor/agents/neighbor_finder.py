# src/ii_agent/tools/neighbor/agents/neighbor_finder.py
import aiohttp
import os
import json
from typing import List, Dict, Any, Optional, Tuple
from ..utils.entity import guess_entity_type
from ..config.settings import settings


class NeighborFinder:
    def __init__(self):
        self.api_key = settings.REGRID_API_KEY or os.getenv("REGRID_API_KEY")
        self.base_url = "https://app.regrid.com/api/v2"
        self.target_parcel_info = None  # Store target parcel info
        self.raw_parcels = []  # Store raw parcel features for valuation service
        self.final_radius_miles = None  # Store final search radius used

    async def get_target_parcel(
        self,
        search_mode: str = "COORDS",
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        pin: Optional[str] = None,
        county_path: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Identifies a single target parcel by either coordinates or PIN.
        Returns dict with pin, geometry, lat, lon, county_path.
        """
        if not self.api_key:
            raise ValueError("REGRID_API_KEY not found in environment variables")

        print(f"Identifying target parcel using mode: {search_mode}...")

        try:
            async with aiohttp.ClientSession() as session:
                if search_mode == "COORDS":
                    url = f"{self.base_url}/parcels/point"
                    params = {"token": self.api_key, "lat": lat, "lon": lon, "limit": 1}
                    async with session.get(url, params=params) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            print(f"Error finding target parcel: {error_text}")
                            return None
                        data = await response.json()

                elif search_mode == "PIN":
                    url = f"{self.base_url}/parcels/apn"
                    params = {"token": self.api_key, "parcelnumb": pin}
                    if county_path:
                        params["path"] = county_path
                    async with session.get(url, params=params) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            print(f"Error finding target parcel: {error_text}")
                            return None
                        data = await response.json()
                else:
                    print("Invalid search mode.")
                    return None

                features = data.get("parcels", {}).get("features", [])
                if not features:
                    print("Target parcel could not be found with the provided input.")
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
                }

                # Store target parcel info for later use
                self.target_parcel_info = target_info

                if not all(
                    [
                        target_info["pin"],
                        target_info["geometry"],
                        target_info["lat"],
                        target_info["lon"],
                    ]
                ):
                    print("Found parcel but it is missing critical information.")
                    return None

                print(
                    f"Successfully identified target parcel. PIN: {target_info['pin']}"
                )
                return target_info

        except Exception as e:
            print(f"Error finding target parcel: {e}")
            return None

    async def get_adjacent_parcels(
        self, target_geometry: Dict[str, Any], target_pin: str
    ) -> set:
        """
        Finds PINs for all parcels adjacent to the target geometry.
        """
        if not self.api_key:
            raise ValueError("REGRID_API_KEY not found in environment variables")

        print(f"Finding neighbors for target PIN: {target_pin}...")

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/parcels/area"
                # Use POST to avoid 414 Request-URI Too Large with complex geometries
                payload = {"geojson": target_geometry}
                headers = {"Content-Type": "application/json"}

                async with session.post(
                    url, params={"token": self.api_key}, json=payload, headers=headers
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"Error finding adjacent parcels: {error_text}")
                        return set()

                    data = await response.json()
                    features = data.get("parcels", {}).get("features", [])

                    adjacent_pins = set()
                    for parcel in features:
                        pin = (
                            parcel.get("properties", {})
                            .get("fields", {})
                            .get("parcelnumb")
                        )
                        if pin and pin != target_pin:
                            adjacent_pins.add(pin)

                    print(f"Found {len(adjacent_pins)} adjacent parcels.")
                    return adjacent_pins

        except Exception as e:
            print(f"Error finding adjacent parcels: {e}")
            return set()

    async def find_by_location_with_expansion(
        self,
        lat: float,
        lon: float,
        initial_radius_mi: float = 0.5,  # Kept for backward compatibility (ignored)
        target_count: int = 30,
        adjacent_pins: Optional[set] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query Regrid API for parcels using expanding radii to ensure nearest parcels first.

        Searches at fixed radii of 0.5, 1.0, and 1.5 miles until MAX_PARCELS (30) unique
        parcels are accumulated. This guarantees we get the truly nearest parcels while
        capping total parcel records for billing optimization.

        Also adds owns_adjacent_parcel flag to owners.
        Returns: [{ "name": "Karen Newman", "entity_type": "person", "pins": ["12-34-56"], "owns_adjacent_parcel": "No" }, ...]

        Note: target_count caps the number of owners returned (default 30).
        """
        if not self.api_key:
            raise ValueError("REGRID_API_KEY not found in environment variables")

        max_parcels = settings.MAX_PARCELS  # Hard cap on parcels (billing optimization)
        search_radii_mi = [0.5, 1.0, 1.5]  # Fixed search radii in miles

        url = f"{self.base_url}/parcels/point"

        # Track unique parcels across expansions (by PIN)
        all_parcels = {}  # pin -> parcel feature
        radius_mi = search_radii_mi[0]  # Track current radius for logging

        print(f"\nFetching up to {max_parcels} nearest parcels using expanding radii...")

        async with aiohttp.ClientSession() as session:
            for radius_mi in search_radii_mi:
                if len(all_parcels) >= max_parcels:
                    break
                radius_meters = radius_mi * 1609.34
                # Request enough to potentially fill remaining quota
                remaining = max_parcels - len(all_parcels)
                request_limit = min(remaining + 20, 100)  # Small buffer for deduplication

                params = {
                    "lat": lat,
                    "lon": lon,
                    "radius": int(radius_meters),
                    "token": self.api_key,
                    "limit": request_limit,
                }

                print(f"  Searching {radius_mi:.2f} mi radius ({int(radius_meters)}m)...")

                try:
                    async with session.get(url, params=params) as response:
                        if response.status == 200:
                            data = await response.json()
                            parcels = data.get("parcels", {}).get("features", [])

                            if not parcels:
                                print(f"    No parcels found at this radius")
                                continue

                            # Add new unique parcels (by PIN)
                            new_count = 0
                            for parcel in parcels:
                                if len(all_parcels) >= max_parcels:
                                    break
                                pin = (
                                    parcel.get("properties", {})
                                    .get("fields", {})
                                    .get("parcelnumb")
                                )
                                if pin and pin not in all_parcels:
                                    all_parcels[pin] = parcel
                                    new_count += 1

                            print(f"    Found {new_count} new parcels (total: {len(all_parcels)})")

                            if len(all_parcels) >= max_parcels:
                                print(f"  Reached {max_parcels} parcel limit")
                                break
                        else:
                            error_text = await response.text()
                            print(f"Regrid API error: {error_text[:500]}")
                            break

                except Exception as e:
                    print(f"Error fetching from Regrid: {e}")
                    break

        if not all_parcels:
            print("No parcels found within maximum search radius.")
            self.raw_parcels = []
            return []

        print(f"Accumulated {len(all_parcels)} unique parcels at final radius {radius_mi:.2f} mi")

        # Store raw parcels for valuation service access
        parcel_features = list(all_parcels.values())
        self.raw_parcels = parcel_features
        self.final_radius_miles = radius_mi  # Store final radius used

        # Process all accumulated parcels to extract unique owners
        owners = self._process_parcels(parcel_features, adjacent_pins)

        # Convert to list and cap at target_count owners
        result = list(owners.values())
        if len(result) > target_count:
            result = result[:target_count]

        print(f"Extracted {len(result)} distinct owners from {len(all_parcels)} parcels")
        return result

    async def find_by_location(
        self, lat: float, lon: float, radius_mi: float
    ) -> List[Dict[str, Any]]:
        """
        Legacy method for backward compatibility.
        Query Regrid API for parcels within radius and return unique owner records.
        """
        # Use the new method with expansion, but with the original radius and no target count limit
        return await self.find_by_location_with_expansion(
            lat=lat,
            lon=lon,
            initial_radius_mi=radius_mi,
            target_count=settings.MAX_NEIGHBORS,
            adjacent_pins=None,
        )

    def _get_name_key(self, name: str) -> str:
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

    def _choose_most_complete_name(self, name1: str, name2: str) -> str:
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

    def _process_parcels(
        self, features: List[Dict], adjacent_pins: Optional[set] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Process GeoJSON features from Regrid and deduplicate by owner.
        Groups multiple PINs under the same owner.
        Adds owns_adjacent_parcel flag if adjacent_pins provided.
        Returns dict instead of list for easier merging.
        """
        owner_map = {}  # key: name_key, value: owner_data
        name_variations = {}  # key: name_key, value: set of original names

        for feature in features:
            if feature.get("type") != "Feature":
                continue

            properties = feature.get("properties", {})
            fields = properties.get("fields", {})
            enhanced = properties.get("enhanced_ownership", [])

            # Extract owner name - try enhanced ownership first, then regular fields
            owner_name = self._extract_owner_name(fields, enhanced)
            if not owner_name or owner_name.lower() in [
                "unknown",
                "unavailable",
                "null",
            ]:
                continue

            # Clean and title case
            owner_name = owner_name.strip().title()

            # Get a normalized key for comparison
            name_key = self._get_name_key(owner_name)

            # Extract PIN/parcel ID
            pin = (
                fields.get("parcelnumb")
                or fields.get("parcelnumb_no_formatting")
                or fields.get("ll_uuid")
                or ""
            )

            # Track name variations for debugging
            if name_key not in name_variations:
                name_variations[name_key] = set()
            name_variations[name_key].add(owner_name)

            # Deduplicate and group PINs by name key
            if name_key in owner_map:
                # Owner already exists - update with most complete name
                existing_name = owner_map[name_key]["name"]
                most_complete_name = self._choose_most_complete_name(
                    existing_name, owner_name
                )
                owner_map[name_key]["name"] = most_complete_name

                # Add PIN to existing owner
                if pin and pin not in owner_map[name_key]["pins"]:
                    owner_map[name_key]["pins"].append(pin)
                    # Check if this PIN is adjacent to target
                    if adjacent_pins and pin in adjacent_pins:
                        owner_map[name_key]["owns_adjacent_parcel"] = "Yes"
            else:
                # Create new owner entry
                owner_data = {
                    "name": owner_name,  # Use the actual name (will be updated if more complete version found)
                    "entity_type": guess_entity_type(owner_name),
                    "pins": [pin] if pin else [],
                    "owns_adjacent_parcel": "No",  # Default to No
                }

                # Check if this PIN is adjacent to target
                if adjacent_pins and pin and pin in adjacent_pins:
                    owner_data["owns_adjacent_parcel"] = "Yes"

                owner_map[name_key] = owner_data

        # Log any names that were merged (optional - for debugging)
        for name_key, variations in name_variations.items():
            if len(variations) > 1:
                final_name = owner_map[name_key]["name"]
                print(f"  Merged name variations into '{final_name}': {variations}")

        return owner_map

    def _extract_owner_name(self, fields: Dict, enhanced: List) -> Optional[str]:
        """Extract owner name from fields or enhanced ownership data."""
        # Try enhanced ownership first (more accurate)
        if enhanced and len(enhanced) > 0:
            eo = enhanced[0]  # Primary owner record
            # Try full name fields
            if eo.get("eo_owner"):
                return str(eo["eo_owner"])
            # Try combining first/last
            if eo.get("eo_ownerfirst") and eo.get("eo_ownerlast"):
                return f"{eo['eo_ownerfirst']} {eo['eo_ownerlast']}"

        # Fallback to regular fields
        owner_fields = ["owner", "owner1", "ownername", "ownname1", "owner_name"]

        for field in owner_fields:
            if fields.get(field):
                value = str(fields[field]).strip()
                if value and value.lower() not in ["null", "none", ""]:
                    return value

        return None
