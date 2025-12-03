#!/usr/bin/env python3
"""
Apply deduplication to neighbor_final_merged.json without re-running the full pipeline.
"""
import json
from pathlib import Path
from typing import List, Dict, Any


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def dedupe_neighbors(neighbors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicate neighbors with similar names (Levenshtein distance <= 2).
    Rules:
    - Combine PINs from all duplicates
    - Stance priority: oppose > neutral > support > unknown
    - Keep stance, community_influence, approach_recommendations from winning entry
    - Use lowest confidence: low < medium < high
    """
    STANCE_PRIORITY = {"oppose": 3, "neutral": 2, "support": 1, "unknown": 0}
    CONFIDENCE_PRIORITY = {"low": 1, "medium": 2, "high": 3}
    MAX_DISTANCE = 2

    # Group by similar names
    groups: List[List[Dict[str, Any]]] = []
    group_names: List[str] = []

    for n in neighbors:
        name = n.get("name", "").strip().lower()
        if not name:
            continue

        matched_group_idx = None
        for idx, rep_name in enumerate(group_names):
            if levenshtein_distance(name, rep_name) <= MAX_DISTANCE:
                matched_group_idx = idx
                break

        if matched_group_idx is not None:
            groups[matched_group_idx].append(n)
        else:
            groups.append([n])
            group_names.append(name)

    deduped = []
    for group in groups:
        if len(group) == 1:
            deduped.append(group[0])
            continue

        names_in_group = [e.get("name", "") for e in group]
        print(f"[DEDUP] Found {len(group)} similar entries: {names_in_group}, merging...")

        # Combine all PINs
        all_pins = []
        for entry in group:
            pins = entry.get("pins", [])
            if isinstance(pins, list):
                all_pins.extend(pins)
            elif pins:
                all_pins.append(pins)
        
        seen_pins = set()
        unique_pins = []
        for pin in all_pins:
            if pin not in seen_pins:
                seen_pins.add(pin)
                unique_pins.append(pin)

        # Find entry with highest stance priority
        def get_stance_priority(entry):
            stance = entry.get("noted_stance", "unknown") or "unknown"
            return STANCE_PRIORITY.get(stance.lower(), 0)

        winning_entry = max(group, key=get_stance_priority)

        # Find lowest confidence
        def get_confidence_priority(entry):
            conf = entry.get("confidence", "medium") or "medium"
            return CONFIDENCE_PRIORITY.get(conf.lower(), 2)

        lowest_conf_entry = min(group, key=get_confidence_priority)
        lowest_confidence = lowest_conf_entry.get("confidence", "medium")

        # Merge
        merged_entry = winning_entry.copy()
        merged_entry["pins"] = unique_pins
        merged_entry["confidence"] = lowest_confidence

        # Combine claims
        all_claims = []
        for entry in group:
            claims = entry.get("claims", "")
            if claims and claims not in all_claims:
                all_claims.append(claims)
        if len(all_claims) > 1:
            merged_entry["claims"] = " ".join(all_claims)

        print(f"[DEDUP] Merged into '{merged_entry.get('name')}': {len(unique_pins)} PINs, stance={merged_entry.get('noted_stance')}, confidence={lowest_confidence}")
        deduped.append(merged_entry)

    return deduped


if __name__ == "__main__":
    base = Path(__file__).parent
    json_path = base / "neighbor_outputs" / "neighbor_final_merged.json"
    
    print(f"Loading {json_path}...")
    with open(json_path) as f:
        data = json.load(f)
    
    original_count = len(data["neighbors"])
    print(f"Original neighbor count: {original_count}")
    
    data["neighbors"] = dedupe_neighbors(data["neighbors"])
    
    new_count = len(data["neighbors"])
    print(f"After dedup: {new_count} ({original_count - new_count} removed)")
    
    # Save back
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"Saved to {json_path}")
