import json
from collections import defaultdict
from pathlib import Path

from validation.reconcile import compare
from validation.runner import CheckResult

BAD = ("FAIL", "MISMATCH")


def build_report(results: list[CheckResult], truth, as_of, row_counts: dict,
                 informational: dict, ground_truth_path: str) -> dict:
    checks = []
    for r in results:
        c, found = r.check, set(r.found)
        entry = {"check_id": c.check_id, "category": c.category, "description": c.description,
                 "findings": len(found), "sample": list(r.found[:5])}
        if c.issue_type is None:
            entry["status"] = "PASS" if not found else "FAIL"
        else:
            entry["issue_type"] = c.issue_type.value
            if truth is None:
                entry["status"] = "UNCHECKED"
            else:
                rec = compare(found, truth.get(c.issue_type.value, set()))
                entry["reconciliation"] = rec
                bad = rec["false_positives"] or rec["false_negatives"]
                entry["status"] = "MISMATCH" if bad else "MATCH"
        checks.append(entry)

    by_cat = defaultdict(lambda: {"checks": 0, "failed": 0})
    for e in checks:
        by_cat[e["category"]]["checks"] += 1
        by_cat[e["category"]]["failed"] += e["status"] in BAD
    failed = sum(e["status"] in BAD for e in checks)
    return {
        "verdict": "FAIL" if failed else "PASS",
        "as_of": as_of.isoformat(),
        "ground_truth_used": truth is not None,
        "ground_truth_file": ground_truth_path,
        "totals": {"checks": len(checks), "failed": failed},
        "by_category": [{"category": k, **v} for k, v in by_cat.items()],
        "row_counts": row_counts,
        "informational": informational,
        "reconciliation": [e for e in checks if "issue_type" in e],
        "failures": [e for e in checks if e["status"] in BAD and "issue_type" not in e],
        "checks": checks,
    }


def render_markdown(rep: dict) -> str:
    L = ["# Validation summary", "",
         f"- **Verdict: {rep['verdict']}**",
         f"- As-of date: {rep['as_of']}",
         f"- Checks run: {rep['totals']['checks']}, failed: {rep['totals']['failed']}",
         f"- Ground truth used for reconciliation: {'yes' if rep['ground_truth_used'] else 'no'}",
         "", "## Results by category", "",
         "| Category | Checks | Failed |", "|---|---|---|"]
    L += [f"| {c['category']} | {c['checks']} | {c['failed']} |" for c in rep["by_category"]]

    L += ["", "## Planted anomalies: detection vs ground truth", "",
          "| Check | Issue type | Expected | Found | TP | FP | FN | Precision | Recall | Status |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for e in rep["reconciliation"]:
        r = e.get("reconciliation")
        if r:
            L.append(f"| {e['check_id']} | {e['issue_type']} | {r['expected']} | {r['found']} | "
                     f"{r['true_positives']} | {r['false_positives']} | {r['false_negatives']} | "
                     f"{r['precision']} | {r['recall']} | {e['status']} |")
        else:
            L.append(f"| {e['check_id']} | {e['issue_type']} | n/a | {e['findings']} | | | | | | {e['status']} |")

    L += ["", "## Failed checks", ""]
    if rep["failures"]:
        L += [f"- `{e['check_id']}`: {e['description']} ({e['findings']} findings, e.g. {e['sample']})"
              for e in rep["failures"]]
    else:
        L.append("None. Every expected-zero check found nothing.")

    L += ["", "## Row counts", "", "| Table | Rows |", "|---|---|"]
    L += [f"| {t} | {n} |" for t, n in rep["row_counts"].items()]
    L += ["", "## Informational (normal states, not defects)", ""]
    L += [f"- {k}: {v}" for k, v in rep["informational"].items()]
    return "\n".join(L) + "\n"


def write_report(rep: dict, out_dir) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation_summary.json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    (out / "validation_summary.md").write_text(render_markdown(rep), encoding="utf-8")