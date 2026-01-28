# src/neighbor/engines/gemini_engine.py
"""Gemini Deep Research engine for initial neighbor profiling.

Uses Google's Gemini Interactions API (same as verification) but for
the initial deep research phase instead of OpenAI.

Follows the same debug-file pattern as verification:
1. Save raw Gemini interaction response to DEBUG JSON file
2. Parse neighbors from the debug file
3. Support resuming from existing debug files
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Literal, Callable

from google import genai
from pydantic import ValidationError

from ..config.settings import settings
from ..config.prompts import PERSON_SYSTEM, ORG_SYSTEM
from ..models.schemas import NeighborProfile
from ..utils.json_parse import extract_fenced_blocks
from .base import ResearchEngine, ResearchEvent

logger = logging.getLogger(__name__)


class GeminiDeepResearchEngine(ResearchEngine):
    """Gemini Deep Research engine for initial neighbor profiling."""

    def __init__(self, model: Optional[str] = None):
        """Initialize the Gemini client.

        Args:
            model: Gemini model to use. Defaults to deep-research-pro-preview-12-2025.
        """
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY or GOOGLE_API_KEY environment variable required"
            )
        self.client = genai.Client(api_key=api_key)
        self.model = model or "deep-research-pro-preview-12-2025"
        self.poll_interval = settings.GEMINI_POLL_INTERVAL
        self.max_wait_time = settings.GEMINI_MAX_WAIT_TIME

        # Output directories
        self.debug_dir = Path(__file__).parent.parent / "deep_research_outputs"
        self.debug_dir.mkdir(parents=True, exist_ok=True)

    def _build_combined_input(
        self,
        names: List[Any],
        context: Dict[str, Any],
        entity_type: Literal["person", "organization"],
    ) -> str:
        """Build the combined input for Gemini Deep Research.

        Combines system prompt + user query into a single input string,
        preserving all context from the OpenAI version.

        Args:
            names: List of neighbor names (strings or dicts with name/pins)
            context: Dict with county, state, city
            entity_type: "person" or "organization"

        Returns:
            Combined input string for Gemini
        """
        # Select system prompt based on entity type
        system_prompt = PERSON_SYSTEM if entity_type == "person" else ORG_SYSTEM

        # Build neighbors list (same logic as responses_engine.py)
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

        # Build location string (same logic as responses_engine.py)
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

        # Build user query (same as responses_engine.py)
        user_query = f"""Research the landowners for the parcels to identify their stance on development and standing within the community.

Location: {location_str}

Neighbors to profile ({entity_type}s):
{neighbors_list}

Follow the OUTPUT format and example provided in your instructions above."""

        # Combine system prompt + user query into single input
        # Add clear separation between instructions and task
        combined_input = f"""{system_prompt}

---

## YOUR TASK

