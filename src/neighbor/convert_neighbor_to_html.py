import re
import json
from pathlib import Path
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, select_autoescape

BASE = Path(__file__).resolve().parent
TPL = BASE / "templates"
OUT = BASE / "neighbor_html_outputs"


def _env():
    env = Environment(
        loader=FileSystemLoader(str(TPL)), autoescape=select_autoescape(["html"])
    )
    # Add PIN normalization and citation formatting as Jinja2 filters
    env.filters["normalize_pin"] = normalize_pin
    env.filters["format_citations"] = format_citations
    return env


def _list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return list(v)


def normalize_pin(pin):
    """Normalize PINs by removing zero-width characters and using non-breaking hyphens"""
    if not pin:
        return pin
    return (
        str(pin)
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
        .replace("\u2060", "")
        .replace("-", "\u2011")
    )


def abbreviate_confidence(confidence):
    """Convert confidence levels to single letter abbreviations for compact display"""
    if not confidence:
        return ""
    confidence_lower = str(confidence).lower().strip()
    if confidence_lower in ["high", "h"]:
        return "H"
    elif confidence_lower in ["medium", "m"]:
        return "M"
    elif confidence_lower in ["low", "l"]:
        return "L"
    else:
        return confidence  # Return original if not recognized


def format_citations(text):
    """Convert contentReference citations to clickable circles and markdown links to HTML"""
    if not text:
        return text

    # First, convert markdown links [text](url) to HTML <a> tags
    # Pattern to match markdown links [link text](url)
    markdown_link_pattern = r"\[([^\]]+)\]\(([^)]+)\)"

    def replace_markdown_link(match):
        link_text = match.group(1)
        url = match.group(2)
        # Keep the full URL including fragment identifiers for text highlighting
        return f'<a href="{url}" target="_blank">{link_text}</a>'

    formatted_text = re.sub(markdown_link_pattern, replace_markdown_link, text)

    # Then handle contentReference citations
    # Pattern to match contentReference[oaicite:N]{index=N} citations
    citation_pattern = r":contentReference\[oaicite:(\d+)\]\{index=(\d+)\}"

    def replace_citation(match):
        index = match.group(2)
        return f'<span class="citation-circle">{index}</span>'

    # Replace all contentReference citations with circles
    formatted_text = re.sub(citation_pattern, replace_citation, formatted_text)

    return formatted_text


def load_deep_research_files(filepaths):
    """Load and combine all deep research JSON files."""
    all_neighbors = []
    all_citations = []
    all_overview_summaries = []
    location_context = None

    for filepath in filepaths:
        filepath = Path(filepath)
        if not filepath.exists():
            print(f"Warning: Deep research file not found: {filepath}")
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Extract data from each file
        all_neighbors.extend(data.get("neighbors", []))
        all_citations.extend(data.get("annotations", []))

        if data.get("overview_summary"):
            all_overview_summaries.append(data["overview_summary"])

        # Get location context from first file
        if not location_context and data.get("location_context"):
            location_context = data["location_context"]

    return {
        "neighbors": all_neighbors,
        "citations": all_citations,
        "overview_summary": " ".join(all_overview_summaries)
        if all_overview_summaries
        else None,
        "location_context": location_context,
    }


