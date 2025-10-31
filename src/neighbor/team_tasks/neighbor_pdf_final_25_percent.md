# Neighbor Agent PDF Generation - Final 25% Implementation

## Overview
Complete the Neighbor Agent by adding PDF generation capabilities that mirror the Diligence Agent's architecture with EXACT branding. This is a simple copy-and-adapt approach using existing, working code.

## Current Status ✅
- ✅ Neighbor agent core functionality complete (JSON output)
- ✅ Orchestrator, engines, models all working  
- ✅ Diligence PDF pipeline fully functional
- ❌ Missing: HTML templates + PDF conversion for Neighbor

## Goal
Transform Neighbor JSON output → Professional PDF reports with identical Diligence branding

## UNDER-ENGINEERED Implementation Plan

### STEP 1: Copy Core Infrastructure (5 minutes)
Simply copy these working files from Diligence to Neighbor:

```bash
# Copy PDF converter (works perfectly as-is)
cp src/ii_agent/tools/diligence/pdf_converter.py src/ii_agent/tools/neighbor/

# Copy conversion scripts (adapt file paths only)
cp src/ii_agent/tools/diligence/convert_html_to_pdf.py src/ii_agent/tools/neighbor/
cp src/ii_agent/tools/diligence/run_conversion_pipeline.py src/ii_agent/tools/neighbor/

# Copy logos
cp src/ii_agent/tools/diligence/Helpen_Logo_*.svg src/ii_agent/tools/neighbor/
```

### STEP 2: Create 3 Simple HTML Templates (20 minutes)

**A. Title Page** (`templates/neighbor-title-page.html`)
- Copy `diligence/templates/title-page-playwright.html`
- Change: "Community Screens" → "Neighbor Screens" 
- Same branding, colors, fonts, layout

**B. Parameters Page** (`templates/neighbor-parameters.html`)
- Copy `diligence/templates/parameters-disclaimer-playwright.html`
- Update parameters table:
  - Coordinates: `{{ coordinates }}`
  - PIN: `{{ pin }}` 
  - Technology: `{{ technology }}`
  - Request Date: `{{ request_date }}`

**C. Main Report Page** (`templates/neighbor-deep-dive.html`)
- Copy any diligence deep dive template as base
- Replace content area with neighbor table:
  - Helpen logo + "Neighbor Screens" blue label
  - Table with columns: Neighbor ID, Entity Type, PINs, Influence Indicators, Stance Signal, Notable Risk/Opportunity Flags, Public Social Links, Confidence

### STEP 3: Create Simple Conversion Script (15 minutes)

**`convert_neighbor_to_html.py`** - Copy `diligence/convert_diligence_to_html.py`:

```python
def generate_neighbor_reports(json_data):
    """Convert neighbor JSON to HTML using templates"""
    
    # Title page - same template, different title
    # Parameters page - use neighbor-specific fields  
    # Main report - loop through neighbors array, populate table
    
    # Use exact same Jinja2 template rendering as Diligence
```

### STEP 4: Create Output Directories (2 minutes)
```bash
mkdir -p src/ii_agent/tools/neighbor/{neighbor_html_outputs,individual_pdf_reports,combined_pdf_reports}
```

### STEP 5: Integration Point (8 minutes)

Add to `neighbor/orchestrator/neighbor_orchestrator.py`:

```python
async def generate_pdf_report(self, result_data, output_format="pdf"):
    """Generate PDF report from neighbor screening results"""
    if output_format == "pdf":
        # Call convert_neighbor_to_html.py
        # Call convert_html_to_pdf.py  
        # Return PDF file path
    return result_data
```

## Implementation Details

### Template Specifications

**Title Page Changes:**
```html
<!-- Change this line -->
<div class="main-title">{{ county|upper }}, {{ state|upper }} - Community Screens</div>
<!-- To this -->
<div class="main-title">{{ location|upper }} - Neighbor Screens</div>
```

