# Neighbor Deep Research Agent

**Purpose:** Quickly build evidence-backed, hyper-local profiles of neighbors (people and organizations) around a prospective project site so developers can identify high-risk opposers and potential champions early—before serious dollars are spent.

This agent complements your county/township Community Diligence workflow by zooming into the micro-neighborhood: landowners, LLCs, churches, small businesses—any adjacent stakeholder with outsize influence on permits, meetings, and public sentiment. Every claim is citation-backed; anything not verifiable is marked `unknown`.

## Table of Contents

- [Why this exists](#why-this-exists)
- [What it does](#what-it-does)
- [Key design principles](#key-design-principles)
- [Architecture](#architecture)
- [Directory layout](#directory-layout)
- [Inputs & configuration](#inputs--configuration)
- [Outputs & schema](#outputs--schema)
- [Prompts (person vs org)](#prompts-person-vs-org)
- [Running the agent](#running-the-agent)
- [Streaming & tracing](#streaming--tracing)
- [Parcel API (NeighborFinder)](#parcel-api-neighborfinder)
- [Disambiguation, safety, and compliance](#disambiguation-safety-and-compliance)
- [Error handling, batching, rate limits](#error-handling-batching-rate-limits)
- [Testing plan](#testing-plan)
- [Agents SDK vs Orchestration (trade-offs)](#agents-sdk-vs-orchestration-trade-offs)
- [Roadmap](#roadmap)
- [FAQ](#faq)

## Why this exists

Neighbors can quietly make or break a project. A single influential landowner, a long-time school board chair, or the organizer of the Friday-morning "coffee group" can swing a hearing or set the tone on Facebook groups. Traditional research is slow and inconsistent. This agent automates the deep dive on the adjacent parcels and owners to surface stance, influence, and behavioral signals—with sources—so teams can prioritize outreach and avoid avoidable fights.

## What it does

- **Pulls a list of neighbors** (people & organizations) from a point/parcel + radius, or you can provide names directly.
- **Runs OpenAI Deep Research in parallel batches** (default 4 per batch) to build qualitative profiles for each neighbor:
  - Background & identity (occupation, org type, local tenure indicators)
  - Public stance & history on development (renewables, warehouses, data centers, transmission as proxies)
  - Community influence (formal roles + informal "soft power" signals)
  - Public social footprint (Facebook groups/pages, X posts, Nextdoor—all public only)
  - Opposition capacity (petition/LTE frequency, meeting appearances)
  - Financial stress indicators (public tax sale lists, UCC liens, auctions)
  - Coalition predictors (shared well/drainage/road associations)
  - Risk & influence scoring + engagement recommendation
- **Returns strict JSON** (primary) validated by Pydantic, plus optional Markdown for human consumption. All facts are citation-backed; unknowns remain `unknown`.

## Key design principles

- **Hyper-local, qualitative depth.** Not just "vocal or not," but who they are, how long they've been here, where they show up (church, VFW, coop boards), who trusts them, and digital footprints—all public.
- **No speculation.** If unsure, return `unknown`.
- **Evidence everywhere.** Each claim includes a citation (URL/title) from the Deep Research annotations.
- **Small, robust, swappable.** Orchestrator handles batching/concurrency; the research engine is an adapter (Responses now; Agents SDK later).
- **Consistent with Diligence.** Async orchestrator, config, prompts, schemas mirror the existing tool.

## Architecture

Two-phase pipeline managed by an async orchestrator:

1. **Neighbor identification (optional):** given location + radius, call a parcel API (e.g., ReGrid) to assemble nearby owners (persons + orgs). Or skip this by providing names.
2. **Deep Research:** split neighbors into person and organization groups; batch by 4 (default) per call; run in parallel; merge results; validate JSON via Pydantic; return citations.

The research call goes through a **ResearchEngine** interface:
- **DeepResearchResponsesEngine** (default): OpenAI Responses API using a Deep Research model with `web_search_preview`.
- **AgentsSDKEngine** (future): OpenAI Agents SDK for streaming, handoffs, and typed guardrails.

**Streaming/tracing seam:** an optional `on_event(evt)` callback receives start / progress / finish / error events per batch. Flip to Agents SDK later without touching orchestrator logic.

## Directory layout

```
src/neighbor/
├── __init__.py                        # NeighborAgent public interface
├── orchestrator/
│   └── neighbor_orchestrator.py       # batching, concurrency, merge, validation
├── engines/
│   ├── base.py                        # ResearchEngine Protocol + event type
│   ├── responses_engine.py            # Responses API (Deep Research) implementation
│   └── agents_sdk_engine.py           # (stub) Agents SDK impl for future streaming/guardrails
├── agents/
│   └── neighbor_finder.py             # (stub) ReGrid parcel integration
├── config/
│   ├── prompts.py                     # PERSON_SYSTEM & ORG_SYSTEM
│   └── settings.py                    # model, batch size, caps, engine selector
├── models/
│   └── schemas.py                     # Pydantic: NeighborProfile, NeighborResult
└── utils/
    ├── entity.py                      # heuristic to guess person vs organization
    └── json_parse.py                  # robust JSON/fenced block parser
```

## Inputs & configuration

**Ways to run:**
- **A)** Provide a list of names (best for early pilots and controlled tests), optionally with `entity_type_map={"Acme LLC":"organization"}`.
- **B)** Provide a point (`"lat,lon"`) and radius; the agent will (later) call a parcel API to get owners.

**Common parameters:**
- `location: str | None` – `"lat,lon"` (parcel ID support later)
- `radius_mi: float` – default 0.5 (settings)
- `neighbors: list[str] | None` – bypass parcel lookup if provided
- `county, state, tech: str | None` – context improves disambiguation & relevance
- `entity_type_map: dict[str,str] | None` – override person/org on a per-name basis
- `on_event: Optional[Callable[[ResearchEvent], None]]` – receive streaming/tracing events

**Environment / settings** (`config/settings.py`):
- `OPENAI_API_KEY` (required)
- `REGRID_API_KEY` (optional, for NeighborFinder)
- `ENGINE_TYPE`: `"responses"` (default) or `"agentsdk"` (future)
- `DR_MODEL`: `"o4-mini-deep-research-2025-06-26"` (default) or `"o3-deep-research-2025-06-26"`
- `BATCH_SIZE`: 4 (typ.)
- `MAX_NEIGHBORS`: 20 (cap)
- `CONCURRENCY_LIMIT`: 8 (semaphore)
- `STREAMING_ENABLED`, `TRACE_ENABLED`: flags to wire your app instrumentation

## Outputs & schema

Primary output is JSON validated by Pydantic (`NeighborResult`):

```json
{
  "neighbors": [
    {
      "name": "Karen Newman",
      "entity_type": "person",
      "profile_summary": "Long-time resident and community activist.",
      "residency_status": "local",
      "tenure_signals": ["Active since at least 2008 in county minutes"],
      "approx_age_bracket": "46-60",
      "household_public_signals": ["Spouse name appears in 2017 church bulletin"],
      "org_classification": "unknown",
      "org_local_presence": "unknown",
      "stance": "Opposed to 138 kV transmission line in 2022; petition organizer in 2019.",
      "influence_level": "high",
      "risk_level": "high",
      "engagement_recommendation": "Engage via hospital auxiliary lead; offer EMS grant.",
      "social": {
        "platforms": ["Facebook"],
        "groups_or_pages": ["Protect Jefferson Co."],
        "notable_posts": [
          {
            "claim": "Shared call-to-action against power line (2022-08).",
            "url": "https://example.local/fb-post",
            "title": "FB Group Post",
            "date": "2022-08-18"
          }
        ]
      },
      "influence": {
        "formal_roles": ["Hospital Foundation Board (former)"],
        "informal_roles": ["Festival organizer"],
        "economic_footprint": ["Sponsors HS athletics"],
        "affiliations": ["VFW Auxiliary"],
        "network_notes": ["Coffee group regular at Main St Diner"]
      },
      "behavioral_indicators": ["Frequent LTEs on local policy topics"],
      "financial_stress_signals": [],
      "coalition_predictors": ["Shared driveway maintenance association"],
      "disambiguation": {
        "candidates": ["Karen A. Newman (Martinsburg)"],
        "method": ["Matched by county minutes + mailing address"]
      },
      "citations": [
        { "claim": "Testified at County Commission Aug 2022", "url": "https://city.gov/minutes", "title": "Commission Minutes" }
      ]
    }
  ],
  "location_context": "Neighbors within 1.0 mi of Jefferson, WV",
  "success": true,
  "runtime_minutes": 2.3,
  "citations_flat": [
    { "title": "Commission Minutes", "url": "https://city.gov/minutes" }
  ]
}
```

**Notes:**
- `entity_type`: `"person"` | `"organization"` | `"unknown"` — the orchestrator splits batches by type to get the correct prompt.
- `stance`, `influence_level`, `risk_level` are requested from the model; we can add a light post-pass if any are omitted.
- `citations` exist per profile and `citations_flat` provides a merged list.

## Prompts (person vs org)

We maintain two focused system prompts in `config/prompts.py`:

- **PERSON_SYSTEM** targets individuals: background, tenure signals, community influence (formal/informal), social groups, petitions/LTEs, financial stress (public only), coalition predictors, risk/influence scores, and a 1–2 line engagement recommendation—with citations.

- **ORG_SYSTEM** targets organizations/LLCs: registries/filings, beneficial owners (public), local presence, prior activity/advocacy, donations/sponsorships, coalitions, social presence, stance/capacity, classification (energy developer, data center, agriculture, church/school, utility/co-op, land investment), risk/influence, engagement recommendation—with citations.

Both enforce strict JSON output and no speculation (`unknown` if not verifiable).

## Running the agent

Minimal usage when you provide names explicitly:

```python
from neighbor import NeighborAgent

agent = NeighborAgent()

result = await agent.screen(
    neighbors=["Karen Newman", "XYZ Capital LLC", "First Baptist Church"],
    entity_type_map={
        "XYZ Capital LLC": "organization",
        "First Baptist Church": "organization"
    },
    county="Jefferson",
    state="WV",
    tech="battery_storage",
    radius_mi=1.0
)

for n in result["neighbors"]:
    print(n["name"], "->", n["risk_level"])
```

Use `location="lat,lon"` + `radius_mi` to enable the parcel-to-owners path once NeighborFinder is wired to ReGrid.

## Streaming & tracing

You can pass an optional `on_event(evt)` callback to receive progress events from the engine:

```python
def on_event(evt):
    # evt: { type: "start"|"progress"|"finish"|"error", batch_size, entity_type, message, meta }
    print("EVENT:", evt)

result = await agent.screen(
    neighbors=["John Doe","Acme Farms LLC"],
    county="Napa", state="CA", tech="solar",
    on_event=on_event
)
```

- In **Responses mode**, we emit coarse events at batch start/finish and errors.
- In a future **Agents SDK engine**, we'll forward fine-grained streaming/tool events and traces through the same hook.

## Parcel API (NeighborFinder)

`agents/neighbor_finder.py` is a stub where we'll integrate the ReGrid (or similar) parcel API:

- **Input:** lat, lon, radius_mi.
- **Output:** a deduped list of `{ "name": "...", "entity_type": "person"|"organization" }`.
- **Heuristics:** if the API returns "LLC/Inc/Trust/Authority/Church/School/Township/County", set `entity_type="organization"`; otherwise default to `"person"` (you can override with `entity_type_map`).

Until the API is wired, pass names in to skip this step.

## Disambiguation, safety, and compliance

- **Public information only.** No private rooms, no scraping behind logins.
- **No guessing.** If data isn't public or clear, return `unknown`.
- **Disambiguation:** when names collide, fill `disambiguation.candidates` and `disambiguation.method` (how we picked the match).
- **Sensitive data:** use age brackets, not DOB; mention family only when publicly surfaced (e.g., obituaries, local features).
- **Citations everywhere:** every factual claim should include a URL / title in `citations`.

## Error handling, batching, rate limits

- **Batching:** default 4 neighbors per model call (`settings.BATCH_SIZE`).
- **Cap:** limit to 20 neighbors (`settings.MAX_NEIGHBORS`) to prevent runaway cost in dense areas.
- **Concurrency:** orchestrator uses an async semaphore (`CONCURRENCY_LIMIT`) for parallel batches.
- **Failures:** a batch exception does not crash the run; results are partial, with events logged to `on_event`.
- **Parsing:** strict JSON is requested; if the model wraps it in code fences, we extract with a robust parser; then validate via Pydantic.
- **Back-pressure:** you can dial down `CONCURRENCY_LIMIT` or raise it based on model throughput and quotas.

## Testing plan

**Unit:**
- JSON parsing (plain & fenced) → valid Python object or clear exception
- Entity guesser (person vs org) → correct for common tokens
- Pydantic validation fails closed when fields are missing

**Integration:**
- 11 names → expect 3 batches (size 4 default → 4/4/3) and events on start/finish
- Known neighbor ("publicly vocal" case) → stance and citations appear
- Ambiguous name → disambiguation.candidates populated

**Perf:**
- Parallel vs sequential runtime, monitor token usage & cost envelopes

**Safety:**
- No DOBs or non-public personal details; unknowns remain `unknown`

## Agents SDK vs Orchestration (trade-offs)

We ship with orchestration-level Deep Research via the Responses API and a thin ResearchEngine adapter so you can swap in an Agents SDK engine later.

**Start with Orchestration (default):**
- Straightforward async batches; simple retries/backoff; predictable $ per run
- Minimal coupling; easy to flip models (o4-mini-deep-research vs o3)
- Great fit for batch screening of 10–20 neighbors

**Move to Agents SDK when you need:**
- Rich streaming of web-tool steps; tracing/observability out of the box
- Multi-agent handoffs (clarifier → researcher → summarizer)
- Mid-flow guardrails (typed outputs at step boundaries)

Because the orchestrator talks to an interface (`ResearchEngine`), switching stacks doesn't require touching batching, schemas, or prompts—just set `ENGINE_TYPE="agentsdk"` and provide the implementation.

## Roadmap

- **Parcel API integration (ReGrid):** populate owners automatically, dedupe, classify entity type.
- **Clips/Monitoring mode:** after an initial screening, keep watching key neighbors/groups; alert on new posts/LTEs/minutes.
- **SDK engine:** fully stream model/tool events, show progress UI, capture traces.
- **Second-pass drills:** when an org is identified as an energy developer or a church with strong networks, auto-spawn deeper targeted research.
- **Caching:** short-term cache by name+county+state to reduce repeat costs.
- **Engagement playbooks:** turn profile patterns into templated outreach recommendations.

## FAQ

**Q: How accurate is person vs organization classification?**  
We use parcel API metadata when available; otherwise a safe heuristic (LLC/Inc/Trust/Authority/Church/School, etc. → organization). You can override with `entity_type_map`.

**Q: What if the model can't find anything?**  
It returns `unknown` fields and zero claims; you still get a profile shell with `citations: []`.

**Q: Can we prioritize certain neighbors?**  
Yes—just pass a curated list for high-priority screening, or add a filter after parcel lookup.

**Q: Can we expand beyond 20 neighbors?**  
Yes, but beware runtime/cost. Consider two passes: top-10 by proximity/parcel size, then the rest as needed.

**Q: Will this replace our consultants?**  
Often it reduces consultant hours for initial screens and de-risks later work by revealing issues early. Many teams still use consultants for on-the-ground engagement and formal studies.

## Quick reference (code)

```python
from neighbor import NeighborAgent

def on_event(evt):  # optional streaming/tracing hook
    print(evt)

agent = NeighborAgent()

result = await agent.screen(
    neighbors=["Jane Smith", "Riverside Baptist Church", "North Valley Coop"],
    entity_type_map={"Riverside Baptist Church":"organization","North Valley Coop":"organization"},
    county="Somerset", state="PA", tech="transmission", radius_mi=0.5,
    on_event=on_event
)

# JSON result validated by NeighborResult (Pydantic)
print(result["runtime_minutes"], "minutes")
for p in result["neighbors"]:
    print(p["name"], p["risk_level"], "—", p["influence_level"])
```

---

**Source context:** This README is aligned with the planning document for the Neighbor Deep Research Agent (site neighbor screening), including goals, architecture, batching, prompts, outputs, and integration approach.