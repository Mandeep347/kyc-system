# KYC Evidence Tracker

Tracks customer onboarding evidence, validates completeness, schedules periodic
reviews and raises expiry alerts. Includes a seeded synthetic data generator with
labelled anomalies, a validation suite, and a FastAPI backend with role-based write
actions. **All data is synthetic.**

- **Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16 (Docker), Faker, pytest
- **Fixed seed:** `20261022` | **As-of date:** `2026-10-30` (the fixed "today" for all derived data)
- **Volumes:** 8,000 customers (1,000 businesses), about 20,000 documents (exactly 20,000), 3,000 review cases

## Quick start

```bash
python3.12 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                   # Windows: copy .env.example .env

docker compose up -d                   # Postgres
alembic upgrade head                   # create the 11 tables + append-only triggers
python -m generator                    # build, export to output/, load Postgres
python -m validation                   # ~100 checks + reconciliation with ground truth
uvicorn app.main:app --reload          # API at http://localhost:8000/docs
```

Everything in one go (tests, generation, validation, reproducibility proof, packaging):

```bash
python scripts/build_release.py
```

## Generator command

```bash
python -m generator --scale 1.0 --seed 20261022 --as-of 2026-10-30
```

| Flag | Meaning |
|---|---|
| `--scale` | 1.0 = full dataset, 0.01 = starter. Same code, same rules. |
| `--no-anomalies` | clean baseline only |
| `--no-load` / `--no-export` | skip the database / skip the files |
| `--export-dir` | where files go (default `output/`) |
| `--repro-check` | generate twice in separate processes (different `PYTHONHASHSEED`) and compare checksums |

## Architecture

```
generator/  builds lists of dicts in memory (customers -> documents -> verifications -> owners,
            ratings, reviews -> derived statuses -> alerts -> anomalies -> audit events)
   |  loader.py -> Postgres        export.py -> output/ (CSV, JSON, SQL, ground truth, manifest)
app/        FastAPI: api (routers) -> services (rules, SQL) -> models (tables)
validation/ read-only SQL checks; reconcile.py is the only reader of ground truth
```

Rules (score bands, cadences, lead times, document specs, allowed transitions) live once, in
`app/core/rules.py`. The generator, validator and API all import them. Completeness is one pure
function, `app/services/completeness.py`, used by the generator and by `GET /customers/{id}/completeness`.

Documentation: `docs/data_dictionary.md` (fields and rules), `docs/schema_reference.md`
(generated from the models, includes the ER diagram).

## Data model

11 tables: `customer_types`, `kyc_requirements`, `customers`, `documents`, `verifications`,
`risk_ratings`, `review_cases`, `beneficial_owners`, `alerts`, `audit_events`, plus the
bridge table `document_requirement_links` (the only N:M). History is never overwritten:
re-verification adds a row, ownership and risk changes close a row and open a new one,
every state change adds an audit event.

IDs: `CUS-000001`, `KYC-2026-000001`, `DOC-<customer-id>-001`, `REV-<customer-id>-2026`,
`ALT-2026-000001` (as specified); `CT-`, `VER-`, `RSK-`, `BO-`, `AUD-`, `LNK-`, `GT-` are our own, in the same style.

## How data is generated

1. Each stage has its own random stream derived from `(seed, stage name)`, so adding a stage never shifts earlier data.
2. Valid baseline first. Outcomes are derived, never random: document status from the latest verification,
   `kyc_status` from completeness, review resolution from evidence, alerts from expiry dates and lead times.
3. Then labelled anomalies are injected, each on its own customers, and the answer key is written separately.

| Planted anomaly | Count | Detection rule (data only) |
|---|---|---|
| Expired document marked VALID | 60 | `status='VALID' AND expiry_date < as_of` |
| Customer APPROVED with missing mandatory evidence | 80 | approved and completeness < 100% |
| Ownership above 100% | 25 | active owners sum > 100 |
| Review closed without resolution | 40 | `CLOSED` and `resolution IS NULL` |
| Duplicate document across customers | 30 pairs (60 docs) | same `document_number`, 2+ customers |
| Missing ownership records | 15 | business with no active owner |

Normal states that also appear in the baseline: expired documents (never renewed), name mismatches,
overdue reviews (due date passed, still open).

## Validation

