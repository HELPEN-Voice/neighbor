# src/ii_agent/tools/neighbor/utils/json_parse.py
import json, re
from typing import Optional, Tuple


def _extract_markdown(text: str) -> Optional[str]:
    """Helper to extract optional markdown block."""
    markdown_match = re.search(r"```markdown\s*([\s\S]+?)\s*```", text)
    return markdown_match.group(1).strip() if markdown_match else None


def extract_fenced_blocks(text: str) -> Tuple[dict, Optional[str]]:
    """
    Extract JSON from fenced blocks or raw text, and optionally extract markdown.
    Returns: (parsed_json_dict, markdown_text)
    Raises ValueError if no valid JSON found.
    """
    # Try fenced JSON block first
    json_match = re.search(r"```json\s*([\s\S]+?)\s*```", text)

    if json_match:
        try:
            json_data = json.loads(json_match.group(1).strip())
            return json_data, _extract_markdown(text)
        except json.JSONDecodeError as e:
            # If fenced block exists but has invalid JSON, still raise error
            raise ValueError(f"Invalid JSON in fenced block: {e}")

    # Fallback: try whole text as JSON
    try:
        json_data = json.loads(text.strip())
        return json_data, _extract_markdown(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"No valid JSON found: {e}")
