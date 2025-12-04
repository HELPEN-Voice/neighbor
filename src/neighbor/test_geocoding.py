#!/usr/bin/env python3
"""Test script for Azure Maps geocoding functionality."""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from neighbor.utils.geocoding import reverse_geocode_azure, parse_location_string


async def test_geocoding():
    """Test the geocoding functionality with various coordinates."""

    print("=" * 60)
    print("AZURE MAPS GEOCODING TEST")
    print("=" * 60)

    # Check for API key
    api_key = os.getenv("AZURE_MAPS_API_KEY")
    if not api_key:
        print("❌ AZURE_MAPS_API_KEY not found in environment variables")
        return False

    print(f"✓ AZURE_MAPS_API_KEY found")
    print()

    # Test coordinates from different locations
    test_locations = [
        {
            "name": "Indianapolis, IN",
            "lat": 39.7684,
            "lon": -86.1581,
            "expected_state": "IN",
            "expected_county_contains": "Marion",
        },
        {
            "name": "Dallas, TX",
            "lat": 32.7767,
            "lon": -96.7970,
            "expected_state": "TX",
            "expected_county_contains": "Dallas",
        },
        {
            "name": "Nashville, TN",
            "lat": 36.1627,
            "lon": -86.7816,
            "expected_state": "TN",
            "expected_county_contains": "Davidson",
        },
        {
            "name": "Durham, NC",
            "lat": 35.9940,
            "lon": -78.8986,
            "expected_state": "NC",
            "expected_county_contains": "Durham",
        },
    ]

    all_passed = True

    for test in test_locations:
        print(f"\n{'=' * 50}")
        print(f"Testing: {test['name']}")
        print(f"Coordinates: {test['lat']}, {test['lon']}")
        print("-" * 50)

        try:
            # Test the geocoding
            result = await reverse_geocode_azure(test["lat"], test["lon"])

            # Display results
            print(f"Results:")
            print(f"  County: {result['county']}")
            print(f"  State: {result['state']}")
            print(f"  City: {result['city']}")
            print(f"  Address: {result['address']}")

            # Validate results
            passed = True
            if result["state"] != test["expected_state"]:
                print(
                    f"  ❌ State mismatch: expected {test['expected_state']}, got {result['state']}"
                )
                passed = False
            else:
                print(f"  ✓ State matches: {result['state']}")

            if (
                result["county"]
                and test["expected_county_contains"] not in result["county"]
            ):
                print(
                    f"  ❌ County mismatch: expected to contain '{test['expected_county_contains']}', got {result['county']}"
                )
                passed = False
            elif result["county"]:
                print(f"  ✓ County contains expected: {result['county']}")
            else:
                print(f"  ❌ No county returned")
                passed = False

            if not passed:
                all_passed = False

        except Exception as e:
            print(f"  ❌ Error: {e}")
            all_passed = False

    # Test parse_location_string function
    print(f"\n{'=' * 50}")
    print("Testing parse_location_string function")
    print("-" * 50)

    test_strings = [
        ("39.7684,-86.1581", (39.7684, -86.1581)),
        ("32.7767, -96.7970", (32.7767, -96.7970)),
        ("  36.1627  ,  -86.7816  ", (36.1627, -86.7816)),
    ]

    for test_str, expected in test_strings:
        try:
            lat, lon = parse_location_string(test_str)
            if (lat, lon) == expected:
                print(f"✓ '{test_str}' -> ({lat}, {lon})")
            else:
                print(f"❌ '{test_str}' -> expected {expected}, got ({lat}, {lon})")
                all_passed = False
        except Exception as e:
            print(f"❌ '{test_str}' -> Error: {e}")
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
    print("=" * 60)

    return all_passed


if __name__ == "__main__":
    success = asyncio.run(test_geocoding())
    sys.exit(0 if success else 1)
