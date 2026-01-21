"""PIN normalization utilities."""

import re


def normalize_pin(pin: str) -> str:
    """Normalize PIN by removing zero-width characters and collapsing whitespace.

    Args:
        pin: The PIN string to normalize.

    Returns:
        Normalized PIN with zero-width characters removed and multiple
        whitespace collapsed to single spaces.
    """
    if not pin:
        return ""
    result = (
        str(pin)
        .replace("\u200b", "")  # Zero-width space
        .replace("\u200c", "")  # Zero-width non-joiner
        .replace("\u200d", "")  # Zero-width joiner
        .replace("\ufeff", "")  # BOM/zero-width no-break space
        .replace("\u2060", "")  # Word joiner
    )
    return re.sub(r"\s+", " ", result.strip())
