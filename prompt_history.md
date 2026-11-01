# Development Log: KYC Evidence Tracker

This log records how the project was built with Claude as an AI assistant, from 30 Oct to 1 Nov 2026. The prompts below are the real ones I sent, shortened where I pasted long text.

## How I worked

- I told Claude at the start that I was a fresher with foundational knowledge. My main skills are FastAPI and Postgres, and I wanted each concept explained.
- I asked for code to copy and a note on which file each piece goes in. I created every file, ran every command and checked every result myself.
- The project was built in phases. I did not move to the next phase until the tests passed and the checks gave the expected numbers.
- When something failed, I pasted the full error. Two failures happened (entries 4 and 13).

## Prompt history

### Phase 0 and 1: Understanding the problem, setup and design

**1. Problem statement (30 Oct)**
> Pasted the full problem statement. "Understand this problem statement and help me with the implementation.  My core domain is FastAPI and Postgres for data. Choose React or Next for frontend but the frontend will be done after the backend part. At the very start, help me to understand the problem statement in easy terms and how we will reach the final solution."

- **Result:** A plain-language explanation of KYC, the 10 entities, the planted anomalies, and a phase plan. Claude recommended React with Vite for the frontend.
- **What I learned:** Half of the task is data engineering (seeded data, labelled anomalies, a separate answer key) and half is a backend app.

**2. Delivery format**
> "Just show me the with and explanations, but only after the project setup, just give me the code and i will review it before implementing myself. I will only paste the code after reviewing it so that I know exactly how my whole project architecture is and how the system runs and what depends on what."

- **Result:** Folder structure, Docker Compose for Postgres, `.env`, config, enums and a health endpoint, followed by a dependency diagram and a "what goes where" table. Also the ER relationships, business rules, ID formats and a data dictionary for all entities.
- **I did:** Created the structure, started Postgres, ran the API and checked `/health`.

**3. "done"**
- Confirmed Phase 0 and 1 were finished. Claude moved on to the database schema.

### Phase 2: Database schema

**4. First test failure**
> Pasted the pytest output. "Yes the test failed, see this."

- **Cause:** My typo. I wrote `customer_type` in the test, but the table is `customer_types`. Postgres reported a missing table instead of an integrity error.
- **Fix:** I corrected the table name. Claude also suggested asserting the constraint name in the test.
- **What I learned:** A test that expects one kind of error will fail on a different kind. Reading the error type (`ProgrammingError` vs `IntegrityError`) tells me where the bug is.

**5. Moving on**
> "Oh typo was my mistake, now tests are passed. Move to phase 3."

- **Result:** The SQLAlchemy models, Alembic migrations, a naming convention for constraints, and append-only triggers on `verifications` and `audit_events`.
- **Design point I took away:** The database blocks structural errors only. Business-rule violations are left for validation, so the planted anomalies can exist in the data.

### Phase 3: Synthetic data generator

**6. Phase 3A (customers and master data)**
> "Done we can move to phase 3B." (sent after 3A)

- **Built:** A seeded generator with one random stream per stage, deterministic IDs, the requirement matrix, and 8,000 customers.

**7. Phase 3B (documents and verifications)**
> "Yes everything done and tests are green. Move to phase 3C."

- **Built:** Documents, verifications and requirement links. Document status comes from the latest verification, not from random assignment. Document count is exactly 20,000.
- **Ran:** The tests and the SQL status checks.

**8. Phase 3C (owners, ratings and reviews)**
> "Yes all done and test passed move to phase 3D"

- **Built:** Beneficial owners with integer-based percentage splits, risk rating history, and the 3,000 review cases for the 2026 cohort.
- **Ran:** The five SQL checks.

**9. Phase 3D (derived values)**
> "Everything done and green, we can move next"

- **Built:** One shared completeness function, derived `kyc_status`, review resolutions, expiry and overdue alerts, and a replayable audit trail.
- **Ran:** The SQL cross-checks, which returned 0 as expected.

**10. Phase 3E (anomalies, ground truth, exports)**
> "Yes everything is done and green, we can move to phase 4"

- **Built:** Six labelled anomaly types, a separate ground truth, CSV/JSON/SQL exports, a manifest with checksums and `--repro-check`.
- **Ran:** The six SQL checks and the two-run reproducibility check.

### Phase 4: Validation suite

**11. Starting Phase 5 (after Phase 4)**
> "Everything done, start phase 5"

- **Built (Phase 4):** About 100 read-only SQL checks, plus a report that compares findings with the ground truth using precision and recall. A test deliberately corrupts data inside a rolled-back transaction to prove the checks catch it.

### Phase 5: FastAPI backend

**12. Phase 5A results and next step**
> Pasted the `/exceptions/summary` response (total 280: 60 / 80 / 25 / 40 / 60 / 15). "Everything is green we can move next."

- **Built (5A):** Read endpoints for customers, completeness, documents, owners, reviews, alerts, audit trail, exceptions and metrics. Exceptions are found from data only. Only `/metrics` reads the ground truth.
- **Built (5B):** Role-based write endpoints (submit, verify, reassess, start and close review, acknowledge and resolve alert). Every write appends rows and an audit event. There are no update or delete routes.

**13. Second test failure**
> Pasted the pytest output. "Check this error."

- **Failure:** `test_validation_suite_still_passes_after_a_work_session` returned 409 instead of 200.
- **Cause:** A test bug, not an API bug. Two steps of the same test used the same alert. Step 3 closed a review, which auto-resolved its alert. Step 4 then tried to resolve that alert again, and the API correctly refused.
- **Fix:** I made step 4 choose a different alert.
- **What I learned:** The test data was a snapshot from before the writes. The 409 confirmed that the API validates state before writing.

### Phase 6: Documentation and packaging

**14. Wrap-up**
> "Now everything is working and green. Move to phase 6 and we are not going to make the frontend part, so phase 7 will not be. This will be the last part and we have to wrap up."

- **Decision:** I dropped the frontend. The OpenAPI docs at `/docs` are the interface.
- **Built:** A schema doc generator, README, a data dictionary change list, a release script with an acceptance gate, and a demo script.
- **Ran:** `python scripts/build_release.py`.

**15. This development log**
> Generate an md file with complete logs of propmts i given while developing this whole project. Use concise prompts rather then the full actual propmt just enough to reflect the honest developemnt process. 

## Reflections

*Written after the project, not part of the prompt history.*

**What I learned**
- Derive outcomes from rules instead of assigning them randomly. This made the data consistent and the checks possible.
- A fixed seed alone is not enough for reproducibility. I also needed a fixed as-of date, separate random streams per stage, deterministic ordering and byte-stable file output.
- The database should enforce structure, and validation should find business-rule problems. Otherwise the planted anomalies could not have been stored.
- Ground truth stays separate from detection. Detectors work from data only, and one file compares them with the answer key.
- Reusing the same rules in the generator, validator and API (`rules.py`, `completeness.py`) means they cannot disagree.

**What I would do differently**
- Read error types carefully before assuming the code is wrong. Both of my failures were test mistakes.
- Add real authentication instead of header-based demo roles.
- Add a scheduled job that raises new expiry alerts as time passes.
- Build the frontend dashboard.

**Limits I want to be clear about**
- Claude wrote the code and I copied it. I understood the design and ran and debugged each step, but I did not write every line myself.
- The dataset is fully synthetic, and no external data was used.