#!/usr/bin/env python3
"""
Process the 4 missing neighbors in 2 smaller API calls, then combine into one batch file.
This avoids the timeout by making 2 calls of 2 people each, but saves as one batch_persons_4-5.json.

Usage:
    cd ~/neighbor && source .venv/bin/activate && python src/neighbor/run_missing_batch.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Load environment variables
from dotenv import load_dotenv

env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from neighbor.engines.responses_engine import DeepResearchResponsesEngine
from neighbor.webhook_manager import webhook_manager
import subprocess
import time


# The 4 missing people with their PINs from regrid_all.json
# Split into 2 groups for smaller API calls
BATCH_A = [
    {"name": "Lee Warren M", "pins": ["025-00-00-011-000"], "owns_adjacent_parcel": "No"},
    {"name": "Smith Lewis B & James E Smith", "pins": ["025-00-00-022-000"], "owns_adjacent_parcel": "No"},
]

BATCH_B = [
    {"name": "Watson Douglas Jr", "pins": ["025-00-00-153-000"], "owns_adjacent_parcel": "No"},
    {"name": "Robinson Francine", "pins": ["025-00-00-066-000"], "owns_adjacent_parcel": "No"},
]

# Context from the current run
CONTEXT = {
    "county": "Lee County",
    "state": "SC",
    "city": None,
    "radius_mi": 0.5,
}

OUTPUT_DIR = Path(__file__).parent / "neighbor_outputs"
DR_OUTPUT_DIR = Path(__file__).parent / "deep_research_outputs"


def start_ngrok_tunnel():
    """Start ngrok tunnel to AWS webhook server if configured"""
    ngrok_domain = os.getenv("NGROK_DOMAIN", "").strip('"')
    webhook_url = os.getenv("OPENAI_WEBHOOK_URL", "").strip('"')

    if not ngrok_domain:
        print("⚠️  No NGROK_DOMAIN configured, skipping tunnel setup")
        return None

    if "ngrok" not in webhook_url:
        print("⚠️  Webhook URL doesn't use ngrok, skipping tunnel setup")
        return None

    # Check if ngrok is already running
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"ngrok.*{ngrok_domain}"], capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"✅ Ngrok tunnel already running for {ngrok_domain}")
            return None
    except Exception:
        pass

    print(f"🚀 Starting ngrok tunnel to AWS webhook server...")
    print(f"   Domain: {ngrok_domain}")

    try:
        process = subprocess.Popen(
            [
                "ngrok",
                "http",
                "--domain",
                ngrok_domain,
                "http://webhook-server-alb-1412407138.us-east-2.elb.amazonaws.com",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(3)

        if process.poll() is not None:
            stdout, stderr = process.communicate()
            print(f"❌ Failed to start ngrok: {stderr}")
            return None

        print(f"✅ Ngrok tunnel started successfully (PID: {process.pid})")
        return process

    except Exception as e:
        print(f"❌ Failed to start ngrok: {e}")
        return None


def stop_ngrok_tunnel(process):
    """Stop the ngrok tunnel if we started it"""
    if process is None:
        return
    try:
        print(f"🛑 Stopping ngrok tunnel (PID: {process.pid})...")
        process.terminate()
        process.wait(timeout=5)
        print("✅ Ngrok tunnel stopped")
    except Exception as e:
        print(f"⚠️  Error stopping ngrok: {e}")


async def process_batch(engine, batch, batch_name, context):
    """Process a single batch via deep research."""
    print(f"\n{'='*60}")
    print(f"Processing {batch_name}: {[p['name'] for p in batch]}")
    print(f"{'='*60}")

    try:
        result = await engine.run_batch(
            names=batch,
            context=context,
            entity_type="person",
            on_event=lambda e: print(f"  [{e['type']}] {e.get('message', '')}")
        )

        if result and result.get("neighbors"):
            print(f"  ✅ Got {len(result['neighbors'])} profiles")
            print(f"  📄 DR file: {result.get('saved_filepath', 'N/A')}")
            return result
        else:
            print(f"  ❌ No results for {batch_name}")
            return None

    except Exception as e:
        print(f"  ❌ Error processing {batch_name}: {e}")
        import traceback
        traceback.print_exc()
        return None


def combine_and_save_batch(result_a, result_b):
    """Combine two results into one batch_persons_4-5.json file."""

    # Combine neighbors from both results
    all_neighbors = []
    all_neighbors.extend(result_a.get("neighbors", []))
    all_neighbors.extend(result_b.get("neighbors", []))

    # Combine annotations
    all_annotations = []
    all_annotations.extend(result_a.get("annotations", []))
    all_annotations.extend(result_b.get("annotations", []))

    # Combine overview summaries
    overview_a = result_a.get("overview_summary", "")
    overview_b = result_b.get("overview_summary", "")
    combined_overview = f"{overview_a} {overview_b}".strip() if overview_a or overview_b else None

    # Get saved filepaths (we'll use the first one for the batch file, but track both)
    saved_filepath_a = result_a.get("saved_filepath")
    saved_filepath_b = result_b.get("saved_filepath")

    # Create combined dr_* file with all 4 neighbors
    DR_OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:20]
    combined_dr_filename = f"dr_persons_{timestamp}.json"
    combined_dr_path = DR_OUTPUT_DIR / combined_dr_filename

    combined_dr_data = {
        "timestamp": datetime.now().isoformat(),
        "entity_type": "person",
        "batch_size": len(all_neighbors),
        "location_context": CONTEXT,
        "overview_summary": combined_overview,
        "neighbors": all_neighbors,
        "annotations": all_annotations,
        "source_files": [saved_filepath_a, saved_filepath_b],
    }

    with open(combined_dr_path, "w") as f:
        json.dump(combined_dr_data, f, indent=2)

    print(f"\n📄 Created combined DR file: {combined_dr_filename}")

    # Create batch file pointing to combined dr_* file
    batch_data = {
        "batch_idx": 3,  # 0-indexed, so batch 4 = index 3
        "total_batches": 5,
        "entity_type": "person",
        "neighbors": all_neighbors,
        "annotations": all_annotations,
        "overview_summary": combined_overview,
        "saved_filepath": str(combined_dr_path),
        "cached_at": datetime.now().isoformat(),
    }

    batch_filepath = OUTPUT_DIR / "batch_persons_4-5.json"
    with open(batch_filepath, "w") as f:
        json.dump(batch_data, f, indent=2)

    print(f"📦 Created batch file: batch_persons_4-5.json with {len(all_neighbors)} neighbors")

    return batch_filepath


async def main():
    print("=" * 60)
    print("PROCESSING 4 MISSING NEIGHBORS")
    print("Making 2 API calls of 2 people each to avoid timeout")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Check for API key
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY not found")
        return False

    # Check that batch 4-5 doesn't already exist
    batch_4_path = OUTPUT_DIR / "batch_persons_4-5.json"
    if batch_4_path.exists():
        print(f"⚠️  {batch_4_path} already exists!")
        response = input("Delete and recreate? (y/n): ").strip().lower()
        if response != 'y':
            print("Aborted.")
            return False
        batch_4_path.unlink()
        print("  Deleted existing file.")

    # Initialize engine
    engine = DeepResearchResponsesEngine()

    # Process first batch (2 people)
    result_a = await process_batch(engine, BATCH_A, "Batch A (Lee Warren M, Smith Lewis B)", CONTEXT)
    if not result_a:
        print("❌ Batch A failed")
        return False

    # Small delay between batches
    print("\n⏳ Waiting 10 seconds before next batch...")
    await asyncio.sleep(10)

    # Process second batch (2 people)
    result_b = await process_batch(engine, BATCH_B, "Batch B (Watson Douglas Jr, Robinson Francine)", CONTEXT)
    if not result_b:
        print("❌ Batch B failed")
        return False

    # Combine results into one batch file
    print("\n" + "=" * 60)
    print("COMBINING RESULTS")
    print("=" * 60)

    batch_filepath = combine_and_save_batch(result_a, result_b)

    # Verify all batch files exist
    print("\n" + "=" * 60)
    print("VERIFICATION - All person batches")
    print("=" * 60)

    expected = [f"batch_persons_{i}-5.json" for i in range(1, 6)]
    all_exist = True
    total_neighbors = 0

    for fname in expected:
        path = OUTPUT_DIR / fname
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            count = len(data.get('neighbors', []))
            total_neighbors += count
            print(f"  ✓ {fname}: {count} neighbors")
        else:
            print(f"  ✗ {fname}: MISSING")
            all_exist = False

    print(f"\n  Total: {total_neighbors} neighbors across 5 batches")

    print("\n" + "=" * 60)
    if all_exist:
        print("SUCCESS - All batches complete!")
        print(f"Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("\nNext step: Run the main pipeline with --no-clean to verify and merge:")
        print("  cd ~/neighbor && source .venv/bin/activate && python src/neighbor/run_live_regrid.py --lat 34.247731 --lon -80.157798 --no-clean")
    else:
        print("INCOMPLETE - Some batches missing")
    print("=" * 60)

    return all_exist


if __name__ == "__main__":
    # Start ngrok tunnel
    ngrok_process = start_ngrok_tunnel()

    try:
        success = asyncio.run(main())
    finally:
        stop_ngrok_tunnel(ngrok_process)

    sys.exit(0 if success else 1)