**Parameters Page Content:**
```html
<div class="params-grid">
    <div class="param-item">
        <span class="param-label">Coordinates:</span>
        <span class="param-value">{{ coordinates }}</span>
    </div>
    <div class="param-item">
        <span class="param-label">PIN:</span>
        <span class="param-value">{{ pin }}</span>
    </div>
    <div class="param-item">
        <span class="param-label">Technology:</span>
        <span class="param-value">{{ technology }}</span>
    </div>
    <div class="param-item">
        <span class="param-label">Request Date:</span>
        <span class="param-value">{{ request_date }}</span>
    </div>
</div>
```

**Main Report Table:**
```html
<div class="section-content">
    <div class="page-header">
        <img src="Helpen_Logo_Dark_Navy.svg" alt="Helpen" class="logo" />
        <div class="section-label">Neighbor Screens</div>
    </div>
    
    <table class="neighbor-table">
        <thead>
            <tr>
                <th>Neighbor ID</th>
                <th>Entity Type</th>
                <th>PINs (as given)</th>
                <th>Influence Indicators (selected)</th>
                <th>Stance Signal</th>
                <th>Notable Risk/Opportunity Flags</th>
                <th>Public Social Links (if any)</th>
                <th>Confidence</th>
            </tr>
        </thead>
        <tbody>
        {% for neighbor in neighbors %}
            <tr>
                <td>{{ neighbor.name }}</td>
                <td>{{ neighbor.entity_type }}</td>
                <td>{{ neighbor.pins or 'N/A' }}</td>
                <td>{{ neighbor.influence_level }}</td>
                <td>{{ neighbor.stance }}</td>
                <td>{{ neighbor.risk_level }}</td>
                <td>
                    {% if neighbor.social.platforms %}
                        {{ neighbor.social.platforms|join(', ') }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>{{ neighbor.confidence or 'Medium' }}</td>
            </tr>
        {% endfor %}
        </tbody>
    </table>
</div>
```

## File Structure (Post-Implementation)
```
src/ii_agent/tools/neighbor/
├── templates/
│   ├── neighbor-title-page.html
│   ├── neighbor-parameters.html
│   └── neighbor-deep-dive.html
├── pdf_converter.py                  # Copied from diligence
├── convert_neighbor_to_html.py      # Adapted from diligence
├── convert_html_to_pdf.py           # Adapted from diligence
├── run_conversion_pipeline.py       # Adapted from diligence
├── neighbor_html_outputs/           # Generated HTML files
├── individual_pdf_reports/          # Individual PDF pages
└── combined_pdf_reports/            # Final combined PDFs
```

## Exact Code to Copy & Modify

### 1. Copy pdf_converter.py (NO CHANGES NEEDED)
```bash
cp src/ii_agent/tools/diligence/pdf_converter.py src/ii_agent/tools/neighbor/
```

### 2. Adapt convert_html_to_pdf.py (Change paths only)
```python
# Line 24: Change path
base_path = get_base_path().replace('diligence', 'neighbor')

# Line 25: Change directory name  
files = glob.glob(f"{base_path}neighbor_html_outputs/*.html")

# Line 63: Change directory name
os.makedirs(f"{base_path}individual_pdf_reports", exist_ok=True)
```

### 3. Create convert_neighbor_to_html.py
```python
# Copy convert_diligence_to_html.py
# Change these functions:
# - get_base_path() → return neighbor path
# - generate_title_page() → use neighbor data
# - Add generate_neighbor_table() → populate table from JSON
```

## Testing Plan (5 minutes)
1. Run neighbor agent → get JSON output
2. Run conversion pipeline → get PDFs
3. Verify branding matches Diligence exactly
4. Check table data populates correctly

## Success Criteria
- ✅ PDF output with exact Diligence branding
- ✅ "Neighbor Screens" label instead of "Community Screens"
- ✅ Proper table with neighbor data
- ✅ Same fonts, colors, layout as Diligence
- ✅ Professional quality output ready for client delivery

## Time Estimate: 50 minutes total
- Infrastructure copy: 5 min
- Templates creation: 20 min  
- Conversion script: 15 min
- Directory setup: 2 min
- Integration: 8 min

This is a straightforward copy-and-adapt approach using proven, working code. No complex engineering required.