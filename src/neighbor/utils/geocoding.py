# src/ii_agent/tools/neighbor/utils/geocoding.py
"""Azure Maps geocoding utilities for getting location details from coordinates."""

import os
import aiohttp
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode


async def reverse_geocode_azure(
    lat: float, lon: float, api_key: Optional[str] = None
) -> Dict[str, Optional[str]]:
    """
    Use Azure Maps to reverse geocode coordinates to get county and state.

    Args:
        lat: Latitude
        lon: Longitude
        api_key: Azure Maps API key (optional, will use env var if not provided)

    Returns:
        Dict with 'county', 'state', 'city', 'address' keys
    """
    if not api_key:
        api_key = os.getenv("AZURE_MAPS_API_KEY")

    if not api_key:
        print("⚠️  No Azure Maps API key found")
        return {"county": None, "state": None, "city": None, "address": None}

    # Azure Maps reverse geocoding endpoint (v2 API)
    # Note: Azure Maps expects coordinates in lon,lat order (not lat,lon)
    base_url = "https://atlas.microsoft.com/reverseGeocode"
    params = {
        "api-version": "2025-01-01",
        "subscription-key": api_key,
        "coordinates": f"{lon},{lat}",  # Note: longitude first!
    }

    try:
        async with aiohttp.ClientSession() as session:
            url = f"{base_url}?{urlencode(params)}"
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()

                    # Parse Azure Maps v2 response
                    if data.get("features") and len(data["features"]) > 0:
                        feature = data["features"][0]
                        properties = feature.get("properties", {})
                        address = properties.get("address", {})

                        # Extract admin districts (state and county)
                        admin_districts = address.get("adminDistricts", [])

                        # Typically: first element is state, second is county
                        state = None
                        county = None

                        if len(admin_districts) > 0:
                            # State is usually first
                            state = admin_districts[0].get(
                                "shortName"
                            ) or admin_districts[0].get("name")

                        if len(admin_districts) > 1:
                            # County is usually second
                            county_info = admin_districts[1].get("name", "")
                            # Clean up county name if needed
                            if county_info and not county_info.endswith(" County"):
                                county = (
                                    county_info
                                    if "County" in county_info
                                    else f"{county_info} County"
                                )
                            else:
                                county = county_info

                        # Get city and formatted address
                        locality = address.get("locality", "")
                        full_address = address.get("formattedAddress", "")

                        # Detect if area is unincorporated:
                        # If adminDistricts[1] is a County (not a City), the locality
                        # is just the postal city, not the actual municipality.
                        # In this case, leave city empty so we use county instead.
                        is_unincorporated = False
                        if len(admin_districts) > 1:
                            admin2_name = admin_districts[1].get("name", "")
                            # If second admin district is a county (not an independent city),
                            # the area is unincorporated
                            if "County" in admin2_name and "City" not in admin2_name:
                                is_unincorporated = True

                        # Only use locality as city if it's an incorporated area
                        city = "" if is_unincorporated else locality

                        result = {
                            "county": county or None,
                            "state": state or None,
                            "city": city or None,
                            "address": full_address or None,
                            "is_unincorporated": is_unincorporated,
                            "postal_city": locality or None,  # Always include postal city for reference
                        }

                        if is_unincorporated:
                            print(f"✓ Geocoded: {lat}, {lon} -> {county}, {state} (unincorporated, postal city: {locality})")
                        else:
                            print(f"✓ Geocoded: {lat}, {lon} -> {city}, {county}, {state}")
                        return result
                else:
                    error_text = await response.text()
                    print(f"⚠️  Azure Maps API error {response.status}: {error_text}")

    except Exception as e:
        print(f"⚠️  Error calling Azure Maps: {e}")

    return {"county": None, "state": None, "city": None, "address": None}


def parse_location_string(location: str) -> Tuple[float, float]:
    """
    Parse a location string like "39.7684,-86.1581" into lat, lon floats.

    Args:
        location: Comma-separated lat,lon string

    Returns:
        Tuple of (lat, lon) floats
    """
    try:
        parts = location.split(",")
        if len(parts) == 2:
            return float(parts[0].strip()), float(parts[1].strip())
    except (ValueError, AttributeError):
        pass

    raise ValueError(f"Invalid location format: {location}. Expected 'lat,lon'")
