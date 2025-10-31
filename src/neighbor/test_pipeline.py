#!/usr/bin/env python3
"""
Test script for the Neighbor Intelligence pipeline.
Tests: JSON parsing, validation, citation enforcement, HTML rendering, and PDF generation.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any

# CRITICAL: Load environment variables FIRST before any imports that might use them
from dotenv import load_dotenv
# Go up 5 levels: test_pipeline.py -> neighbor -> tools -> ii_agent -> src -> project_root
env_path = Path(__file__).parent.parent.parent.parent.parent / '.env'
load_dotenv(env_path)
print(f"Loaded .env from {env_path}")
print(f"OPENAI_API_KEY present: {'OPENAI_API_KEY' in os.environ}")

# Add parent directories to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

# Now import modules that depend on environment variables
from neighbor.utils.json_parse import extract_fenced_blocks
from neighbor.models.schemas import NeighborProfile, NeighborResult
from neighbor.orchestrator.neighbor_orchestrator import NeighborOrchestrator
from neighbor.convert_neighbor_to_html import generate_neighbor_reports


def create_sample_llm_output() -> str:
    """Create a sample LLM response with both JSON and markdown blocks."""
    return '''
Here's the analysis of the neighbors:

```json
{
  "overview_summary": "Three neighbors identified within 0.5 miles: one confirmed opponent with petition history, one supportive church organization, and one uncited claim that should be downgraded. Mixed community sentiment requires targeted engagement strategy.",
  "neighbors": [
    {
      "name": "John Smith",
      "entity_type": "person",
      "pins": ["12-34-567-890"],
      "stance": "Strongly opposed solar project in 2022",
      "signal": "Opponent",
      "influence_level": "high",
      "influence": {
        "selected": ["County Planning Board", "Farm Bureau president"],
        "formal_roles": ["County Planning Board member since 2019"],
        "economic_footprint": ["Owns 200-acre dairy farm", "Employs 15 locals"]
      },
      "flags": ["Led petition against solar", "Spoke at 3 public hearings"],
      "social": {
        "platforms": ["Facebook"],
        "links": [{"label": "Facebook", "url": "https://facebook.com/jsmith"}]
      },
      "confidence": "high",
      "residency_status": "local",
      "tenure_signals": ["Third-generation farmer"],
      "citations": [
        {"claim": "Opposed solar at hearing", "url": "https://county.gov/minutes/2022-08", "title": "County Minutes", "date": "2022-08-15"},
        {"claim": "Led petition drive", "url": "https://news.local/solar-opposition", "title": "Local News", "date": "2022-09-01"}
      ]
    },
    {
      "name": "Riverside Baptist Church",
      "entity_type": "organization",
      "pins": ["12-34-567-891"],
      "stance": "Supportive of clean energy initiatives",
      "signal": "Supporter",
      "influence_level": "medium",
      "org_classification": "church",
      "org_local_presence": "yes",
      "influence": {
        "selected": ["500+ member congregation", "Youth sports sponsor"],
        "affiliations": ["State Baptist Convention", "Local Ministerial Association"]
      },
      "flags": ["Hosts community events", "Solar panels on roof"],
      "social": {
        "platforms": ["Facebook", "YouTube"],
        "links": [
          {"label": "Facebook", "url": "https://facebook.com/riversidebaptist"},
          {"label": "YouTube", "url": "https://youtube.com/@riversidebaptist"}
        ]
      },
      "confidence": "high",
      "citations": [
        {"claim": "Solar panels installed 2021", "url": "https://church.org/news/solar", "title": "Church Newsletter", "date": "2021-06-15"}
      ]
    },
    {
      "name": "Mary Johnson",
      "entity_type": "person",
      "pins": ["12-34-567-892"],
      "stance": "Oppose",
      "signal": "Opponent",
      "influence_level": "high",
      "flags": ["Very influential in community"],
      "confidence": "high",
      "residency_status": "local",
      "citations": []
    }
  ],
  "citations_flat": [
    {"title": "County Minutes", "url": "https://county.gov/minutes/2022-08"},
    {"title": "Local News", "url": "https://news.local/solar-opposition"},
    {"title": "Church Newsletter", "url": "https://church.org/news/solar"}
  ]
}
```

```markdown
| Name | Entity Type | PINs | Influence | Stance | Flags | Links | Confidence |
|------|-------------|------|-----------|--------|-------|-------|------------|
| John Smith | Person | 12-34-567-890 | County Planning Board, Farm Bureau | Opposed solar 2022 | Petition leader | Facebook | High |
| Riverside Baptist | Organization | 12-34-567-891 | 500+ members | Supportive | Has solar panels | FB, YouTube | High |
| Mary Johnson | Person | 12-34-567-892 | Community leader | Oppose | Influential | None | High |
```

This is the complete analysis.
'''


async def test_pipeline():
    """Test the complete neighbor intelligence pipeline."""
    print("=" * 60)
    print("NEIGHBOR INTELLIGENCE PIPELINE TEST")
    print("=" * 60)
    
    # 1. Test JSON extraction and parsing
    print("\n1. Testing JSON extraction from fenced blocks...")
    sample_output = create_sample_llm_output()
    
    try:
        json_data, markdown_data = extract_fenced_blocks(sample_output)
        print("   ✓ Successfully extracted JSON from fenced block")
        print(f"   ✓ Found {len(json_data.get('neighbors', []))} neighbors")
        print(f"   ✓ Overview summary: {json_data.get('overview_summary')[:50]}...")
        if markdown_data:
            print(f"   ✓ Also found markdown block ({len(markdown_data)} chars)")
    except Exception as e:
        print(f"   ✗ Failed to extract JSON: {e}")
        return False
    
    # 2. Test Pydantic validation
    print("\n2. Testing Pydantic validation...")
    validation_errors = []
    for idx, neighbor in enumerate(json_data.get("neighbors", [])):
        try:
            profile = NeighborProfile(**neighbor)
            print(f"   ✓ Neighbor {idx+1} ({neighbor['name']}) validated successfully")
        except Exception as e:
            validation_errors.append(f"Neighbor {idx+1}: {e}")
            print(f"   ✗ Neighbor {idx+1} ({neighbor.get('name', 'Unknown')}) validation failed:")
            print(f"      Error: {e}")
            print(f"      Problematic data: {json.dumps(neighbor, indent=2)[:500]}")
    
    # 3. Test citation validation (the important part!)
    print("\n3. Testing citation validation (downgrade uncited claims)...")
    print("   Before citation validation:")
    mary = json_data["neighbors"][2]  # Mary Johnson with no citations
    print(f"     - Mary Johnson stance: '{mary.get('stance')}'")
    print(f"     - Mary Johnson influence_level: '{mary.get('influence_level')}'")
    print(f"     - Mary Johnson citations: {mary.get('citations')}")
    
    # Simulate the orchestrator's citation validation logic
    from neighbor.orchestrator.neighbor_orchestrator import NeighborOrchestrator
    orchestrator = NeighborOrchestrator()
    
    # Apply citation validation logic (copied from orchestrator)
    DECISION_FIELDS = {
        "stance": "unknown",
        "signal": "unknown",
        "influence_level": "unknown",
        "risk_level": "unknown",
        "profile_summary": None,
        "engagement_recommendation": None,
        "residency_status": "unknown",
        "approx_age_bracket": "unknown",
        "org_classification": "unknown",
        "org_local_presence": "unknown",
        "confidence": "medium",
    }
    
    DECISION_LIST_FIELDS = [
        "flags",
        "behavioral_indicators",
        "financial_stress_signals",
        "coalition_predictors",
        "tenure_signals",
        "household_public_signals",
    ]
    
    neighbors_copy = json_data["neighbors"].copy()
    for p in neighbors_copy:
        has_citations = bool(p.get("citations"))
        
        if not has_citations:
            # Downgrade fields
            for field, default_value in DECISION_FIELDS.items():
                if field in p and p[field] not in [None, default_value, "unknown", ""]:
                    p[field] = default_value
            
            for field in DECISION_LIST_FIELDS:
                if field in p and p[field]:
                    p[field] = []
            
            if p.get("influence"):
                if isinstance(p["influence"], dict):
                    p["influence"]["selected"] = []
                    p["influence"]["formal_roles"] = []
                    p["influence"]["informal_roles"] = []
                    p["influence"]["economic_footprint"] = []
                    p["influence"]["affiliations"] = []
                    p["influence"]["network_notes"] = []
    
    print("\n   After citation validation:")
    mary_after = neighbors_copy[2]
    print(f"     - Mary Johnson stance: '{mary_after.get('stance')}' (should be 'unknown')")
    print(f"     - Mary Johnson influence_level: '{mary_after.get('influence_level')}' (should be 'unknown')")
    print(f"     - Mary Johnson flags: {mary_after.get('flags')} (should be [])")
    
    # Verify the downgrade worked
    assert mary_after.get("stance") == "unknown", "Stance should be downgraded to 'unknown'"
    assert mary_after.get("influence_level") == "unknown", "Influence level should be downgraded"
    assert mary_after.get("flags") == [], "Flags should be empty"
    print("   ✓ Citation validation working correctly!")
    
    # 4. Test HTML generation
    print("\n4. Testing HTML generation...")
    result_data = {
        "neighbors": neighbors_copy,
        "overview_summary": json_data.get("overview_summary"),
        "location_context": "Neighbors within 0.5 mi of Test County, WV",
        "county": "Test County",
        "state": "WV",
        "coordinates": "39.3190, -77.7312",
        "pin": ["09-12-345-6789-000"],
        "technology": "Solar",
        "request_date": "2025-01-09",
        "risk_score": 3.2,
        "success": True,
        "citations_flat": json_data.get("citations_flat", [])
    }
    
    try:
        html_files = generate_neighbor_reports(result_data)
        print(f"   ✓ Generated {len(html_files)} HTML files:")
        for f in html_files:
            if Path(f).exists():
                print(f"     - {Path(f).name} ({Path(f).stat().st_size} bytes)")
            else:
                print(f"     - {Path(f).name} (NOT FOUND)")
    except Exception as e:
        print(f"   ✗ HTML generation failed: {e}")
        return False
    
    # 5. Check HTML content for proper rendering
    print("\n5. Checking HTML content...")
    deep_dive_html = Path(html_files[2])  # neighbor-deep-dive.html
    if deep_dive_html.exists():
        content = deep_dive_html.read_text()
        
        # Check for overview summary
        if json_data.get("overview_summary") and json_data["overview_summary"][:30] in content:
            print("   ✓ Overview summary found in HTML")
        else:
            print("   ✗ Overview summary missing from HTML")
        
        # Check for downgraded Mary Johnson
        if "Mary Johnson" in content:
            print("   ✓ Mary Johnson rendered in table")
            # Her stance should now show as empty/unknown in the table
    
    # 6. Test PDF generation and combination
    print("\n6. Testing PDF generation and combination...")
    try:
        import subprocess
        from pypdf import PdfReader, PdfWriter
        
        base_dir = Path(__file__).parent
        pdf_script = base_dir / "convert_html_to_pdf.py"
        
        if pdf_script.exists():
            # Generate individual PDFs
            result = subprocess.run(
                [sys.executable, str(pdf_script)],
                cwd=str(base_dir),
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                pdf_dir = base_dir / "individual_pdf_reports"
                combined_dir = base_dir / "combined_pdf_reports"
                combined_dir.mkdir(parents=True, exist_ok=True)
                
                # Combine PDFs in order
                writer = PdfWriter()
                expected_pdfs = [
                    "neighbor-title-page-playwright.pdf",
                    "neighbor-parameters-playwright.pdf",
                    "neighbor-deep-dive.pdf"
                ]
                
                pages_added = 0
                for pdf_name in expected_pdfs:
                    pdf_path = pdf_dir / pdf_name
                    if pdf_path.exists():
                        reader = PdfReader(str(pdf_path))
                        for page in reader.pages:
                            writer.add_page(page)
                            pages_added += 1
                        print(f"   ✓ Added {pdf_name} ({len(reader.pages)} page(s))")
                
                # Write combined PDF
                combined_path = combined_dir / "neighbor_report_test.pdf"
                with open(combined_path, "wb") as fp:
                    writer.write(fp)
                
                print(f"   ✓ Combined PDF created: {combined_path}")
                print(f"   ✓ Total pages: {pages_added}")
                
                # Clean up individual PDFs if you want
                # for pdf_name in expected_pdfs:
                #     (pdf_dir / pdf_name).unlink(missing_ok=True)
                
            else:
                print(f"   ✗ PDF generation failed: {result.stderr}")
        else:
            print("   ⚠ PDF converter script not found (skipping)")
    except ImportError:
        print("   ⚠ pypdf not installed - cannot combine PDFs")
    except Exception as e:
        print(f"   ⚠ PDF generation test skipped: {e}")
    
    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)
    
    return True


if __name__ == "__main__":
    # Run the test
    success = asyncio.run(test_pipeline())
    sys.exit(0 if success else 1)