#!/usr/bin/env python3
"""
Test script for the complete Neighbor Intelligence pipeline with LIVE Regrid data.
Flow: Regrid API -> JSON files -> Deep Research -> Results

Usage:
    python test_live_regrid.py --lat 44.8951 --lon -90.4420
    python test_live_regrid.py  # Uses default coordinates
"""

import asyncio
import argparse
import json
import os
import sys
import subprocess
import time
from pathlib import Path
from datetime import datetime
from pprint import pprint

# Load environment variables
from dotenv import load_dotenv

env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from neighbor import NeighborAgent
from neighbor.utils.geocoding import reverse_geocode_azure, parse_location_string


def start_ngrok_tunnel():
    """Start ngrok tunnel to AWS webhook server if configured"""
    from typing import Optional

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
    print(
        f"   Target: http://webhook-server-alb-1412407138.us-east-2.elb.amazonaws.com"
    )

    try:
        # Start ngrok in background
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

        # Wait a moment for it to start
        time.sleep(3)

        # Check if it's still running
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            print(f"❌ Failed to start ngrok: {stderr}")
            return None

        print(f"✅ Ngrok tunnel started successfully (PID: {process.pid})")

        # Test the tunnel
        import requests

        try:
            response = requests.get(f"https://{ngrok_domain}/", timeout=5)
            if response.status_code == 200:
                print(f"✅ Tunnel verified: webhook server is accessible")
            else:
                print(
                    f"⚠️  Tunnel may not be working correctly (status: {response.status_code})"
                )
        except Exception as e:
            print(f"⚠️  Could not verify tunnel: {e}")

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
        try:
            process.kill()
        except Exception:
            pass


def generate_pdf_report(result_data: dict) -> str:
    """Generate PDF report from neighbor screening results"""
    base = Path(__file__).resolve().parent  # .../tools/neighbor

    # Run convert_html_to_pdf.py which now handles both conversion and combination
    conv = base / "convert_html_to_pdf.py"
    result = subprocess.run(
        [sys.executable, str(conv)], cwd=str(base), capture_output=True, text=True
    )

    if result.returncode != 0:
        print(f"⚠ PDF conversion error: {result.stderr}")
        raise Exception(f"PDF conversion failed: {result.stderr}")

    # Print the output from convert_html_to_pdf.py
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            print(f"    {line}")

    # Check if combined report was created
    combined = base / "combined_pdf_reports" / "neighbor_report.pdf"
    if combined.exists():
        return str(combined)

    # Fallback to individual PDFs directory
    pdf_dir = base / "individual_pdf_reports"
    return str(pdf_dir)


