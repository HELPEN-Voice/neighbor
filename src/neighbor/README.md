# Neighbor Analysis Pipeline

**Purpose:** Automated, PII-free community sentiment analysis for prospective clean energy development sites. Profiles neighboring property owners and organizations, aggregates findings into privacy-safe output, and generates a concentric sentiment ring map showing community attitudes by distance zone.

This pipeline complements the county-level Community Diligence workflow by zooming into the micro-neighborhood: landowners, LLCs, churches, small businesses — any adjacent stakeholder with influence on permits, meetings, and public sentiment. Every claim is citation-backed; anything unverifiable is marked `unknown`. **No individual names, addresses, or parcel boundaries appear in final output.**

## Table of Contents

- [What it does](#what-it-does)
- [Key design principles](#key-design-principles)
- [Architecture](#architecture)
- [Directory layout](#directory-layout)
- [Sentiment ring map](#sentiment-ring-map)
- [Inputs & configuration](#inputs--configuration)
- [Outputs & schema](#outputs--schema)
- [Running the pipeline](#running-the-pipeline)
- [Testing](#testing)
- [Environment variables](#environment-variables)

## What it does

1. **Identifies neighbors** via Regrid parcel API — finds property owners within an expanding radius of the target site (up to 30 owners). Detects parcels adjacent to the target property.
2. **Runs OpenAI Deep Research in parallel batches** to build qualitative profiles for each neighbor:
   - Background & identity (occupation, org type, local tenure indicators)
   - Public stance on development (renewables, warehouses, data centers, transmission)
   - Community influence (formal roles + informal "soft power" signals)
   - Risk & influence scoring + engagement recommendation
3. **Verifies claims** via Gemini Deep Research cross-check — every factual claim is independently verified; uncited claims are downgraded to `unknown`.
4. **Aggregates into PII-free output** — individual profiles are consumed in-memory only. Final output contains only aggregate statistics: stance distribution, influence breakdown, entity type counts, community themes, and risk scoring.
5. **Generates a sentiment ring map** — concentric translucent rings centered on the target parcel, colored by aggregate sentiment of neighbors within each distance band. No individual parcels are rendered.

## Key design principles

- **Privacy-first.** No individual names, addresses, PINs, or parcel boundaries in any output file. Individual profiles exist only in memory during processing.
- **No speculation.** If data isn't public or clear, return `unknown`.
- **Evidence everywhere.** Each claim includes a citation (URL/title) from Deep Research annotations.
- **Aggregate only.** Final deliverable shows community-level patterns — stance distributions, influence breakdowns, community themes — not individual dossiers.
- **Citation-backed verification.** Two-pass research: OpenAI Deep Research for initial profiles, Gemini Deep Research for independent verification.

## Architecture

Three-phase pipeline managed by `NeighborOrchestrator`:

### Phase 1: Neighbor Identification
- Uses Regrid API to find property owners within radius of target site
- Supports coordinate-based and PIN-based target lookup
- Auto-expands search radius to reach MAX_NEIGHBORS (30)
- Identifies parcels adjacent (touching) the target property
- Classifies entities as person vs organization via heuristics + Regrid metadata

### Phase 2: Deep Research + Verification
- Splits neighbors into person/organization groups with tailored prompts
- Runs concurrent OpenAI Deep Research batches (5 per batch, 15 parallel)
- Verifies each profile via Gemini Deep Research cross-check
- Uncited or unverified claims downgraded to `unknown`

### Phase 3: Aggregation + Map Generation
- Consumes individual profiles in memory, outputs only aggregate statistics
- Generates sentiment ring map via Mapbox Static Images API (GeoJSON overlay)
- Produces HTML/PDF reports with no individual PII

Research calls go through a **ResearchEngine** interface:
- **DeepResearchResponsesEngine** (default): OpenAI Responses API with `o4-mini-deep-research`
- **AgentsSDKEngine** (future): OpenAI Agents SDK for streaming and guardrails

## Directory layout

```
src/neighbor/
├── __init__.py                            # Public interface
├── orchestrator/
│   └── neighbor_orchestrator.py           # Pipeline control: identification → research → aggregation → map
├── agents/
│   ├── neighbor_finder.py                 # Regrid API: target lookup, radius expansion, adjacency detection
│   ├── verification_manager_neighbor.py   # Orchestrates Gemini verification passes
│   ├── verification_neighbor_base.py      # Base verification logic
│   ├── verification_neighbor_person.py    # Person-specific verification
│   └── verification_neighbor_org.py       # Organization-specific verification
├── engines/
│   ├── base.py                            # ResearchEngine protocol + event types
│   ├── responses_engine.py                # OpenAI Responses API (Deep Research)
│   └── agents_sdk_engine.py               # (future) Agents SDK engine
├── mapping/
│   ├── sentiment_ring_generator.py        # Concentric ring map: distance binning, GeoJSON, Mapbox render
│   ├── geometry_utils.py                  # Haversine distance, circle polygons, simplification, centroids
│   ├── mapbox_client.py                   # Mapbox Static Images API client (GeoJSON + polyline strategies)
│   ├── styles.py                          # Color/style constants
│   └── __init__.py                        # Module exports
├── models/
│   ├── schemas.py                         # Pydantic: NeighborProfile, NeighborResult
│   └── aggregate_schemas.py               # NeighborAggregateResult (PII-free output schema)
├── config/
│   ├── prompts.py                         # PERSON_SYSTEM & ORG_SYSTEM research prompts
│   └── settings.py                        # Model, batch size, caps, engine selector
├── utils/
│   ├── aggregator.py                      # Builds aggregate stats from individual profiles
│   ├── entity.py                          # Person vs organization heuristic
│   ├── json_parse.py                      # Robust JSON/fenced-block parser
│   ├── pin.py                             # PIN normalization
│   ├── geocoding.py                       # Coordinate/address utilities
│   └── db_connector.py                    # PostgreSQL persistence
├── services/
│   └── local_valuation.py                 # Property valuation utilities
├── templates/
│   ├── neighbor-title-page-playwright.html
│   ├── neighbor-parameters-playwright.html
│   ├── neighbor-sentiment-map.html        # Ring map + statistics slide
│   └── neighbor-deep-dive.html            # Aggregate summary slide
├── tests/
│   └── test_sentiment_rings.py            # 26 tests for geometry, binning, classification, ring generation
├── convert_neighbor_to_html.py            # JSON → HTML report generator
├── convert_html_to_pdf.py                 # HTML → PDF via Playwright
├── run_conversion_pipeline.py             # End-to-end conversion runner
└── webhook_server.py                      # FastAPI webhook for Deep Research callbacks
```

## Sentiment ring map

The sentiment ring map replaces per-parcel visualizations to eliminate PII re-identification risk. Individual parcel boundaries colored by stance are trivially re-identifiable via county GIS — a viewer can look up who owns any colored parcel in seconds.

### What the map shows

1. **Satellite imagery base** (Mapbox)
2. **Target parcel polygon** (gold fill) — the development site itself, not PII
3. **3 concentric translucent ring zones** centered on the target parcel centroid
4. Each ring colored by aggregate sentiment of neighbors within that distance band

### Ring coloring

| Sentiment | Color | Fill Opacity | Condition |
|-----------|-------|-------------|-----------|
| Oppose | `#DC2626` (red) | 0.25 | oppose ratio > 0.4 |
| Support | `#16A34A` (green) | 0.20 | support ratio > 0.4 |
| Mixed | `#F59E0B` (amber) | 0.20 | no category > 0.4 |
| Neutral | `#94A3B8` (gray) | 0.15 | neutral dominant |
| No data | `#94A3B8` (gray) | 0.10 | 0 neighbors in ring |

### Ring boundaries

Adaptive based on neighbor distribution:
- Compute haversine distance from target centroid to each neighbor's parcel centroid
- If max distance <= 0.5 mi: 3 equal-width bands
- Otherwise: 33rd / 67th percentile splits with 0.1 mi minimum ring width

### Rendering

Uses Mapbox Static Images API with GeoJSON overlay (supports filled polygons). 3 ring polygons + target polygon = ~5KB URL-encoded, well under 8KB limit. Rings 2 and 3 are donut polygons (outer ring + reversed inner ring hole).

## Inputs & configuration

### Running modes

- **Coordinate search:** Provide `location="lat,lon"` — finds target parcel and nearby owners via Regrid API
- **PIN search:** Provide `pin="018.0508.000"` + `county_path="/us/wi/clark/green-grove"` — looks up specific parcel

### Common parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `location` | `"lat,lon"` coordinates | — |
| `pin` | Target parcel number | — |
| `county_path` | Regrid county path | — |
| `radius_mi` | Initial search radius | 0.5 |
| `county` | County name | — |
| `state` | State abbreviation | — |
| `tech` | Technology type (solar, wind, etc.) | — |

## Outputs & schema

The pipeline produces **aggregate-only** output. No individual names, addresses, or parcel data:

```json
{
  "total_screened": 24,
  "residents_count": 18,
  "organizations_count": 6,
  "adjacent_count": 4,
  "stance_distribution": {"oppose": 5, "support": 8, "neutral": 7, "unknown": 4},
  "influence_distribution": {"High": 3, "Medium": 12, "Low": 9},
  "entity_type_breakdown": {"Resident": 18, "Organization": 6},
  "risk_score": 4.2,
  "risk_level": "moderate",
  "themes": ["Agricultural preservation concerns", "Property value impacts"],
  "opposition_summary": "Concentrated among adjacent agricultural landowners...",
  "support_summary": "Local business owners and community organizations...",
  "overview_summary": "Mixed community sentiment with moderate opposition...",
  "map_image_path": "/path/to/ring_map.png",
  "map_ring_stats": [
    {"ring": 1, "inner_mi": 0.0, "outer_mi": 0.25, "count": 5, "oppose": 3, "support": 1, "neutral": 1, "unknown": 0, "sentiment": "oppose"},
    {"ring": 2, "inner_mi": 0.25, "outer_mi": 0.6, "count": 8, "oppose": 1, "support": 4, "neutral": 3, "unknown": 0, "sentiment": "support"},
    {"ring": 3, "inner_mi": 0.6, "outer_mi": 1.2, "count": 11, "oppose": 1, "support": 3, "neutral": 3, "unknown": 4, "sentiment": "neutral"}
  ]
}
```

### HTML/PDF output

4 report slides generated:
1. **Title page** — location, risk score visualization
2. **Parameters** — coordinates, PIN, technology, date
3. **Sentiment ring map** — ring map image + distance-based statistics table
4. **Deep dive** — aggregate breakdown: stance distribution, influence, entity types, community themes

## Running the pipeline

```python
from neighbor import NeighborAgent

agent = NeighborAgent()

# Coordinate-based search
result = await agent.screen(
    location="44.8951,-90.4420",
    radius_mi=0.5,
    county="Marathon",
    state="WI",
    tech="solar"
)

# PIN-based search
result = await agent.screen(
    pin="018.0508.000",
    county_path="/us/wi/clark/green-grove",
    county="Clark",
    state="WI"
)
```

### Performance

| Metric | Value |
|--------|-------|
| Max neighbors | 30 |
| Batch size | 5 per Deep Research call |
| Concurrency | 15 parallel batches |
| Runtime | 15-30 minutes |
| Verification | Gemini Deep Research cross-check |

### Standalone HTML conversion

```bash
cd src/neighbor
python -m neighbor.convert_neighbor_to_html
python -m neighbor.convert_html_to_pdf
```

## Testing

```bash
# Sentiment ring tests (26 tests)
python -m pytest src/neighbor/tests/test_sentiment_rings.py -v

# All tests
python -m pytest src/neighbor/tests/ -v
```

Test coverage:
- `TestHaversineDistance` — zero distance, NYC-LA known distance, symmetry, short distance, antipodal
- `TestCreateCirclePolygon` — point count, closure, radius accuracy within 1%, latitude correction
- `TestRingBinning` — compact equal-width, spread percentile, minimum width, empty/single
- `TestSentimentAggregation` — oppose/support/neutral/mixed/no_data classification, boundary ratios
- `TestSentimentRingGenerator` — ring stats format, no neighbors, end-to-end mock, GeoJSON strategy, donut holes

## Environment variables

```bash
# Required
REGRID_API_KEY          # Parcel data from Regrid
OPENAI_API_KEY          # OpenAI Deep Research
MAPBOX_TOKEN            # Mapbox Static Images API

# Research configuration
ENGINE_TYPE=responses   # "responses" (default) or "agentsdk" (future)
DR_MODEL=o4-mini-deep-research-2025-06-26

# Database (optional)
DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT

# Webhook (for async Deep Research callbacks)
OPENAI_WEBHOOK_URL
OPENAI_WEBHOOK_SECRET
```
