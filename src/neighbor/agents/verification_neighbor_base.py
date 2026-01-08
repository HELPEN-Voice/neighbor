"""Base verification agent for Neighbor profiles using Gemini Deep Research.

This module provides the base class for verifying neighbor profiles using
Google's Gemini Deep Research API. It handles the common polling and
response parsing logic.
"""

import os
import re
import json
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

from google import genai

from ..config.settings import settings
from ..config.prompts import VERIFICATION_NEIGHBOR_SYSTEM


class NeighborVerificationAgent:
    """Base class for neighbor verification using Gemini Deep Research."""

    def __init__(self):
        """Initialize the Gemini client."""
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY or GOOGLE_API_KEY environment variable required"
            )
        self.client = genai.Client(api_key=api_key)
        self.name = "Verification Agent - Neighbor (Gemini Deep Research)"
        self.agent = "deep-research-pro-preview-12-2025"
        self.poll_interval = settings.GEMINI_POLL_INTERVAL
        self.max_wait_time = settings.GEMINI_MAX_WAIT_TIME

    def _get_system_prompt(self) -> str:
        """Get the system prompt for verification.

        Override in subclasses to add entity-specific prompts.
        """
        return VERIFICATION_NEIGHBOR_SYSTEM

    def _build_verification_input(
        self,
        profiles: List[Dict[str, Any]],
        context: Dict[str, Any],
        entity_type: str,
    ) -> str:
        """Build the full input for Gemini Deep Research.

        Args:
            profiles: List of neighbor profile dicts to verify
            context: Dict with county, state, city
            entity_type: "person" or "organization"

        Returns:
            Formatted input string for Gemini
        """
        county = context.get("county", "Unknown")
        state = context.get("state", "Unknown")
        current_date = datetime.now().strftime("%Y-%m-%d")

        # Format system prompt with context
        system_prompt = self._get_system_prompt().format(
            county=county,
            state=state,
            entity_type=entity_type,
        )

        # Format profiles as JSON
        profiles_json = json.dumps(profiles, indent=2, ensure_ascii=False)

        full_input = f"""{system_prompt}

---

## Verification Context
- County: {county}
- State: {state}
- Entity Type: {entity_type}
- Current Date: {current_date}
- Number of Profiles: {len(profiles)}

## Draft Profiles to Verify
```json
{profiles_json}
```

## Instructions
1. Verify ALL profiles above using web search
2. Apply the EDITING POLICY strictly: fix in place, remove incorrect data, mark "unknown" for unverifiable
3. Ensure every claim has a valid URL citation
4. Return the verified profiles in the EXACT same JSON structure

CRITICAL: Your output MUST be a valid JSON array of neighbor profiles wrapped in ```json code blocks.
"""
        return full_input

    def verify_batch(
        self,
        profiles: List[Dict[str, Any]],
        context: Dict[str, Any],
        entity_type: str = "person",
        source_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verify a batch of neighbor profiles.

        Args:
            profiles: List of neighbor profile dicts to verify
            context: Dict with county, state, city
            entity_type: "person" or "organization"
            source_file: Name of the source dr_*.json file (for DEBUG matching)

        Returns:
            Dict with verified profiles and metadata
        """
        county = context.get("county", "Unknown")
        state = context.get("state", "Unknown")

        print(f"\n{'=' * 70}")
        print(f"🔍 GEMINI DEEP RESEARCH - NEIGHBOR VERIFICATION ({entity_type.upper()})")
        print(f"{'=' * 70}")
        print(f"   Location: {county}, {state}")
        print(f"   Entity type: {entity_type}")
        print(f"   Profiles to verify: {len(profiles)}")
        print(f"   Source file: {source_file or 'N/A'}")
        print(f"   Agent: {self.agent}")

        if not profiles:
            return {
                "status": "completed",
                "neighbors": [],
                "metadata": {"note": "No profiles to verify"},
            }

        # Check for existing DEBUG JSON files for THIS source file to re-parse
        debug_dir = Path(__file__).parent.parent / "deep_research_outputs"
        debug_files = sorted(debug_dir.glob("interaction_DEBUG_*.json"), reverse=True) if debug_dir.exists() else []

        if debug_files and source_file:
            print(f"   📂 Found {len(debug_files)} DEBUG JSON file(s), looking for match to {source_file}...")
            for debug_file in debug_files:
                try:
                    with open(debug_file, "r", encoding="utf-8") as f:
                        debug_data = json.load(f)

                    # Verify this DEBUG file matches current source file
                    if debug_data.get("source_file") != source_file:
                        continue

                    print(f"   🔄 Found matching DEBUG: {debug_file.name}")

                    # Extract verified_content from debug JSON
                    verified_content = None
                    thinking_summaries = []

                    for output in debug_data.get("outputs", []):
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
                            verified_content = output["text"]

                    if not verified_content and debug_data.get("outputs"):
                        verified_content = debug_data["outputs"][-1].get("text")

                    if verified_content:
                        print(f"   📊 Parsing cached content: {len(verified_content)} chars")
                        verified_profiles = self._parse_json_output(verified_content, profiles)

                        # Success - delete the DEBUG file since it's been parsed
                        import subprocess
                        subprocess.run(["trash", str(debug_file)], check=True)
                        print(f"   🗑️ Trashed parsed DEBUG file: {debug_file.name}")

                        print(f"\n{'=' * 70}")
                        print("✅ NEIGHBOR VERIFICATION COMPLETE (from cached DEBUG)")
                        print(f"{'=' * 70}")
                        print(f"   Profiles verified: {len(verified_profiles)}")

                        return {
                            "status": "completed",
                            "neighbors": verified_profiles,
                            "metadata": {
                                "thinking_summaries": thinking_summaries,
                                "total_tokens": 0,
                                "entity_type": entity_type,
                                "profiles_input": len(profiles),
                                "profiles_output": len(verified_profiles),
                                "from_cache": True,
                            },
                        }
                except Exception as e:
                    print(f"   ⚠️ Failed to parse {debug_file.name}: {e}")
                    continue

            print(f"   ℹ️ No matching DEBUG file for {source_file}, proceeding with API call...")

        # Build full input
        full_input = self._build_verification_input(profiles, context, entity_type)
        print(f"   📄 Input size: {len(full_input)} characters")

        try:
            # Start Deep Research task
            print(f"   🚀 Sending to Gemini Deep Research API...")

            interaction = self.client.interactions.create(
                input=full_input,
                agent=self.agent,
                background=True,
                store=True,  # Required when using background=True
                agent_config={
                    "type": "deep-research",
                    "thinking_summaries": "always",
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
                    return {
                        "status": "failed",
                        "error": f"Timeout after {self.max_wait_time // 60} minutes",
                        "neighbors": profiles,  # Return original on timeout
                    }

                interaction = self.client.interactions.get(interaction_id)
                status = interaction.status

                if status != last_status:
                    print(f"   📊 Status: {status} ({int(elapsed)}s elapsed)")
                    last_status = status

                if status == "completed":
                    break
                elif status in ["failed", "cancelled"]:
                    error_msg = getattr(interaction, "error", None) or f"Status: {status}"
                    return {
                        "status": "failed",
                        "error": str(error_msg),
                        "neighbors": profiles,  # Return original on failure
                    }

                time.sleep(self.poll_interval)

            # Save full interaction response as debug JSON before extracting
            debug_json_path = Path(__file__).parent.parent / "deep_research_outputs" / f"interaction_DEBUG_{entity_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            debug_json_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Serialize full interaction response (use model_dump if available)
                if hasattr(interaction, 'model_dump'):
                    debug_data = interaction.model_dump()
                else:
                    debug_data = {attr: getattr(interaction, attr) for attr in dir(interaction)
                                  if not attr.startswith('_') and not callable(getattr(interaction, attr))}

                # Add our tracking fields
                debug_data["source_file"] = source_file
                debug_data["entity_type"] = entity_type

                with open(debug_json_path, "w", encoding="utf-8") as f:
                    json.dump(debug_data, f, indent=2, ensure_ascii=False, default=str)
                print(f"   📄 Saved interaction debug to: {debug_json_path.name}")
            except Exception as e:
                print(f"   ⚠️ Failed to save interaction debug: {e}")
                return {
                    "status": "failed",
                    "error": f"Failed to save interaction debug: {e}",
                    "neighbors": profiles,
                }

            # Extract output ONLY from the saved debug JSON file
            with open(debug_json_path, "r", encoding="utf-8") as f:
                debug_data = json.load(f)

            if not debug_data.get("outputs"):
                return {
                    "status": "failed",
                    "error": "No outputs in completed interaction",
                    "neighbors": profiles,
                }

            # Separate thinking summaries from text output (from debug JSON)
            thinking_summaries = []
            verified_content = None

            for output in debug_data.get("outputs", []):
                output_type = output.get("type")
                if output_type == 'thought' and output.get("summary"):
                    summary = output["summary"]
                    if isinstance(summary, list):
                        for item in summary:
                            # Handle both string and text content object formats
                            if isinstance(item, dict) and item.get("text"):
                                thinking_summaries.append(item["text"])
                            elif isinstance(item, str):
                                thinking_summaries.append(item)
                    elif isinstance(summary, str):
                        thinking_summaries.append(summary)
                elif output_type == 'text' and output.get("text"):
                    verified_content = output["text"]

            # Fallback to last output if no text type found
            if not verified_content and debug_data["outputs"]:
                verified_content = debug_data["outputs"][-1].get("text")

            if not verified_content:
                return {
                    "status": "failed",
                    "error": "Empty output from Gemini Deep Research",
                    "neighbors": profiles,
                }

            # Log what we're about to parse (should match debug JSON exactly)
            print(f"   📊 Parsing content from debug JSON: {len(verified_content)} chars")
            print(f"   📄 Source: {debug_json_path.name}")

            # Parse verified profiles from output
            verified_profiles = self._parse_json_output(verified_content, profiles)

            # Get usage info if available
            total_tokens = 0
            if hasattr(interaction, "usage") and interaction.usage:
                total_tokens = getattr(interaction.usage, "total_tokens", 0)

            print(f"\n{'=' * 70}")
            print("✅ NEIGHBOR VERIFICATION COMPLETE")
            print(f"{'=' * 70}")
            print(f"   Profiles verified: {len(verified_profiles)}")
            print(f"   Thinking summaries: {len(thinking_summaries)}")
            if total_tokens:
                print(f"   Total tokens: {total_tokens}")

            return {
                "status": "completed",
                "neighbors": verified_profiles,
                "metadata": {
                    "thinking_summaries": thinking_summaries,
                    "total_tokens": total_tokens,
                    "entity_type": entity_type,
                    "profiles_input": len(profiles),
                    "profiles_output": len(verified_profiles),
                },
            }

        except Exception as e:
            print(f"❌ Verification failed: {e}")
            import traceback
            traceback.print_exc()
            return {
                "status": "failed",
                "error": str(e),
                "neighbors": profiles,  # Return original on exception
            }

    def _parse_json_output(
        self,
        content: str,
        original_profiles: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Parse JSON profiles from Gemini output.

        Args:
            content: Raw text output from Gemini
            original_profiles: Original profiles to fall back to

        Returns:
            List of verified profile dicts
        """
        # Clean up content
        content = content.strip()

        # Remove markdown code block wrapper if present
        if content.startswith("```json"):
            content = content[len("```json"):].strip()
        elif content.startswith("```"):
            first_newline = content.find("\n")
            if first_newline != -1:
                content = content[first_newline + 1:].strip()

        if content.endswith("```"):
            content = content[:-3].strip()

        # Try to find JSON array or object in content
        # Strategy 1: Find ```json code blocks and extract the full JSON (greedy)
        json_block_match = re.search(r'```json\s*(\[[\s\S]*\])\s*```', content)
        if json_block_match:
            json_str = json_block_match.group(1)
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, list):
                    print(f"   ✓ Parsed {len(parsed)} profiles from JSON code block")
                    return parsed
            except json.JSONDecodeError:
                pass

        # Strategy 2: Try regex patterns
        json_patterns = [
            # Pattern 1: Direct JSON array (entire content)
            r'^\s*\[[\s\S]*\]\s*$',
            # Pattern 2: Object with neighbors array
            r'\{\s*"neighbors"\s*:\s*(\[[\s\S]*?\])',
            # Pattern 3: Find array starting with neighbor_id (greedy to get full array)
            r'(\[\s*\{[^[]*"neighbor_id"[\s\S]*\])',
        ]

        for pattern in json_patterns:
            match = re.search(pattern, content)
            if match:
                json_str = match.group(1) if match.lastindex else match.group(0)
                try:
                    parsed = json.loads(json_str)
                    if isinstance(parsed, list):
                        print(f"   ✓ Parsed {len(parsed)} profiles from JSON array")
                        return parsed
                    elif isinstance(parsed, dict) and "neighbors" in parsed:
                        print(f"   ✓ Parsed {len(parsed['neighbors'])} profiles from neighbors key")
                        return parsed["neighbors"]
                except json.JSONDecodeError:
                    continue

        # Try parsing entire content as JSON
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return parsed
            elif isinstance(parsed, dict) and "neighbors" in parsed:
                return parsed["neighbors"]
        except json.JSONDecodeError:
            pass

        # FAIL LOUDLY - save raw output for debugging and raise error
        debug_path = Path(__file__).parent.parent / "deep_research_outputs" / f"gemini_raw_output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(f"=== GEMINI RAW OUTPUT ===\n")
            f.write(f"Length: {len(content)} chars\n")
            f.write(f"First 500 chars:\n{content[:500]}\n\n")
            f.write(f"Last 500 chars:\n{content[-500:]}\n\n")
            f.write(f"=== FULL CONTENT ===\n{content}")

        print(f"   ❌ Could not parse JSON from Gemini output!")
        print(f"   📄 Raw output saved to: {debug_path.name}")
        print(f"   Content length: {len(content)} chars")
        print(f"   First 200 chars: {content[:200]}...")

        raise ValueError(f"Failed to parse JSON from Gemini output. Raw output saved to {debug_path}")