def generate_neighbor_reports(data: dict):
    """
    Generate HTML reports from neighbor data.
    Always loads location.json for coordinates/pin data and sets request_date.

    Args:
        data: Neighbor analysis data loaded from neighbor_final_merged.json
    """
    try:
        print(f"[generate_neighbor_reports] Starting HTML generation...")
        print(f"[generate_neighbor_reports] Data keys: {list(data.keys())}")
        print(
            f"[generate_neighbor_reports] Neighbors count: {len(data.get('neighbors', []))}"
        )

        OUT.mkdir(parents=True, exist_ok=True)
        env = _env()
    except Exception as e:
        print(f"[generate_neighbor_reports] ERROR during initialization: {e}")
        import traceback

        traceback.print_exc()
        raise

    # Copy logos to output directory
    import shutil

    logo_dark = BASE / "templates" / "Helpen_Logo_Dark_Navy.svg"
    if logo_dark.exists():
        shutil.copy2(logo_dark, OUT / "Helpen_Logo_Dark_Navy.svg")
        print(f"[generate_neighbor_reports] Copied logo to output directory")

    # Always load location data
    location_file = BASE / "neighbor_outputs" / "location.json"
    if location_file.exists():
        with open(location_file, "r", encoding="utf-8") as f:
            location_data = json.load(f)
            # Add coordinates or pin to data
            if "coords" in location_data:
                data["coordinates"] = location_data["coords"]
            if "pin" in location_data:
                data["pin"] = location_data["pin"]
            print(
                f"[generate_neighbor_reports] Loaded location data from {location_file.name}"
            )

    # Always set request_date to current date
    data["request_date"] = datetime.now().strftime("%Y-%m-%d")

    # Don't load deep research files - we should only use the final merged data
    # which already has the correct adjacency information from the orchestrator

    # ---------- Title ----------
    # Use city if available, otherwise use county
    city = data.get("city", "")
    county = data.get("county", "")
    state = data.get("state", "")

    print(
        f"[generate_neighbor_reports] Location info - City: {city}, County: {county}, State: {state}"
    )

    if city:
        location_display = f"{city}, {state}"
    elif county:
        location_display = f"{county}, {state}"
    else:
        location_display = state or "Unknown Location"

    title_ctx = {
        "location_display": location_display,  # New field for display
        "county": county or data.get("location", "").split(",")[0].strip(),
        "state": state or data.get("location", "").split(",")[-1].strip(),
        "coordinates": data.get("coordinates"),
        # risk_score used by the bars; default safe value
        "risk_score": float(data.get("risk_score", 0))
        if str(data.get("risk_score", "")).replace(".", "", 1).isdigit()
        else 0.0,
    }
    html = env.get_template("neighbor-title-page-playwright.html").render(**title_ctx)
    (OUT / "neighbor-title-page-playwright.html").write_text(html, encoding="utf-8")
    print(
        f"[generate_neighbor_reports] ✓ Generated neighbor-title-page-playwright.html"
    )

    # ---------- Parameters ----------
    params_ctx = {
        "coordinates": data.get("coordinates", "Not provided"),
        "pin": normalize_pin(", ".join(_list(data.get("pin"))))
        if data.get("pin")
        else "Not provided",
        "technology": data.get("technology", "Not provided"),
        "request_date": data.get(
            "request_date", datetime.utcnow().strftime("%Y-%m-%d")
        ),
        # Optional: county/state if you want them visible in the Coordinates card details
        "county": data.get("county"),
        "state": data.get("state"),
        "location_detail": data.get("location_detail"),
    }
    html = env.get_template("neighbor-parameters-playwright.html").render(**params_ctx)
    (OUT / "neighbor-parameters-playwright.html").write_text(html, encoding="utf-8")
    print(
        f"[generate_neighbor_reports] ✓ Generated neighbor-parameters-playwright.html"
    )

    # ---------- Neighbor table ----------
    # New schema: neighbor_id, name, entity_category, entity_type, pins, claims, confidence
    neighbors = []
    for nb in data.get("neighbors", []):
        # Claims is now a string
        claims = nb.get("claims", "")

        # Handle approach_recommendations - format motivations + engage text
        approach = nb.get("approach_recommendations", {})
        if isinstance(approach, dict):
            motivations = approach.get("motivations", [])
            engage = approach.get("engage", "")

            # Format: "motivation_1, motivation_2, motivation_3. \n\n[engage text]"
            if motivations:
                motivations_text = ", ".join(motivations)
                approach_text = (
                    f"{motivations_text}.\n\n{engage}" if engage else motivations_text
                )
            else:
                approach_text = engage
        else:
            # Handle legacy string format if present
            approach_text = approach if approach else ""

        neighbor_dict = {
            "neighbor_id": nb.get("neighbor_id", ""),
            "name": nb.get("name", ""),
            "entity_category": nb.get("entity_category", ""),
            "entity_type": nb.get("entity_type", ""),
            "pins": nb.get("pins", []),  # Already a list in new schema
            "claims": claims,
            "noted_stance": nb.get("noted_stance", ""),
            "community_influence": nb.get("community_influence", ""),
            "influence_justification": nb.get("influence_justification", ""),
            "entity_classification": nb.get("entity_classification", ""),
            "approach_recommendations": approach_text,
            "confidence": abbreviate_confidence(nb.get("confidence", "")),
            "owns_adjacent_parcel": nb.get(
                "owns_adjacent_parcel", "No"
            ),  # Added adjacency flag
        }
        neighbors.append(neighbor_dict)

    table_ctx = {
        "neighbors": neighbors,
        "overview_summary": data.get("overview_summary"),  # The 2-3 sentence summary
        "residents_summary": data.get(
            "residents_summary"
        ),  # Summary specifically for residents
        "organizations_summary": data.get(
            "organizations_summary"
        ),  # Summary specifically for organizations
        "location_context": data.get("location_context"),
        "request_date": data.get("request_date", datetime.now().strftime("%Y-%m-%d")),
    }
    try:
        html = env.get_template("neighbor-deep-dive.html").render(**table_ctx)
        (OUT / "neighbor-deep-dive.html").write_text(html, encoding="utf-8")
        print(f"[generate_neighbor_reports] ✓ Generated all HTML files successfully")
    except Exception as e:
        print(f"[generate_neighbor_reports] ERROR: Failed to generate HTML - {e}")
        raise

    return [
        str(OUT / "neighbor-title-page-playwright.html"),
        str(OUT / "neighbor-parameters-playwright.html"),
        str(OUT / "neighbor-deep-dive.html"),
    ]


