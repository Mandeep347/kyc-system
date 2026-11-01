import argparse
import sys
from datetime import date

from sqlalchemy import text

from app.core.config import settings
from app.core.database import Base, engine
from validation.checks import ALL_CHECKS
from validation.reconcile import load_ground_truth
from validation.report import BAD, build_report, write_report
from validation.runner import run_checks


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m validation")
    p.add_argument("--as-of", type=date.fromisoformat, default=settings.as_of_date)
    p.add_argument("--ground-truth", default="output/ground_truth/ground_truth.csv")
    p.add_argument("--out-dir", default="output/validation")
    args = p.parse_args()

    truth = load_ground_truth(args.ground_truth)
    if truth is None:
        print(f"(no ground truth at {args.ground_truth}: anomaly checks will be reported as UNCHECKED)")

    with engine.connect() as conn:
        results = run_checks(conn, args.as_of)
        row_counts = {t.name: conn.execute(text(f'SELECT count(*) FROM "{t.name}"')).scalar_one()
                      for t in Base.metadata.sorted_tables}
        informational = {
            "overdue reviews (due_date < as_of and not CLOSED)": conn.execute(text(
                "SELECT count(*) FROM review_cases WHERE status <> 'CLOSED' "
                "AND due_date < CAST(:d AS date)"), {"d": args.as_of}).scalar_one(),
            "documents with status EXPIRED": conn.execute(text(
                "SELECT count(*) FROM documents WHERE status = 'EXPIRED'")).scalar_one(),
            "documents with a NAME_MISMATCH verification": conn.execute(text(
                "SELECT count(DISTINCT document_id) FROM verifications "
                "WHERE result = 'NAME_MISMATCH'")).scalar_one(),
        }

    rep = build_report(results, truth, args.as_of, row_counts, informational, args.ground_truth)
    write_report(rep, args.out_dir)

    print(f"\nRan {rep['totals']['checks']} checks")
    for c in rep["by_category"]:
        print(f"  {c['category']:<28} {c['checks']:>3} checks, {c['failed']} failed")
    print("\nPlanted anomalies vs ground truth:")
    for e in rep["reconciliation"]:
        r = e.get("reconciliation")
        detail = (f"expected={r['expected']:<3} found={r['found']:<3} "
                  f"precision={r['precision']} recall={r['recall']}") if r else f"found={e['findings']}"
        print(f"  {e['status']:<9} {e['check_id']:<36} {detail}")
    for e in rep["checks"]:
        if e["status"] in BAD and "issue_type" not in e:
            print(f"\nFAILED {e['check_id']}: {e['findings']} findings, e.g. {e['sample']}")
    print(f"\nVERDICT: {rep['verdict']}   (report: {args.out_dir}/validation_summary.md)")
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())