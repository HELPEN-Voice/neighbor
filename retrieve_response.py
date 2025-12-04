#!/usr/bin/env python3
"""
Retrieve a completed OpenAI response and save it in the correct format.
"""

import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

# Load environment variables
from dotenv import load_dotenv

env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

# Add to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from neighbor.webhook_manager import webhook_manager


async def retrieve_and_save(response_id: str, output_file: str):
    """Retrieve a response and save it."""
    print(f"Retrieving response: {response_id}")

    result = await webhook_manager.retrieve_response(response_id)

    print(f"\nResponse status: {result.get('status')}")
    print(f"Has raw_output: {'raw_output' in result}")
    print(f"Has citations: {'citations' in result}")
    print(f"Has research_steps: {'research_steps' in result}")

    if result.get("status") == "completed":
        # Read the existing file to get metadata
        with open(output_file, "r") as f:
            existing = json.load(f)

        # Update with the new data
        existing["raw_text"] = result.get("raw_output", "")
        existing["annotations"] = result.get("citations", [])
        existing["research_steps"] = result.get("research_steps", [])
        existing["status"] = "completed"
        existing["retrieved_at"] = datetime.now().isoformat()

        # Try to parse the JSON from raw_output
        try:
            # Look for JSON blocks in the output
            from neighbor.utils.json_parse import extract_fenced_blocks

            parsed, markdown = extract_fenced_blocks(existing["raw_text"])

            existing["overview_summary"] = parsed.get("overview_summary")
            existing["neighbors"] = parsed.get("neighbors", [])
            existing["markdown_debug"] = markdown
            print(f"\n✅ Parsed JSON: {len(existing['neighbors'])} neighbors")
        except Exception as e:
            print(f"\n⚠️ Could not parse JSON from output: {e}")

        # Save the updated file
        with open(output_file, "w") as f:
            json.dump(existing, f, indent=2)

        print(f"\n✅ Saved updated response to: {output_file}")
        print(f"Raw output length: {len(existing['raw_text'])} chars")
        print(f"Citations: {len(existing['annotations'])}")

        return existing
    else:
        print(f"\n❌ Response not completed yet. Status: {result.get('status')}")
        print(f"Error: {result.get('error', 'N/A')}")
        return None


if __name__ == "__main__":
    response_id = "resp_0906c4c24718ce0000690d3985d2d08197b6297533426d9845"
    output_file = "/home/falcao/neighbor/src/neighbor/deep_research_outputs/dr_organizations_20251106_164255_4179.json"

    result = asyncio.run(retrieve_and_save(response_id, output_file))

    if result:
        print("\n✅ Response retrieved and saved successfully!")
    else:
        print("\n❌ Failed to retrieve response")
        sys.exit(1)
