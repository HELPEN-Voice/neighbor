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
    ) -> Dict[str, Any]:
        """Verify a batch of neighbor profiles.

        Args:
            profiles: List of neighbor profile dicts to verify
            context: Dict with county, state, city
            entity_type: "person" or "organization"

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
        print(f"   Agent: {self.agent}")

        if not profiles:
            return {
                "status": "completed",
                "neighbors": [],
                "metadata": {"note": "No profiles to verify"},
            }

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

            # Extract the output
            if not interaction.outputs:
                return {
                    "status": "failed",
                    "error": "No outputs in completed interaction",
                    "neighbors": profiles,
                }

            # Separate thinking summaries from text output
            thinking_summaries = []
            verified_content = None

            for output in interaction.outputs:
                output_type = getattr(output, 'type', None)
                if output_type == 'thought' and hasattr(output, 'summary'):
                    if output.summary:
                        # summary may be a list of TextContent objects
                        if isinstance(output.summary, list):
                            for item in output.summary:
                                if hasattr(item, 'text'):
                                    thinking_summaries.append(item.text)
                                else:
                                    thinking_summaries.append(str(item))
                        elif hasattr(output.summary, 'text'):
                            thinking_summaries.append(output.summary.text)
                        else:
                            thinking_summaries.append(str(output.summary))
                elif output_type == 'text' and hasattr(output, 'text'):
                    verified_content = output.text

            # Fallback to last output if no text type found
            if not verified_content and interaction.outputs:
                verified_content = getattr(interaction.outputs[-1], 'text', None)

            if not verified_content:
                return {
                    "status": "failed",
                    "error": "Empty output from Gemini Deep Research",
                    "neighbors": profiles,
                }

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
        json_patterns = [
            # Pattern 1: Direct JSON array
            r'^\s*\[[\s\S]*\]\s*$',
            # Pattern 2: JSON in code block
            r'```json\s*(\[[\s\S]*?\])\s*```',
            # Pattern 3: Object with neighbors array
            r'\{\s*"neighbors"\s*:\s*(\[[\s\S]*?\])',
            # Pattern 4: Just find any JSON array
            r'(\[[\s\S]*?\{[\s\S]*?"neighbor_id"[\s\S]*?\}[\s\S]*?\])',
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
