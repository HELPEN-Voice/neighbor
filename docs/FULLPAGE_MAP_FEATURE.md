# Full-Page Neighbor Map Feature

## Overview

This feature adds a **full-page map** to the neighbor screen PDF that displays **ALL neighbors** (High, Medium, AND Low influence) with numbered markers. The numbers correspond to a new `#` column added to the neighbor tables, allowing users to cross-reference the map with the detailed neighbor information.

## Why This Was Built

The existing map page (`neighbor-map-playwright.html`) only shows High/Medium influence neighbors and has a split layout with a reference table on the right side. This made the map small and crowded.

The diligence screen in `system_agent` has a similar full-page map feature. We needed the same capability for the standalone neighbor screen in this repo, but **without modifying any shared code** that could affect the diligence workflow.

## What Was Added

### New Files

| File | Purpose |
|------|---------|
| `src/neighbor/mapping/fullpage_map_generator.py` | Standalone map generator that includes ALL influence levels. Has its own `FullPageLabelGenerator` class. |
| `src/neighbor/templates/neighbor-map-fullpage.html` | Full-page template - map fills the entire page with small overlay legend |
| `docs/FULLPAGE_MAP_FEATURE.md` | This documentation |

### Modified Files

| File | Changes |
|------|---------|
| `src/neighbor/templates/neighbor-deep-dive.html` | Added `#` column as first column in both Residents and Organizations tables |
| `src/neighbor/convert_neighbor_to_html.py` | Generates fullpage map template, builds `name_to_marker` lookup for table column |
| `src/neighbor/convert_html_to_pdf.py` | Added `neighbor-map-fullpage.pdf` to PDF combination order |
| `src/neighbor/orchestrator/neighbor_orchestrator.py` | Added `generate_fullpage_map()` helper, calls it in both fresh run and cached paths |

### What Was NOT Modified

- `src/neighbor/mapping/labeling.py` - unchanged (used by existing map)
- `src/neighbor/mapping/map_generator.py` - unchanged (used by existing map)
- `src/neighbor/templates/neighbor-map-playwright.html` - unchanged (existing map page)
- **Nothing in system_agent repo**

## How It Works

### Map Generation Flow

```
NeighborOrchestrator.screen()
    │
    ├── Generates regular map (800x450, High/Medium only)
    │   └── Uses existing NeighborMapGenerator + LabelGenerator
    │
    └── Generates full-page map (1920x1080, ALL influence levels)
        └── Uses NEW FullPageMapGenerator + FullPageLabelGenerator
```

### Data Flow

1. **Orchestrator** generates both maps and stores in `neighbor_final_merged.json`:
   - `map_image_path` / `map_labels` - existing map (High/Medium only)
   - `fullpage_map_image_path` / `fullpage_map_labels` - new full-page map (ALL neighbors)

2. **HTML Conversion** (`convert_neighbor_to_html.py`):
   - Builds `name_to_marker` lookup from `fullpage_map_labels`
   - Passes `map_marker` to each neighbor dict for table rendering
   - Generates `neighbor-map-fullpage.html` template

3. **PDF Pipeline** (`convert_html_to_pdf.py`):
   - Converts all HTML files to individual PDFs
   - Combines in order: title → parameters → existing map → **fullpage map** → deep-dive tables

### Numbering System

- Numbers are assigned **per unique owner name** (not per parcel)
- If John Smith owns 3 parcels, all 3 show the same number
- Sorted by influence: High → Medium → Low
- Characters: 1-9, then a-z (supports up to 35 unique owners)
- Target parcel always gets "T"

## PDF Page Order

1. Title page
2. Parameters page
3. Parcel Overview (existing small map with reference table)
4. **Parcel Map (NEW full-page map)**
5. Neighbor tables (Residents, then Entities)

## Configuration

The full-page map uses these settings (hardcoded in `generate_fullpage_map()`):

```python
width=1920      # Full HD width
height=1080     # Full HD height
padding=80      # Larger padding for breathing room
retina=True     # High-DPI output
```

## Testing

1. Run the neighbor pipeline for a location:
   ```bash
   python -m src.neighbor.orchestrator.neighbor_orchestrator --location "39.5,-86.5" --county "Putnam County" --state "Indiana"
   ```

2. Run the conversion pipeline:
   ```bash
   python src/neighbor/run_conversion_pipeline.py
   ```

3. Check the output PDF in `combined_pdf_reports/` - should now have 5+ pages with the full-page map before the tables.

4. Verify:
   - Full-page map shows ALL neighbors (including Low influence)
   - Each neighbor has a numbered marker
   - The `#` column in the tables matches the map markers
   - Low influence neighbors appear in gray on the map

## Troubleshooting

**Map not generating?**
- Check `MAPBOX_ACCESS_TOKEN` is set in `.env`
- Verify `raw_parcels.json` exists in `neighbor_outputs/`

**Numbers missing in table?**
- Ensure `fullpage_map_labels` is populated in `neighbor_final_merged.json`
- Check the `name_to_marker` lookup is finding matches (names must match exactly)

**PDF missing the fullpage map page?**
- Verify `neighbor-map-fullpage.html` was generated in `neighbor_html_outputs/`
- Check `convert_html_to_pdf.py` includes `neighbor-map-fullpage.pdf` in the combination list