`python -m validation` runs read-only SQL checks: primary keys, foreign keys, ID formats, enums,
date ordering, reconciliation of derived values, lifecycle replay of the audit trail, duplicates and
business rules. Expected-zero checks must find nothing; planted-anomaly detectors must **match the
ground truth exactly**. Output: `output/validation/validation_summary.{md,json}`.

## Reproducibility

`python -m generator --repro-check` runs the generator twice in separate processes and compares the
SHA-256 of every output file. Files are written as raw UTF-8 with `\n` line endings and the manifest
has no timestamps, so checksums match across Windows and Linux. Evidence: `output/reproducibility_report.json`.

## API

Interactive docs: `http://localhost:8000/docs`.

| Area | Endpoints |
|---|---|
| Customers | `GET /customers`, `/customers/{id}`, `/completeness`, `/documents`, `/owners`, `/reviews`, `/audit-trail` |
| Documents | `GET /documents/{id}`, `/documents/expiring` |
| Alerts / reviews | `GET /alerts`, `/alerts/summary`, `/alerts/{id}`, `/reviews/overdue`, `/reviews/{id}` |
| Exceptions | `GET /exceptions`, `/exceptions/summary` (found from data, never from ground truth) |
| Metrics | `GET /metrics` (precision/recall vs ground truth, completeness, alerts, reviews) |
| Writes | `POST /customers/{id}/documents`, `/documents/{id}/verify`, `/customers/{id}/reassess`, `/reviews/{id}/start`, `/reviews/{id}/close`, `/alerts/{id}/acknowledge`, `/alerts/{id}/resolve` |
| Admin | `GET /admin/validation` (runs the validation suite live) |

Write endpoints need headers `X-Role` (`CUSTOMER`, `ANALYST`, `REVIEWER`, `ADMIN`) and `X-User`.
This is a **demo identity, not security**; real authentication would replace `get_principal`.
There are no PUT/PATCH/DELETE routes.

## Tests

```bash
python -m pytest -q
```

Covers generator determinism and derived values, planted-anomaly detectors (exact match with
ground truth, none in the clean baseline), the validator (including deliberate corruption), every API
endpoint, and a full write session followed by the validation suite. DB tests roll back; your data is untouched.

## Assumptions and decisions

- "1,000 business entities" = 1,000 of the 8,000 customers (70% companies, 30% sole proprietors). Individuals have no beneficial owners.
- The 3,000 reviews are the 2026 cohort: customers whose onboarding date plus risk cadence (HIGH 12, MEDIUM 24, LOW 36 months) lands in 2026.
- Review due dates are computed from the current risk rating, as if the schedule is recalculated when the rating changes.
- `kyc_status` and review resolutions are evaluated as of the as-of date.
- A sole proprietor is named after the person; `incorporation_date` holds the business registration date.
- Emails use `example.com` (reserved, no real mailboxes).
- Alerts are generated for trigger dates from 1 Jan 2026 up to the as-of date (matches the `ALT-2026-` ID format).
- Owner and verification IDs are renumbered after injection so planted rows don't stand out by ID. The anomaly dataset and the
  `--no-anomalies` baseline therefore number those two tables differently.

## Limitations and future work

- Authentication is a header-based demo.
- No scheduled job raises new alerts as time passes; the alert rules exist in the generator and would be reused.
- API writes use the configured as-of date with the real time of day. After manual writes the database no longer equals the generated
  snapshot, so `/metrics` can show recall below 1.0. Run `python -m generator` to reset.
- No frontend was built; the OpenAPI docs are the interface.
- `MISSING_EVIDENCE` and `OWNERSHIP_ERROR` alerts are not generated in the baseline (see `/exceptions`).

## Provenance and lawful use

No external data was used. Every record is produced by this repository with Faker (locale `en_IN`) and Python's
`random`. See `output/source_manifest.json`. No real people, companies or documents.

## Results of the final run

| Item | Value |
|---|---|
| Customers / documents / businesses / reviews | 8,000 / 20,000 / 1,000 / 3,000 |
| Planted anomalies detected | 280 of 280 (precision 1.0, recall 1.0) |
| Validation verdict | PASS |
| Reproducibility | all files MATCH |
| Customer status mix, alert counts | (copy from `GET /metrics`) |