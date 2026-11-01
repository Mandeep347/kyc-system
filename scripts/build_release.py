"""Builds and verifies the final submission.  Run from the project root:
    python scripts/build_release.py
Stop uvicorn first (the generator truncates tables)."""
import csv
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
OUT = ROOT / "output"
ZIP = ROOT / "kyc-submission.zip"

STEPS = [
    ("Apply migrations", [PY, "-m", "alembic", "upgrade", "head"]),
    ("Run the test suite", [PY, "-m", "pytest", "-q"]),
    ("Generate the full dataset, export it, load Postgres", [PY, "-m", "generator"]),
    ("Validate the database against the ground truth", [PY, "-m", "validation"]),
    ("Reproducibility proof (two separate runs)", [PY, "-m", "generator", "--repro-check"]),
    ("Generate the starter dataset (files only)",
     [PY, "-m", "generator", "--scale", "0.01", "--no-load", "--export-dir", "sample_data"]),
    ("Generate the schema reference", [PY, "scripts/make_schema_docs.py"]),
]

REQUIRED_FILES = [
    "README.md", "requirements.txt", "docker-compose.yml", ".env.example",
    "docs/data_dictionary.md", "docs/schema_reference.md",
    "output/manifest.json", "output/source_manifest.json", "output/seed.sql",
    "output/ground_truth/ground_truth.csv", "output/ground_truth/ground_truth.json",
    "output/ground_truth/expected_decisions.csv",
    "output/validation/validation_summary.md", "output/validation/validation_summary.json",
    "output/reproducibility_report.json",
]
EXPECTED_ROWS = {"csv/customers.csv": 8000, "csv/documents.csv": 20000,
                 "csv/review_cases.csv": 3000, "ground_truth/ground_truth.csv": 280}
EXCLUDE_DIRS = {".venv", ".git", "__pycache__", ".pytest_cache", "node_modules"}


def run_steps() -> None:
    for number, (title, cmd) in enumerate(STEPS, start=1):
        print(f"\n{'=' * 8} step {number}/{len(STEPS)}: {title} {'=' * 8}", flush=True)
        subprocess.run(cmd, cwd=ROOT, check=True)


def gate() -> list[str]:
    problems = [f"missing file: {p}" for p in REQUIRED_FILES if not (ROOT / p).exists()]
    if problems:
        return problems

    files = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))["files"]
    for name, want in EXPECTED_ROWS.items():
        got = files[name]["rows"]
        if got != want:
            problems.append(f"{name}: {got} rows, expected {want}")

    with open(OUT / "csv" / "customers.csv", newline="", encoding="utf-8") as f:
        businesses = sum(1 for r in csv.DictReader(f) if r["customer_type_id"] != "CT-01")
    if businesses != 1000:
        problems.append(f"business customers: {businesses}, expected 1000")

    summary = json.loads((OUT / "validation" / "validation_summary.json").read_text(encoding="utf-8"))
    if summary["verdict"] != "PASS":
        problems.append("validation verdict is not PASS")
    bad = [e["check_id"] for e in summary["reconciliation"] if e["status"] != "MATCH"]
    if bad:
        problems.append(f"anomaly checks not matching ground truth: {bad}")

    repro = json.loads((OUT / "reproducibility_report.json").read_text(encoding="utf-8"))
    if not repro["identical"]:
        problems.append("reproducibility check found different checksums")
    return problems


def package() -> None:
    if ZIP.exists():
        ZIP.unlink()
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT)
            if not path.is_file() or path == ZIP:
                continue
            if EXCLUDE_DIRS & set(rel.parts) or rel.name == ".env" or rel.name.endswith(".zip"):
                continue
            if rel.parts[0] == "output" and len(rel.parts) > 1 and rel.parts[1].startswith("repro_run_"):
                continue
            z.write(path, Path("kyc-system") / rel)
    print(f"\nwrote {ZIP.name} ({ZIP.stat().st_size / 1_000_000:.1f} MB)")


def main() -> int:
    run_steps()
    problems = gate()
    if problems:
        print("\nACCEPTANCE GATE FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print("\nACCEPTANCE GATE PASSED: volumes, ground truth, validation and reproducibility all check out")
    package()
    return 0


if __name__ == "__main__":
    sys.exit(main())