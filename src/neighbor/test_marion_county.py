#!/usr/bin/env python
import sys
from pathlib import Path
# Add the project root to sys.path
project_root = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables from .env
from dotenv import load_dotenv
load_dotenv(project_root / '.env')

import asyncio
from neighbor.agents.neighbor_finder import NeighborFinder
import json
from collections import Counter


async def test_marion():
    finder = NeighborFinder()

    # Get target parcel - trying Fillmore County, Nebraska
    # Geneva, Nebraska area (county seat)
    print('Getting target parcel...')
    target = await finder.get_target_parcel(
        search_mode='COORDS',
        lat=40.5267,
        lon=-97.5956
    )

    if target:
        print(f'Target PIN: {target["pin"]}')
        print(f'County Path: {target["county_path"]}')

        # Get adjacent parcels
        adjacent = await finder.get_adjacent_parcels(
            target_geometry=target['geometry'],
            target_pin=target['pin']
        )
        print(f'Adjacent parcels: {len(adjacent)}')

        # Find neighbors with radius expansion
        neighbors = await finder.find_by_location_with_expansion(
            lat=40.5267,
            lon=-97.5956,
            initial_radius_mi=0.5,
            target_count=30,
            adjacent_pins=adjacent
        )

        print(f'\nFound {len(neighbors)} neighbors')

        # Check for duplicates
        names = [n['name'] for n in neighbors]
        unique_names = set(names)
        print(f'Unique names: {len(unique_names)}')

        if len(names) != len(unique_names):
            print('\nDuplicates found:')
            counts = Counter(names)
            for name, count in counts.items():
                if count > 1:
                    print(f'  - {name}: {count} times')

        # Save to JSON in neighbor directory
        output_file = Path('fillmore_county_test.json')

        with open(output_file, 'w') as f:
            json.dump({
                'location': 'Fillmore County, Nebraska',
                'coordinates': {'lat': 40.5267, 'lon': -97.5956},
                'total': len(neighbors),
                'unique_count': len(unique_names),
                'neighbors': neighbors
            }, f, indent=2)

        print(f'\nSaved to {output_file}')

        # Show first few neighbors
        print('\nFirst 5 neighbors:')
        for n in neighbors[:5]:
            print(f'  - {n["name"]} ({n["entity_type"]}): {len(n["pins"])} parcels, Adjacent: {n["owns_adjacent_parcel"]}')


if __name__ == "__main__":
    asyncio.run(test_marion())