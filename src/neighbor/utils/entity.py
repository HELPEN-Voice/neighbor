# src/ii_agent/tools/neighbor/utils/entity.py
import re
from typing import Literal

ORG_TOKENS = {
    " llc ",
    " l.l.c ",
    " inc ",
    " ltd ",
    " co ",
    " corp ",
    " company ",
    " church ",
    " parish ",
    " school ",
    " township ",
    " county ",
    " authority ",
    " association ",
    " trustees ",
    " trust ",
    " tr ",
    " revocable ",
    " municipal ",
    " municipality ",
    " city of ",
    " borough ",
    " volunteer fire ",
    " vfd ",
    " vfc ",
    " llp ",
    " pllc ",
    " lp ",
    " bank ",
    " community ",
    " foundation ",
    " institute ",
    " center ",
    " group ",
    " partners ",
}


def guess_entity_type(name: str) -> Literal["person", "organization"]:
    # Pre-normalize: lowercase, replace punctuation with spaces, collapse whitespace
    normalized = name.lower()
    # Replace punctuation with spaces
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    # Collapse multiple spaces to single space
    normalized = re.sub(r"\s+", " ", normalized)
    # Add spaces at boundaries for token matching
    normalized = f" {normalized.strip()} "

    return "organization" if any(tok in normalized for tok in ORG_TOKENS) else "person"
