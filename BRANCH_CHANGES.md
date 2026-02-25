# Branch: `neighbor-updates`

## Summary

Removes all PII from the neighbor screening pipeline's persistent outputs. Names, parcel IDs, addresses, and claims are used in-memory during Deep Research but are never written to disk, database, or reports. All persisted output is now aggregate: counts, distributions, risk scores, and LLM-generated community themes.

The most recent update adds **per-theme persona bullets with citations** to community themes. Each theme now includes anonymized one-line persona summaries (no names) backed by public-record citations, plus a mandatory 4th "Active Community Members" theme.

```
Deep Research (names in-memory) ──► AGGREGATION BOUNDARY ──► PII-Free Output (persisted)
```

---

## New Files

### `src/neighbor/models/aggregate_schemas.py`

Pydantic schemas for PII-free output:

| Schema | Purpose |
|--------|---------|
| `CommunityTheme` | Thematic grouping with description, neighbor count, concerns, influence level, engagement approach |
| `OppositionSummary` | Count of opposed neighbors, common concerns, influence levels |
| `SupportSummary` | Count of supportive neighbors, common reasons |
| `NeighborAggregateResult` | Top-level result with all counts, distributions, risk scores, themes, and metadata |

### `src/neighbor/utils/aggregator.py`

Converts `NeighborProfile` dicts (with PII) into a `NeighborAggregateResult` dict (PII-free):

| Function | Description |
|----------|-------------|
| `aggregate_neighbors()` | Main entry point — computes stats and generates themes via Gemini Flash |
| `_compute_counts()` | Tallies total, residents, organizations, adjacent |
| `_compute_influence_distribution()` | Counts High/Medium/Low influence |
| `_compute_stance_distribution()` | Counts oppose/support/neutral/unknown |
| `_compute_entity_type_breakdown()` | Counts by entity classification (agriculture, religious, municipal, etc.) |
| `_compute_risk()` | `min(10, 2 + (high_influence * 2) + (opposed * 3))` |
| `_build_opposition_summary()` | Extracts opposition count, concerns, influence levels |
| `_build_support_summary()` | Extracts support count, reasons |
| `_generate_themes()` | Sends profile names + claims snippets to Gemini Flash for exactly 4 community themes with per-individual member assignments |
| `_build_theme_members()` | Maps LLM member assignments back to profiles — extracts influence, adjacency, and citations (deduplicated by URL, capped at 3) |

### `src/neighbor/models/aggregate_schemas.py` — New Models

| Schema | Purpose |
|--------|---------|
| `ThemeMemberCitation` | Citation backing a theme member's persona (title, url, date) |
| `ThemeMember` | Individual assigned to a theme with anonymized persona line, influence level, adjacency flag, and citations |

The `CommunityTheme` model now includes a `members: List[ThemeMember]` field (defaults to `[]` for backward compatibility with older output).

### `src/neighbor/tests/test_aggregator_themes.py` — New

30 unit tests covering:
- `ThemeMemberCitation` / `ThemeMember` / `CommunityTheme` schema validation and serialization roundtrips
- `_build_theme_members()`: citation extraction, dedup, cap at 3, persona truncation, out-of-range/malformed index handling, null citations, influence normalization
- `_generate_themes()` with mocked Gemini: successful generation, empty/malformed responses, prompt content verification
- Backward compatibility: old JSON without `members` key loads correctly

---

## Modified Files

### `src/neighbor/orchestrator/neighbor_orchestrator.py`

- Calls `aggregate_neighbors()` after merge/dedupe to produce PII-free result
- Map generation runs before aggregation (needs profiles for parcel coloring) but labels are anonymized
- `neighbor_final_merged.json` now contains only aggregate data — no `neighbors[]` array
- Calls `save_neighbor_aggregate()` instead of `save_neighbor_stakeholders()`
- New `_cleanup_pii_files()` in `finally` block deletes all intermediate PII files:
  - `regrid_people.json`, `regrid_organizations.json`, `regrid_all.json`, `raw_parcels.json`, `batch_*.json`
  - `dr_*.json`, `vr_*.json`, `*.thinking.md`, `*_debug*.md`, `*_DEBUG_*.json`, `gemini_raw_*.txt`
- `synthesize_overview` prompt updated: "DO NOT mention any individual neighbor by name, parcel ID, or address"

### `src/neighbor/mapping/labeling.py`

| Before | After |
|--------|-------|
| Last name (e.g., "SMITH") | Influence label (e.g., "H01", "M03", "L12") |
| `full_name = "JOHN SMITH"` | `full_name = "High Influence — Oppose"` |
| `pin = "018.0508.000"` | `pin = ""` |
| Target: `full_name = "PIN: 018.0508.000"` | `full_name = "Target Site"` |
| Legend column: "Name" | Legend column: "Description" |
| Dedup key: `neighbor.name` | Dedup key: `neighbor.neighbor_id` |

### `src/neighbor/mapping/map_data_builder.py`

- Feature labels use anonymous identifiers instead of names
- Removed `"pin"` from GeoJSON properties

### `src/neighbor/mapping/fullpage_map_generator.py`

Same anonymization as `labeling.py` — influence-based labels, dedup by `neighbor_id`, all `pin` fields empty, target shows "Target Site".

### `src/neighbor/utils/db_connector.py`

New `save_neighbor_aggregate()` method stores PII-free aggregate JSON on `neighbor_screen_runs.aggregate_json` (JSONB) instead of inserting individual records into `stakeholders`.

### `src/neighbor/convert_neighbor_to_html.py`

Renders aggregate themes, distributions, and risk scores instead of individual neighbor rows.

---

## Database Migration

```sql
ALTER TABLE neighbor_screen_runs ADD COLUMN aggregate_json JSONB;
```

---

## Verification

```bash
# No PII in outputs
grep -r "SMITH\|JOHNSON" neighbor_outputs/ deep_research_outputs/ 2>/dev/null
# Should return nothing

# Intermediate files cleaned up
ls neighbor_outputs/regrid_*.json deep_research_outputs/dr_*.json 2>/dev/null
# Should be gone

# No neighbors[] array in final output
python3 -c "import json; d=json.load(open('src/neighbor/neighbor_outputs/neighbor_final_merged.json')); assert 'neighbors' not in d; print('OK')"
```