if __name__ == "__main__":
    """Convert saved JSON to HTML"""
    import sys

    print("=" * 60)
    print("NEIGHBOR HTML CONVERSION (Standalone)")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Clean up old outputs
    base = Path(__file__).parent
    print("\n🧹 Cleaning up old outputs...")

    # Clean HTML outputs
    html_output_dir = base / "neighbor_html_outputs"
    if html_output_dir.exists():
        for html_file in html_output_dir.glob("*.html"):
            html_file.unlink()
            print(f"  ✓ Deleted {html_file.name}")
        for svg_file in html_output_dir.glob("*.svg"):
            svg_file.unlink()
            print(f"  ✓ Deleted {svg_file.name}")

    # Clean PDF outputs
    pdf_dirs = [base / "individual_pdf_reports", base / "combined_pdf_reports"]
    for pdf_dir in pdf_dirs:
        if pdf_dir.exists():
            for pdf_file in pdf_dir.glob("*.pdf"):
                pdf_file.unlink()
                print(f"  ✓ Deleted {pdf_file.name}")

    # Check for required JSON files
    merged_file = base / "neighbor_outputs" / "neighbor_final_merged.json"
    location_file = base / "neighbor_outputs" / "location.json"

    if not merged_file.exists():
        print(f"❌ Error: neighbor_final_merged.json not found")
        print(f"   Expected at: {merged_file}")
        print("\nPlease run the neighbor pipeline first to generate data.")
        sys.exit(1)

    if not location_file.exists():
        print(f"❌ Error: location.json not found")
        print(f"   Expected at: {location_file}")
        print("\nPlease run the neighbor pipeline with the updated orchestrator.")
        sys.exit(1)

    # Load the merged data
    print(f"\n📂 Loading neighbor data from {merged_file.name}...")
    with open(merged_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"  ✓ Loaded {len(data.get('neighbors', []))} neighbors")
    print(f"  ✓ Location: {data.get('location_context', 'Unknown')}")

    # Generate HTML reports
    print(f"\n📄 Generating HTML reports...")
    try:
        html_files = generate_neighbor_reports(data)
        print(f"  ✓ Generated {len(html_files)} HTML files:")
        for html_file in html_files:
            print(f"    - {Path(html_file).name}")
    except Exception as e:
        print(f"❌ HTML generation failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    print("\n" + "=" * 60)
    print("HTML CONVERSION COMPLETE")
    print("Check ./neighbor_html_outputs/ for HTML reports")
    print(f"Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
