# src/ii_agent/tools/neighbor/engines/responses_engine_improved.py
import asyncio
import json
import os
import time
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
import logging

logger = logging.getLogger(__name__)


class DeepResearchResponsesEngine(ResearchEngine):
    def __init__(
        self, client: Optional[AsyncOpenAI] = None, model: Optional[str] = None
    ):
        webhook_url = os.getenv("OPENAI_WEBHOOK_URL", "").strip('"')
        default_headers = (
            {"OpenAI-Notification-Url": webhook_url} if webhook_url else {}
        )
        self.client = client or AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=120.0,  # Reduced timeout for initial call
            default_headers=default_headers,
        )
        self.model = model or settings.DR_MODEL

    async def _check_webhook_server_health(self) -> bool:
        """Check if webhook server is running and healthy."""
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get("http://localhost:8080/", timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"✅ Webhook server healthy: {data}")
                        return True
        except Exception as e:
            logger.error(f"❌ Webhook server not accessible: {e}")
            return False

    async def _create_deep_research_with_retry(
        self,
        system_prompt: str,
        user_query: str,
        max_retries: int = 3,
        initial_delay: float = 5.0,
    ) -> Any:
        """Create deep research request with retry logic."""
        delay = initial_delay
        last_error = None

        for attempt in range(max_retries):
            try:
                logger.info(
                    f"🔄 Attempt {attempt + 1}/{max_retries} to create deep research request"
                )

                # Use a shorter timeout for the initial API call
                webhook_url = os.getenv("OPENAI_WEBHOOK_URL", "").strip('"')
                default_headers = (
                    {"OpenAI-Notification-Url": webhook_url} if webhook_url else {}
                )
                temp_client = AsyncOpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    timeout=30.0,  # 30 second timeout for initial call
                    default_headers=default_headers,
                )

                resp = await temp_client.responses.create(
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

                # Check if we got a response ID
                if hasattr(resp, "id") and resp.id:
                    logger.info(
                        f"✅ Successfully created deep research request: {resp.id}"
                    )
                    return resp
                else:
                    raise ValueError(f"Invalid response from OpenAI: no ID returned")

            except asyncio.TimeoutError:
                last_error = "Timeout creating deep research request"
                logger.error(f"⏱️ Timeout on attempt {attempt + 1}: {last_error}")
            except Exception as e:
                last_error = str(e)
                logger.error(f"❌ Error on attempt {attempt + 1}: {last_error}")

            if attempt < max_retries - 1:
                logger.info(f"⏳ Waiting {delay} seconds before retry...")
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff

        raise Exception(
            f"Failed to create deep research after {max_retries} attempts. Last error: {last_error}"
        )

    async def _poll_response_status(
        self,
        response_id: str,
        max_wait_minutes: int = 45,
        poll_interval_seconds: int = 30,
    ) -> Dict[str, Any]:
        """Poll OpenAI API directly for response status as fallback."""
        start_time = time.time()
        max_wait_seconds = max_wait_minutes * 60

        logger.info(f"📊 Starting polling fallback for {response_id}")

        while (time.time() - start_time) < max_wait_seconds:
            try:
                response = await self.client.responses.retrieve(response_id)
                status = getattr(response, "status", "unknown")

                logger.info(
                    f"📊 Poll result - Status: {status} (elapsed: {int(time.time() - start_time)}s)"
                )

                if status == "completed":
                    logger.info(f"✅ Response completed via polling: {response_id}")
                    return await self._process_completed_response(response)
                elif status == "failed":
                    logger.error(f"❌ Response failed: {response_id}")
                    raise Exception(f"Deep research failed with status: {status}")
                elif status == "cancelled":
                    logger.error(f"⏹️ Response cancelled: {response_id}")
                    raise Exception(f"Deep research cancelled")
                elif status in ["queued", "in_progress"]:
                    # Still processing, continue polling
                    logger.info(f"⏳ Response still {status}, continuing to poll...")
                else:
                    logger.warning(f"⚠️ Unknown status: {status}")

            except Exception as e:
                logger.error(f"❌ Error polling response: {e}")

            await asyncio.sleep(poll_interval_seconds)

        raise Exception(
            f"Timeout after {max_wait_minutes} minutes waiting for response {response_id}"
        )

    async def _process_completed_response(self, response: Any) -> Dict[str, Any]:
        """Process a completed OpenAI response."""
        if not response.output:
            raise ValueError("No output in response")

        output_text = response.output[-1].content[0].text

        # Extract citations
        annotations = response.output[-1].content[0].annotations
        citations = []
        for i, citation in enumerate(annotations):
            citations.append(
                {
                    "index": i + 1,
                    "title": citation.title,
                    "url": citation.url,
                    "start_index": citation.start_index,
                    "end_index": citation.end_index,
                }
            )

        return {
            "raw_output": output_text,
            "citations": citations,
            "status": "completed",
        }

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

        # Check webhook server health first
        webhook_healthy = await self._check_webhook_server_health()
        if not webhook_healthy:
            logger.warning("⚠️ Webhook server not healthy, will rely on polling")

        system_prompt = PERSON_SYSTEM if entity_type == "person" else ORG_SYSTEM

        # Build concise user prompt with context only
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

        # Build location string
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

        # Create the Deep Research task with retry logic
        try:
            resp = await self._create_deep_research_with_retry(
                system_prompt=system_prompt, user_query=user_query
            )
        except Exception as e:
            logger.error(f"❌ Failed to create deep research request: {e}")
            raise

        # Handle background mode
        if getattr(resp, "status", None) == "queued":
            response_id = resp.id
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 📡 Neighbor research queued with response ID: {response_id}"
            )

            # Try webhook first if server is healthy
            if webhook_healthy:
                # Register with webhook manager and wait
                await webhook_manager.register_callback(
                    response_id, f"neighbor_{entity_type}"
                )
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⏳ Waiting for webhook callback for {len(names)} {entity_type}s..."
                )

                # Wait for webhook with shorter timeout (20 minutes)
                webhook_result = await webhook_manager.wait_for_webhook(
                    response_id, timeout=1200
                )

                if webhook_result.get("status") == "completed":
                    print(
                        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Webhook completed for {len(names)} {entity_type}s"
                    )
                    final_result = await webhook_manager.retrieve_response(response_id)

                    if final_result and "raw_output" in final_result:
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
                else:
                    logger.warning(
                        f"⚠️ Webhook failed or timed out, falling back to polling"
                    )
                    # Fall through to polling
                    result = await self._poll_response_status(response_id)
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
                                                    "text": result["raw_output"],
                                                    "annotations": result.get(
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
                # Webhook server not healthy, go straight to polling
                result = await self._poll_response_status(response_id)
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
                                                "text": result["raw_output"],
                                                "annotations": result.get(
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

        final = resp.output[-1].content[0]
        text = final.text
        annotations = getattr(final, "annotations", []) or []

        overview_summary = None
        neighbors = []
        markdown_debug = None

        try:
            # Extract first fenced JSON block
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
                    # Map entity_type value
                    if "entity_type" in neighbor_data:
                        old_entity_type = neighbor_data.get("entity_type")
                        if old_entity_type == "person":
                            neighbor_data["entity_type"] = "individual"
                        elif old_entity_type == "organization":
                            neighbor_data["entity_type"] = "other"

                    # Ensure required fields
                    if "neighbor_id" not in neighbor_data:
                        neighbor_data["neighbor_id"] = f"N-{idx + 1:02d}"

                    neighbor_data["entity_category"] = (
                        "Resident" if entity_type == "person" else "Organization"
                    )

                    # Handle claims - ensure it's a string
                    if "claims" not in neighbor_data:
                        neighbor_data["claims"] = neighbor_data.get(
                            "profile_summary", "No information available."
                        )

                    # Handle noted_stance - normalize case
                    if (
                        "noted_stance" in neighbor_data
                        and neighbor_data["noted_stance"]
                    ):
                        stance = neighbor_data["noted_stance"].lower()
                        # Map any variation to valid enum
                        if stance in ["support", "oppose", "neutral", "unknown"]:
                            neighbor_data["noted_stance"] = stance
                        else:
                            neighbor_data["noted_stance"] = "unknown"

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
                    validated_neighbors.append(neighbor_data)

            neighbors = validated_neighbors
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
            + citations_flat,
            "raw_text": text,
            "overview_summary": overview_summary,
            "markdown_debug": markdown_debug,
        }

        # Save deep research response to JSON file
        saved_filepath = self._save_deep_research_response(
            result=result,
            entity_type=entity_type,
            batch_size=len(names),
            context=context,
        )

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
            output_dir = Path(__file__).parent.parent / "deep_research_outputs"
            output_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:20]
            filename = f"dr_{entity_type}s_{timestamp}.json"
            filepath = output_dir / filename

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