{user_query}
"""
        return combined_input

    def _get_debug_file_path(self, entity_type: str, batch_index: Optional[int] = None) -> Path:
        """Generate a debug file path for saving raw Gemini response."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if batch_index is not None:
            filename = f"initial_DEBUG_{entity_type}s_batch{batch_index}_{timestamp}.json"
        else:
            filename = f"initial_DEBUG_{entity_type}s_{timestamp}.json"
        return self.debug_dir / filename

    def _find_existing_debug_files(self, entity_type: str, names: List[Any]) -> Optional[Path]:
        """Find existing DEBUG files that match this batch.

        Looks for initial_DEBUG_{entity_type}s_*.json files and checks if they
        contain the same neighbor names.
        """
        pattern = f"initial_DEBUG_{entity_type}s_*.json"
        debug_files = sorted(self.debug_dir.glob(pattern), reverse=True)

        # Get names from input for matching
        input_names = set()
        for item in names:
            if isinstance(item, dict):
                input_names.add(item.get("name", "").lower().strip())
            else:
                input_names.add(str(item).lower().strip())

        for debug_file in debug_files:
            try:
                with open(debug_file, "r", encoding="utf-8") as f:
                    debug_data = json.load(f)

                # Check if this debug file has matching names
                cached_names = set()
                for item in debug_data.get("input_names", []):
                    if isinstance(item, dict):
                        cached_names.add(item.get("name", "").lower().strip())
                    else:
                        cached_names.add(str(item).lower().strip())

                # If names match, we can use this debug file
                if input_names == cached_names:
                    return debug_file

            except Exception:
                continue

        return None

    def _save_debug_file(
        self,
        debug_path: Path,
        interaction: Any,
        names: List[Any],
        context: Dict[str, Any],
        entity_type: str,
    ) -> None:
        """Save the raw Gemini interaction response to a debug file."""
        try:
            # Serialize full interaction response
            if hasattr(interaction, 'model_dump'):
                debug_data = interaction.model_dump()
            else:
                debug_data = {
                    attr: getattr(interaction, attr)
                    for attr in dir(interaction)
                    if not attr.startswith('_') and not callable(getattr(interaction, attr))
                }

            # Add our tracking fields
            debug_data["input_names"] = names
            debug_data["location_context"] = context
            debug_data["entity_type"] = entity_type
            debug_data["engine"] = "gemini"
            debug_data["model"] = self.model
            debug_data["saved_at"] = datetime.now().isoformat()

            with open(debug_path, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, indent=2, ensure_ascii=False, default=str)

            print(f"   📄 Saved interaction debug to: {debug_path.name}")

        except Exception as e:
            print(f"   ⚠️ Failed to save interaction debug: {e}")
            raise

    def _extract_text_from_debug(self, debug_data: Dict[str, Any]) -> tuple[Optional[str], List[str]]:
        """Extract text content and thinking summaries from debug data.

        Returns:
            Tuple of (text_content, thinking_summaries)
        """
        text_content = None
        thinking_summaries = []

        outputs = debug_data.get("outputs", [])

        for output in outputs:
            output_type = output.get("type")

            if output_type == 'thought' and output.get("summary"):
                summary = output["summary"]
                if isinstance(summary, list):
                    for item in summary:
                        if isinstance(item, dict) and item.get("text"):
                            thinking_summaries.append(item["text"])
                        elif isinstance(item, str):
                            thinking_summaries.append(item)
                elif isinstance(summary, str):
                    thinking_summaries.append(summary)

            elif output_type == 'text' and output.get("text"):
                text_content = output["text"]

        # Fallback to last output if no text type found
        if not text_content and outputs:
            last_output = outputs[-1]
            if isinstance(last_output, dict):
                text_content = last_output.get("text")

        return text_content, thinking_summaries

    def _parse_neighbors_from_text(
        self,
        text: str,
        entity_type: str,
        on_event: Optional[Callable[[ResearchEvent], None]] = None,
    ) -> tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
        """Parse neighbors from Gemini text output.

        Returns:
            Tuple of (neighbors, overview_summary, markdown_debug)
        """
        overview_summary = None
        neighbors = []
        markdown_debug = None

        try:
            # Extract first fenced JSON block
            parsed_json, markdown_debug = extract_fenced_blocks(text)

            if not isinstance(parsed_json, dict):
                raise ValueError(f"Expected JSON object, got {type(parsed_json)}")

            overview_summary = parsed_json.get("overview_summary")
            raw_neighbors = parsed_json.get("neighbors", [])

            # Validate each neighbor profile with Pydantic
            validated_neighbors = []
            for idx, neighbor_data in enumerate(raw_neighbors):
                try:
                    # Map entity_type value to match schema if needed
                    if "entity_type" in neighbor_data:
                        old_entity_type = neighbor_data.get("entity_type")
                        if old_entity_type == "person":
                            neighbor_data["entity_type"] = "individual"
                        elif old_entity_type == "organization":
                            neighbor_data["entity_type"] = "other"

                    # Ensure required fields have defaults
                    if "neighbor_id" not in neighbor_data:
                        neighbor_data["neighbor_id"] = f"N-{idx + 1:02d}"

                    # Set entity_category based on entity_type parameter
                    neighbor_data["entity_category"] = (
                        "Resident" if entity_type == "person" else "Organization"
                    )

                    # Handle claims
                    if "claims" not in neighbor_data:
                        neighbor_data["claims"] = neighbor_data.get(
                            "profile_summary", "No information available."
                        )

                    # Handle noted_stance - normalize case
                    if "noted_stance" in neighbor_data and neighbor_data["noted_stance"]:
                        stance = neighbor_data["noted_stance"].lower()
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

                    # Handle approach_recommendations
                    approach = neighbor_data.get("approach_recommendations")
                    if approach and not isinstance(approach, dict):
                        neighbor_data["approach_recommendations"] = {
                            "motivations": [],
                            "engage": approach,
                        }

                    # Validate with Pydantic
                    profile = NeighborProfile(**neighbor_data)
                    validated_neighbors.append(profile.dict())

                except ValidationError as ve:
                    if on_event:
                        on_event({
                            "type": "warning",
                            "batch_size": 0,
                            "entity_type": entity_type,
                            "message": f"Validation error for neighbor {idx}: {ve}",
                            "meta": {"neighbor_name": neighbor_data.get("name", "unknown")},
                        })
                    validated_neighbors.append(neighbor_data)

            neighbors = validated_neighbors

        except (ValueError, KeyError, AttributeError) as e:
            # Save raw text for debugging
            raw_output_path = self.debug_dir / f"gemini_raw_output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(raw_output_path, "w", encoding="utf-8") as f:
                f.write(f"=== GEMINI RAW OUTPUT ===\n")
                f.write(f"Length: {len(text)} chars\n")
                f.write(f"Error: {e}\n\n")
                f.write(f"=== FULL CONTENT ===\n{text}")

            print(f"   ❌ Failed to parse neighbors: {e}")
            print(f"   📄 Raw output saved to: {raw_output_path.name}")

            if on_event:
                on_event({
                    "type": "error",
                    "batch_size": 0,
                    "entity_type": entity_type,
                    "message": f"Failed to parse response: {str(e)}",
                    "meta": {"raw_text_sample": text[:500] if text else ""},
                })

        return neighbors, overview_summary, markdown_debug

    async def run_batch(
        self,
        names: List[Any],
        context: Dict[str, Any],
        entity_type: Literal["person", "organization"],
        on_event: Optional[Callable[[ResearchEvent], None]] = None,
        max_retries: int = 2,
        _retry_count: int = 0,
    ) -> Dict[str, Any]:
        """Run a batch of neighbor research using Gemini Deep Research.

        Args:
            names: List of neighbor names (strings or dicts with name/pins)
            context: Dict with county, state, city
            entity_type: "person" or "organization"
            on_event: Optional callback for progress events
            max_retries: Max retry attempts for failed requests
            _retry_count: Internal retry counter

        Returns:
            Dict with neighbors, annotations, raw_text, overview_summary
        """
        if on_event:
            on_event({
                "type": "start",
                "batch_size": len(names),
                "entity_type": entity_type,
                "message": "batch start (Gemini)",
                "meta": {},
            })

        county = context.get("county", "Unknown")
        state = context.get("state", "Unknown")

        print(f"\n{'=' * 70}")
        print(f"🔍 GEMINI DEEP RESEARCH - NEIGHBOR PROFILING ({entity_type.upper()})")
        print(f"{'=' * 70}")
        print(f"   Location: {county}, {state}")
        print(f"   Entity type: {entity_type}")
        print(f"   Neighbors to profile: {len(names)}")
        print(f"   Model: {self.model}")

        # Check for existing DEBUG file that matches this batch
        existing_debug = self._find_existing_debug_files(entity_type, names)

        if existing_debug:
            print(f"   📂 Found existing DEBUG file: {existing_debug.name}")
            print(f"   🔄 Attempting to parse from cached response...")

            try:
                with open(existing_debug, "r", encoding="utf-8") as f:
                    debug_data = json.load(f)

                text_content, thinking_summaries = self._extract_text_from_debug(debug_data)

                if text_content:
                    neighbors, overview_summary, markdown_debug = self._parse_neighbors_from_text(
                        text_content, entity_type, on_event
                    )

                    if neighbors:
                        print(f"   ✅ Parsed {len(neighbors)} neighbors from cached DEBUG file")

                        # Clean up citations
                        result = {
                            "neighbors": neighbors,
                            "annotations": [],
                            "raw_text": text_content,
                            "overview_summary": overview_summary,
                            "markdown_debug": markdown_debug,
                            "thinking_summaries": thinking_summaries,
                        }
                        result = self._cleanup_lenticular_citations(result)

                        # Save as dr_*.json
                        saved_filepath = self._save_deep_research_response(
                            result=result,
                            entity_type=entity_type,
                            batch_size=len(names),
                            context=context,
                        )

                        if saved_filepath:
                            result["saved_filepath"] = str(saved_filepath)

                        # Trash the DEBUG file since we successfully parsed it
                        import subprocess
                        subprocess.run(["trash", str(existing_debug)], check=True)
                        print(f"   🗑️ Trashed parsed DEBUG file")

                        if on_event:
                            on_event({
                                "type": "finish",
                                "batch_size": len(names),
                                "entity_type": entity_type,
                                "message": "batch done (from cache)",
                                "meta": {"neighbors": len(neighbors)},
                            })

                        return result
                    else:
                        print(f"   ⚠️ Could not parse neighbors from cached DEBUG, will retry API call")

            except Exception as e:
                print(f"   ⚠️ Failed to use cached DEBUG: {e}")

        # Build combined input
        combined_input = self._build_combined_input(names, context, entity_type)
        print(f"   📄 Input size: {len(combined_input)} characters")

        # Print the user query portion for debugging
        print("-" * 60)
        print("📝 Task portion of input:")
        task_start = combined_input.find("## YOUR TASK")
        if task_start != -1:
            print(combined_input[task_start:])
        print("-" * 60)

        try:
            # Start Deep Research task
            print(f"   🚀 Sending to Gemini Deep Research API...")

            interaction = self.client.interactions.create(
                input=combined_input,
                agent=self.model,
                background=True,
                store=True,
                agent_config={
                    "type": "deep-research",
                    "thinking_summaries": "auto",
                },
            )

            interaction_id = interaction.id
            print(f"   📡 Interaction started with ID: {interaction_id}")
            print(f"   ⏳ Polling for completion (max {self.max_wait_time // 60} minutes)...")

            # Poll for completion
            start_time = time.time()
            last_status = None

            while True:
                elapsed = time.time() - start_time
                if elapsed > self.max_wait_time:
                    error_msg = f"Timeout after {self.max_wait_time // 60} minutes"
                    print(f"   ❌ {error_msg}")
                    if on_event:
                        on_event({
                            "type": "error",
                            "batch_size": len(names),
                            "entity_type": entity_type,
                            "message": error_msg,
                            "meta": {},
                        })
                    raise Exception(error_msg)

                interaction = self.client.interactions.get(interaction_id)
                status = interaction.status

                if status != last_status:
                    print(f"   📊 Status: {status} ({int(elapsed)}s elapsed)")
                    last_status = status

                if status == "completed":
                    break
                elif status in ["failed", "cancelled"]:
                    error_msg = getattr(interaction, "error", None) or f"Status: {status}"
                    print(f"   ❌ {error_msg}")
                    if on_event:
                        on_event({
                            "type": "error",
                            "batch_size": len(names),
                            "entity_type": entity_type,
                            "message": str(error_msg),
                            "meta": {},
                        })
                    raise Exception(str(error_msg))

                time.sleep(self.poll_interval)

            # Save raw interaction to DEBUG file FIRST (before any parsing)
            debug_path = self._get_debug_file_path(entity_type)
            self._save_debug_file(debug_path, interaction, names, context, entity_type)

            # Now parse from the saved DEBUG file (consistent with verification pattern)
            with open(debug_path, "r", encoding="utf-8") as f:
                debug_data = json.load(f)

            text_content, thinking_summaries = self._extract_text_from_debug(debug_data)

            if not text_content:
                raise Exception("Empty output from Gemini Deep Research")

            print(f"   📊 Response received: {len(text_content)} characters")

            # Parse neighbors from text
            neighbors, overview_summary, markdown_debug = self._parse_neighbors_from_text(
                text_content, entity_type, on_event
            )

            result = {
                "neighbors": neighbors,
                "annotations": [],  # Gemini doesn't provide annotations like OpenAI
                "raw_text": text_content,
                "overview_summary": overview_summary,
                "markdown_debug": markdown_debug,
                "thinking_summaries": thinking_summaries,
            }

            # Clean up lenticular bracket citations
            result = self._cleanup_lenticular_citations(result)

            # Save as dr_*.json for verification stage
            saved_filepath = self._save_deep_research_response(
                result=result,
                entity_type=entity_type,
                batch_size=len(names),
                context=context,
            )

            if saved_filepath:
                result["saved_filepath"] = str(saved_filepath)

            # If we successfully parsed neighbors, trash the DEBUG file
            if neighbors:
                import subprocess
                subprocess.run(["trash", str(debug_path)], check=True)
                print(f"   🗑️ Trashed parsed DEBUG file")
            else:
                print(f"   ⚠️ DEBUG file kept for manual inspection: {debug_path.name}")

            print(f"\n{'=' * 70}")
            print("✅ GEMINI NEIGHBOR PROFILING COMPLETE")
            print(f"{'=' * 70}")
            print(f"   Profiles generated: {len(neighbors)}")
            print(f"   Thinking summaries: {len(thinking_summaries)}")

            if on_event:
                on_event({
                    "type": "finish",
                    "batch_size": len(names),
                    "entity_type": entity_type,
                    "message": "batch done (Gemini)",
                    "meta": {"neighbors": len(neighbors)},
                })

            return result

        except Exception as e:
            print(f"❌ Gemini Deep Research failed: {e}")
            import traceback
            traceback.print_exc()

            if on_event:
                on_event({
                    "type": "error",
                    "batch_size": len(names),
                    "entity_type": entity_type,
                    "message": str(e),
                    "meta": {},
                })
            raise

    def _cleanup_lenticular_citations(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Fix malformed lenticular bracket citations to proper markdown format."""
        neighbors = result.get("neighbors", [])
        fixed_count = 0
        zero_width_chars = '\u200b\u200c\u200d\ufeff'

        for neighbor in neighbors:
            claims = neighbor.get("claims", "")
            if not claims:
                continue

            original = claims

            # Fix lenticular bracket combinations with URLs
            claims = re.sub(r'【([^】]+)】\(([^)]+)\)', r'[\1](\2)', claims)
            claims = re.sub(r'【([^\]]+)\]\(([^)]+)\)', r'[\1](\2)', claims)
            claims = re.sub(r'\[([^】]+)】\(([^)]+)\)', r'[\1](\2)', claims)

            # Remove fully lenticular format with no URL
            claims = re.sub(r'【[^】]+】', '', claims)

            # Strip zero-width characters
            for char in zero_width_chars:
                claims = claims.replace(char, '')

            # Clean up double spaces
            claims = re.sub(r'  +', ' ', claims)
            claims = claims.strip()

            if claims != original:
                fixed_count += 1
                neighbor["claims"] = claims

        if fixed_count > 0:
            print(f"[DEBUG] Fixed lenticular citations in {fixed_count} neighbor(s)")

        return result

    def _save_deep_research_response(
        self,
        result: Dict[str, Any],
        entity_type: str,
        batch_size: int,
        context: Dict[str, Any],
    ) -> Optional[Path]:
        """Save deep research response to dr_*.json file for verification stage."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:20]
            filename = f"dr_{entity_type}s_{timestamp}.json"
            filepath = self.debug_dir / filename

            save_data = {
                "timestamp": datetime.now().isoformat(),
                "engine": "gemini",
                "model": self.model,
                "entity_type": entity_type,
                "batch_size": batch_size,
                "location_context": context,
                "overview_summary": result.get("overview_summary"),
                "neighbors": result.get("neighbors", []),
                "annotations": result.get("annotations", []),
                "raw_text": result.get("raw_text", ""),
                "markdown_debug": result.get("markdown_debug", ""),
                "thinking_summaries": result.get("thinking_summaries", []),
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)

            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 💾 Saved dr_*.json to: {filepath.name}")
            return filepath

        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⚠️ Failed to save dr_*.json: {e}")
            return None
