import csv
from collections import defaultdict
from pathlib import Path


def load_ground_truth(path) -> dict[str, set[str]] | None:
    """issue_type -> set of entity_ids. Returns None if the file does not exist."""
    p = Path(path)
    if not p.exists():
        return None
    truth: dict[str, set[str]] = defaultdict(set)
    with p.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            truth[row["issue_type"]].add(row["entity_id"])
    return truth


def compare(found: set[str], expected: set[str]) -> dict:
    """Detector output vs answer key. Empty-vs-empty counts as perfect (1.0)."""
    hit = found & expected
    false_pos, false_neg = found - expected, expected - found
    return {
        "expected": len(expected),
        "found": len(found),
        "true_positives": len(hit),
        "false_positives": len(false_pos),
        "false_negatives": len(false_neg),
        "precision": round(len(hit) / len(found), 4) if found else 1.0,
        "recall": round(len(hit) / len(expected), 4) if expected else 1.0,
        "missed_sample": sorted(false_neg)[:5],
        "unexpected_sample": sorted(false_pos)[:5],
    }