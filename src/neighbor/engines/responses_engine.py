# src/ii_agent/tools/neighbor/engines/responses_engine.py
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Literal, Callable
from openai import AsyncOpenAI
from pydantic import ValidationError
from ..config.settings import settings
from ..config.prompts import PERSON_SYSTEM, ORG_SYSTEM
from ..models.schemas import NeighborProfile
from ..utils.json_parse import extract_fenced_blocks
from .base import ResearchEngine, ResearchEvent
from ..webhook_manager import webhook_manager


class DeepResearchResponsesEngine(ResearchEngine):
    def __init__(
        self, client: Optional[AsyncOpenAI] = None, model: Optional[str] = None
    ):
        webhook_url = os.getenv("OPENAI_WEBHOOK_URL", "").strip('"')
        default_headers = (
            {"OpenAI-Notification-Url": webhook_url} if webhook_url else {}
        )
        print(f"[DEBUG] DeepResearchResponsesEngine init:")
        print(f"  webhook_url: {webhook_url}")
        print(f"  default_headers: {default_headers}")
        self.client = client or AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=600.0,
            default_headers=default_headers,
        )
        self.model = model or settings.DR_MODEL

    async def run_batch(
        self,
        names: List[Any],  # Can be strings or dicts with name/pins
        context: Dict[str, Any],
        entity_type: Literal["person", "organization"],
        on_event: Optional[Callable[[ResearchEvent], None]] = None,
    ) -> Dict[str, Any]:
        if on_event:
            on_event(
                {
                    "type": "start",
                    "batch_size": len(names),
                    "entity_type": entity_type,
                    "message": "batch start",
                    "meta": {},
                }
            )

        system_prompt = PERSON_SYSTEM if entity_type == "person" else ORG_SYSTEM

        # Build concise user prompt with context only
        # Handle both string names and dict with name/pins
        neighbors_lines = []
        for item in names:
            if isinstance(item, dict):
                name = item.get("name", "Unknown")
                pins = item.get("pins", [])
                if pins:
                    pins_str = ", ".join(pins) if isinstance(pins, list) else str(pins)
                    neighbors_lines.append(f"- {name} (PINs: {pins_str})")
                else:
                    neighbors_lines.append(f"- {name}")
            else:
                neighbors_lines.append(f"- {item}")

        neighbors_list = "\n".join(neighbors_lines)
        # Build location string with city and/or county
        city = context.get("city", "")
        county = context.get("county", "")
        state = context.get("state", "")

        if not state:
            raise ValueError("State is required for location context")

        if city and county:
            location_str = f"{city}, {county}, {state}"
        elif city:
            location_str = f"{city}, {state}"
        elif county:
            location_str = f"{county}, {state}"
        else:
            raise ValueError("Either city or county is required for location context")

        user_query = f"""Research the landowners for the parcels to identify their stance on development and standing within the community.

Location: {location_str}

Neighbors to profile ({entity_type}s):
{neighbors_list}

Follow the OUTPUT format and example provided in your instructions above."""

        # Print the user query for debugging
        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 📝 User query for {len(names)} {entity_type}s:"
        )
        print("-" * 60)
        print(user_query)
        print("-" * 60)

        # Start the Deep Research task in background mode with webhooks
        try:
            resp = await self.client.responses.create(
                model=self.model,
                input=[
                    {
                        "role": "developer",
                        "content": [{"type": "input_text", "text": system_prompt}],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": user_query}],
                    },
                ],
                reasoning={"summary": "detailed"},
                tools=[{"type": "web_search_preview"}],
                background=True,  # Enable async/background mode for webhooks
            )
        except Exception as e:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ❌ OpenAI API call failed: {e}"
            )
            raise

        # Handle background mode with webhooks
        if getattr(resp, "status", None) == "queued":
            response_id = resp.id
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 📡 Neighbor research queued with response ID: {response_id}"
            )

            # Register with webhook manager and wait
            await webhook_manager.register_callback(
                response_id, f"neighbor_{entity_type}"
            )
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⏳ Waiting for webhook callback for {len(names)} {entity_type}s..."
            )

            # Wait for webhook (30 minutes timeout for Deep Research)
            webhook_result = await webhook_manager.wait_for_webhook(
                response_id, timeout=1800
            )

            if webhook_result.get("status") == "completed":
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Webhook completed for {len(names)} {entity_type}s (ID: {response_id[:20]}...)"
                )
                # Retrieve the full response
                final_result = await webhook_manager.retrieve_response(response_id)

                # Extract the content from the webhook response
                if final_result and "raw_output" in final_result:
                    # Create a mock response object with the webhook data
                    resp = type(
                        "Response",
                        (),
                        {
                            "output": [
                                type(
                                    "Output",
                                    (),
                                    {
                                        "content": [
                                            type(
                                                "Content",
                                                (),
                                                {
                                                    "text": final_result["raw_output"],
                                                    "annotations": final_result.get(
                                                        "citations", []
                                                    ),
                                                },
                                            )()
                                        ]
                                    },
                                )()
                            ]
                        },
                    )()
                else:
                    raise Exception(
                        f"Invalid response structure from webhook: {final_result}"
                    )
            elif webhook_result.get("status") == "timeout":
                # Webhook timed out - try direct retrieval as fallback
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⏱️ Webhook timeout for {len(names)} {entity_type}s - attempting direct retrieval"
                )

                # Try to retrieve the response directly since OpenAI has a webhook bug
                try:
                    final_result = await webhook_manager.retrieve_response(response_id)

                    # Check if we got a valid response
                    if final_result and "raw_output" in final_result:
                        print(
                            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Successfully retrieved response via polling for {len(names)} {entity_type}s (ID: {response_id})"
                        )

                        # Create a mock response object with the retrieved data
                        resp = type(
                            "Response",
                            (),
                            {
                                "output": [
                                    type(
                                        "Output",
                                        (),
                                        {
                                            "content": [
                                                type(
                                                    "Content",
                                                    (),
                                                    {
                                                        "text": final_result[
                                                            "raw_output"
                                                        ],
                                                        "annotations": final_result.get(
                                                            "citations", []
                                                        ),
                                                    },
                                                )
                                            ]
                                        },
                                    )
                                ]
                            },
                        )
                    else:
                        raise Exception(
                            f"Response not ready or invalid after timeout: {final_result}"
                        )
                except Exception as e:
                    print(
                        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ❌ Failed to retrieve response after timeout: {str(e)}"
                    )
                    raise Exception(
                        f"Webhook timeout and fallback retrieval failed for {entity_type} batch: {str(e)}"
                    )
            else:
                # Other error cases
                raise Exception(
                    f"Webhook failed for {entity_type} batch: {webhook_result.get('error', 'Unknown error')}"
                )

        final = resp.output[-1].content[0]
        text = final.text
        annotations = getattr(final, "annotations", []) or []

        overview_summary = None
        neighbors = []
        markdown_debug = None

        try:
            # Extract first fenced JSON block (and optionally markdown for debug)
            parsed_json, markdown_debug = extract_fenced_blocks(text)

            if not isinstance(parsed_json, dict):
                raise ValueError(f"Expected JSON object, got {type(parsed_json)}")

            # Extract overview and neighbors
            overview_summary = parsed_json.get("overview_summary")
            raw_neighbors = parsed_json.get("neighbors", [])

            # Validate each neighbor profile with Pydantic
            validated_neighbors = []
            for idx, neighbor_data in enumerate(raw_neighbors):
                try:
                    # Map entity_type value to match new schema if needed
                    if "entity_type" in neighbor_data:
                        old_entity_type = neighbor_data.get("entity_type")
                        # Handle legacy entity_type values
                        if old_entity_type == "person":
                            neighbor_data["entity_type"] = "individual"
                        elif old_entity_type == "organization":
                            # Try to infer the org type from other fields or default to "other"
                            neighbor_data["entity_type"] = "other"

                    # Ensure required fields have defaults if missing
                    if "neighbor_id" not in neighbor_data:
                        neighbor_data["neighbor_id"] = f"N-{idx + 1:02d}"

                    # Always set entity_category based on entity_type parameter
                    neighbor_data["entity_category"] = (
                        "Resident" if entity_type == "person" else "Organization"
                    )

                    # Handle claims - ensure it's a string
                    if "claims" not in neighbor_data:
                        neighbor_data["claims"] = neighbor_data.get(
                            "profile_summary", "No information available."
                        )

                    # Handle noted_stance - normalize case to capitalized
                    if (
                        "noted_stance" in neighbor_data
                        and neighbor_data["noted_stance"]
                    ):
                        stance = neighbor_data["noted_stance"].lower()
                        # Map any variation to valid enum with proper capitalization
                        if stance in ["support", "oppose", "neutral", "unknown"]:
                            neighbor_data["noted_stance"] = stance.capitalize()
                        else:
                            neighbor_data["noted_stance"] = "Unknown"

                    # Handle confidence - normalize case
                    if "confidence" in neighbor_data and neighbor_data["confidence"]:
                        conf = neighbor_data["confidence"].lower()
                        if conf in ["high", "medium", "low"]:
                            neighbor_data["confidence"] = conf
                        else:
                            neighbor_data["confidence"] = "medium"

                    # Handle approach_recommendations - ensure it's structured
                    approach = neighbor_data.get("approach_recommendations")
                    if approach and not isinstance(approach, dict):
                        # Legacy string format - convert to new structure
                        neighbor_data["approach_recommendations"] = {
                            "motivations": [],
                            "engage": approach,
                        }

                    # Validate with Pydantic
                    profile = NeighborProfile(**neighbor_data)
                    validated_neighbors.append(profile.dict())
                except ValidationError as ve:
                    # Log validation error but continue processing other neighbors
                    if on_event:
                        on_event(
                            {
                                "type": "warning",
                                "batch_size": len(names),
                                "entity_type": entity_type,
                                "message": f"Validation error for neighbor {idx}: {ve}",
                                "meta": {
                                    "neighbor_name": neighbor_data.get(
                                        "name", "unknown"
                                    )
                                },
                            }
                        )
                    # Still include the neighbor with defaults where validation failed
                    validated_neighbors.append(neighbor_data)

            neighbors = validated_neighbors

            # Store citations_flat if provided
            citations_flat = parsed_json.get("citations_flat", [])

        except (ValueError, KeyError, AttributeError) as e:
            if on_event:
                on_event(
                    {
                        "type": "error",
                        "batch_size": len(names),
                        "entity_type": entity_type,
                        "message": f"Failed to parse/validate response: {str(e)}",
                        "meta": {"raw_text_sample": text[:500] if text else ""},
                    }
                )
            neighbors = []
            citations_flat = []

        result = {
            "neighbors": neighbors,
            "annotations": [
                {"title": getattr(a, "title", None), "url": getattr(a, "url", None)}
                for a in annotations
            ]
            + citations_flat,  # Combine both annotation sources
            "raw_text": text,
            "overview_summary": overview_summary,
            "markdown_debug": markdown_debug,  # Optional, for logging only
        }

        # Save deep research response to JSON file
        saved_filepath = self._save_deep_research_response(
            result=result,
            entity_type=entity_type,
            batch_size=len(names),
            context=context,
        )

        # Add saved filepath to result for tracking
        if saved_filepath:
            result["saved_filepath"] = str(saved_filepath)
        if on_event:
            on_event(
                {
                    "type": "finish",
                    "batch_size": len(names),
                    "entity_type": entity_type,
                    "message": "batch done",
                    "meta": {"neighbors": len(neighbors)},
                }
            )
        return result

    def _save_deep_research_response(
        self,
        result: Dict[str, Any],
        entity_type: str,
        batch_size: int,
        context: Dict[str, Any],
    ) -> Optional[Path]:
        """Save deep research response to JSON file for debugging and analysis."""
        try:
            # Create output directory
            output_dir = Path(__file__).parent.parent / "deep_research_outputs"
            output_dir.mkdir(parents=True, exist_ok=True)

            # Generate filename with timestamp and entity type
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[
                :20
            ]  # Include microseconds for uniqueness
            filename = f"dr_{entity_type}s_{timestamp}.json"
            filepath = output_dir / filename

            # Prepare data to save (include context and metadata)
            save_data = {
                "timestamp": datetime.now().isoformat(),
                "entity_type": entity_type,
                "batch_size": batch_size,
                "location_context": context,
                "overview_summary": result.get("overview_summary"),
                "neighbors": result.get("neighbors", []),
                "annotations": result.get("annotations", []),
                "raw_text": result.get("raw_text", ""),
                "markdown_debug": result.get("markdown_debug", ""),
            }

            # Write to file
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)

            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 💾 Saved deep research response to: {filepath.name}"
            )
            return filepath

        except Exception as e:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⚠️  Failed to save deep research response: {e}"
            )
            return None