async def test_live_pipeline(lat, lon):
    """Test the complete pipeline with live Regrid data.

    Args:
        lat: Latitude (required)
        lon: Longitude (required)
    """
    print("=" * 60)
    print("LIVE REGRID -> DEEP RESEARCH PIPELINE TEST")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Clean up old outputs
    print("🧹 Cleaning up old outputs...")
    output_dirs = [
        Path(__file__).parent / "neighbor_outputs",
        Path(__file__).parent / "neighbor_html_outputs",
        Path(__file__).parent / "individual_pdf_reports",
        Path(__file__).parent / "combined_pdf_reports",
        Path(__file__).parent / "deep_research_outputs",
    ]

    for output_dir in output_dirs:
        if output_dir.exists():
            # Remove all JSON, HTML, PDF, TXT, and MD files
            for pattern in ["*.json", "*.html", "*.pdf", "*.txt", "*.md"]:
                for file in output_dir.glob(pattern):
                    file.unlink()
                    print(f"  ✓ Deleted {file.name}")

    # Check for API keys
    regrid_key = os.getenv("REGRID_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if not regrid_key:
        print("❌ REGRID_API_KEY not found in environment variables")
        return False
    if not openai_key:
        print("❌ OPENAI_API_KEY not found in environment variables")
        return False

    print(f"✓ REGRID_API_KEY found")
    print(f"✓ OPENAI_API_KEY found")

    # Use the provided coordinates
    print(f"\n{'=' * 60}")
    print(f"Testing location: {lat}, {lon}")
    print(f"  Radius: 0.5 miles")
    print(f"{'=' * 60}")

    try:
        # Initialize the agent
        agent = NeighborAgent()

        # Event handler for progress updates
        def on_event(event):
            if event["type"] == "start":
                print(
                    f"  🔄 Starting batch: {event['batch_size']} {event['entity_type']}s"
                )
            elif event["type"] == "complete":
                print(
                    f"  ✓ Completed batch: {event['batch_size']} {event['entity_type']}s"
                )
            elif event["type"] == "error":
                print(f"  ❌ Error: {event['message']}")

        # Geocode coordinates if county/state not provided
        location_str = f"{lat},{lon}"
        county = None
        state = None
        city = None

        if not county or not state:
            print(f"\n🗺️  Geocoding coordinates to get location details...")
            geo_result = await reverse_geocode_azure(lat, lon)
            if geo_result["county"]:
                county = geo_result["county"]
                print(f"   County: {county}")
            if geo_result["state"]:
                state = geo_result["state"]
                print(f"   State: {state}")
            if geo_result["city"]:
                city = geo_result["city"]
                print(f"   City: {city}")

        # Run the complete pipeline
        print("\n📍 Step 1: Fetching neighbors from Regrid API...")

        result = await agent.screen(
            location=location_str,
            radius_mi=0.5,
            county=county,
            state=state,
            city=city,
            on_event=on_event,
            save_regrid_json=True,  # This saves the Regrid data to JSON
        )

        # Check what was saved
        output_dir = Path(__file__).parent / "neighbor_outputs"
        people_file = output_dir / "regrid_people.json"
        orgs_file = output_dir / "regrid_organizations.json"
        all_file = output_dir / "regrid_all.json"

        print("\n📁 Step 2: JSON files saved:")
        if people_file.exists():
            with open(people_file) as f:
                people_data = json.load(f)
            print(
                f"  ✓ People: {len(people_data['neighbors'])} entries in {people_file.name}"
            )
            # Show first few
            for person in people_data["neighbors"][:3]:
                pins = person.get("pins", [])
                if pins:
                    pins_display = (
                        f"PINs: {', '.join(pins[:2])}{'...' if len(pins) > 2 else ''}"
                    )
                else:
                    pins_display = "No PINs"
                print(f"    - {person['name']} ({pins_display})")

        if orgs_file.exists():
            with open(orgs_file) as f:
                orgs_data = json.load(f)
            print(
                f"  ✓ Organizations: {len(orgs_data['neighbors'])} entries in {orgs_file.name}"
            )
            # Show first few
            for org in orgs_data["neighbors"][:3]:
                print(f"    - {org['name']} (PINs: {len(org.get('pins', []))})")

        print(f"\n🔬 Step 3: Deep Research results:")
        if result.get("success"):
            neighbors = result.get("neighbors", [])
            print(f"  ✓ Researched {len(neighbors)} total neighbors")

            # Count by category (not entity_type which is now more specific)
            persons = sum(
                1 for n in neighbors if n.get("entity_category") == "Resident"
            )
            orgs = sum(
                1 for n in neighbors if n.get("entity_category") == "Organization"
            )
            print(f"    - Residents: {persons}")
            print(f"    - Organizations: {orgs}")

            # Show overview
            if result.get("overview_summary"):
                print(f"\n  Overview: {result['overview_summary'][:200]}...")

            # Show first few results with new fields
            print("\n  Sample results:")
            for i, neighbor in enumerate(neighbors[:3], 1):
                print(
                    f"\n  {i}. {neighbor.get('name')} ({neighbor.get('entity_type', 'unknown')})"
                )
                print(
                    f"     Community Influence: {neighbor.get('community_influence', 'Unknown')}"
                )
                print(
                    f"     Stance: {neighbor.get('noted_stance', 'No documented stance')}"
                )

                # For organizations, show entity classification
                if neighbor.get("entity_category") == "Organization":
                    print(
                        f"     Classification: {neighbor.get('entity_classification', 'unknown')}"
                    )

                # Show motivators if present
                motivators = neighbor.get("potential_motivators", [])
                if motivators:
                    print(f"     Motivators: {', '.join(motivators[:2])}")

                # Show approach recommendations
                approach = neighbor.get("approach_recommendations", "")
                if approach:
                    if isinstance(approach, dict):
                        motivations = approach.get("motivations", [])
                        engage = approach.get("engage", "")
                        motivations_text = (
                            ", ".join(motivations) if motivations else "None specified"
                        )
                        print(f"     Approach Motivations: {motivations_text}")
                        print(
                            f"     Approach Engagement: {engage[:100]}..."
                            if len(engage) > 100
                            else f"     Approach Engagement: {engage}"
                        )
                    else:
                        # Handle legacy string format
                        print(
                            f"     Approach: {approach[:100]}..."
                            if len(approach) > 100
                            else f"     Approach: {approach}"
                        )

            # Generate HTML reports using standalone script
            print(f"\n📄 Step 4: Generating HTML reports...")
            try:
                # Run the standalone conversion script
                conv_script = Path(__file__).parent / "convert_neighbor_to_html.py"
                html_result = subprocess.run(
                    [sys.executable, str(conv_script)],
                    cwd=str(Path(__file__).parent),
                    capture_output=True,
                    text=True,
                )

                if html_result.returncode == 0:
                    # Parse output to show what was generated
                    if "✓ Generated" in html_result.stdout:
                        for line in html_result.stdout.split("\n"):
                            if line.strip() and ("✓" in line or ".html" in line):
                                print(f"  {line.strip()}")
                else:
                    print(f"  ⚠ HTML generation failed: {html_result.stderr}")

                # Generate PDF reports
                print(f"\n📑 Step 5: Generating PDF reports...")
                try:
                    pdf_path = generate_pdf_report(result)
                    print(f"  ✓ Generated PDF report: {Path(pdf_path).name}")
                    print(f"    Full path: {pdf_path}")
                except Exception as e:
                    print(f"  ⚠ PDF generation failed: {e}")
                    import traceback

                    traceback.print_exc()

            except Exception as e:
                print(f"  ⚠ HTML generation skipped: {e}")

        else:
            print(f"  ❌ Research failed")

    except Exception as e:
        print(f"  ❌ Pipeline error: {e}")
        import traceback

        traceback.print_exc()
        return False

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("Check ./neighbor_outputs/ for JSON files")
    print("Check ./neighbor_html_outputs/ for HTML reports")
    print("Check ./individual_pdf_reports/ and ./combined_pdf_reports/ for PDF reports")
    print(f"Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    return True


if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Test Neighbor Intelligence pipeline with Regrid data"
    )
    parser.add_argument("--lat", type=float, required=True, help="Latitude coordinate")
    parser.add_argument("--lon", type=float, required=True, help="Longitude coordinate")
    args = parser.parse_args()

    # Start ngrok tunnel to webhook server
    ngrok_process = start_ngrok_tunnel()

    try:
        # Run the test with provided coordinates
        success = asyncio.run(test_live_pipeline(args.lat, args.lon))
    finally:
        # Always stop ngrok tunnel
        stop_ngrok_tunnel(ngrok_process)

    sys.exit(0 if success else 1)
