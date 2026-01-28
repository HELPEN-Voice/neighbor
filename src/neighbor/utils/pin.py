"""PIN normalization utilities."""

import re


def normalize_pin(pin: str) -> str:
    """Normalize PIN by removing dashes, zero-width characters, and whitespace.

    Args:
        pin: The PIN string to normalize.

    Returns:
        Normalized PIN with dashes, spaces, and zero-width characters removed.
        E.g., "106-03-039F" -> "10603039F"
    """
    if not pin:
        return ""
    result = (
        str(pin)
        .replace("-", "")  # Remove dashes (common in formatted PINs)
        .replace(" ", "")  # Remove spaces
        .replace("\u200b", "")  # Zero-width space
        .replace("\u200c", "")  # Zero-width non-joiner
        .replace("\u200d", "")  # Zero-width joiner
        .replace("\ufeff", "")  # BOM/zero-width no-break space
        .replace("\u2060", "")  # Word joiner
    )
    return result.strip()
