1. CustomerType (master)

Field	Type	Req	Allowed values / rule	Unique
customer_type_id	varchar(10)	Y	PK, format CT-NN	Y
type_code	varchar(20)	Y	enum CustomerTypeCode	Y
display_name	varchar(60)	Y	free text	N
is_business	boolean	Y	true for SOLE_PROPRIETOR and COMPANY	N
requires_beneficial_owner	boolean	Y	true if is_business	N

2. KYCRequirement (master)

Field	Type	Req	Allowed values / rule	Unique
requirement_id	varchar(20)	Y	PK, KYC-2026-NNNNNN	Y
customer_type_id	varchar(10)	Y	FK → CustomerType	N
risk_level	varchar(10)	Y	enum RiskLevel	N
document_category	varchar(30)	Y	enum DocumentCategory	N
is_mandatory	boolean	Y		N
max_age_days	int	N	e.g. 90 for address proof: the issue date must be within this many days	N
expiry_lead_days	int[]	Y	default {60,30,7}, each > 0	N
effective_from	date	Y		N
effective_to	date	N	null = still active; must be ≥ effective_from	N
			Unique together: (customer_type_id, risk_level, document_category, effective_from)	

3. Customer

Field	Type	Req	Allowed values / rule	Unique
customer_id	varchar(12)	Y	PK, CUS-NNNNNN, immutable	Y
customer_type_id	varchar(10)	Y	FK → CustomerType	N
legal_name	varchar(200)	Y	person’s full name or company name	N
date_of_birth	date	N	required for INDIVIDUAL, null for businesses; age ≥ 18 at onboarding	N
incorporation_date	date	N	required for businesses, null for individuals	N
country_code	char(2)	Y	ISO-3166 alpha-2	N
email	varchar(200)	Y	synthetic, valid format	Y
onboarded_on	date	Y	≤ AS_OF_DATE	N
kyc_status	varchar(15)	Y	enum KycStatus (current state only; history lives in AuditEvent)	N

4. Document

Field	Type	Req	Allowed values / rule	Unique
document_id	varchar(25)	Y	PK, DOC-<customer_id>-NNN	Y
customer_id	varchar(12)	Y	FK → Customer	N
document_category	varchar(30)	Y	enum DocumentCategory	N
document_type	varchar(40)	Y	enum DocumentType; must be valid for its category	N
document_number	varchar(40)	Y	the number printed on the document. The same number under different customers is flagged as a duplicate.	N
name_on_document	varchar(200)	Y	compared to Customer.legal_name	N
issue_date	date	Y	≤ AS_OF_DATE	N
expiry_date	date	N	null for non-expiring docs; must be > issue_date	N
status	varchar(10)	Y	enum DocumentStatus; the current result of the latest Verification	N
submitted_on	date	Y	≥ issue_date	N

5. Verification (append-only)

Field	Type	Req	Allowed values / rule	Unique
verification_id	varchar(12)	Y	PK, VER-NNNNNN	Y
document_id	varchar(25)	Y	FK → Document	N
result	varchar(20)	Y	enum VerificationResult	N
name_match_score	numeric(5,2)	Y	0–100; below 85 means NAME_MISMATCH	N
verified_by	varchar(40)	Y	analyst username (synthetic)	N
verified_at	timestamp	Y	≥ document submitted_on	N
remarks	text	N		N

6. RiskRating (append-only history)

Field	Type	Req	Allowed values / rule	Unique
rating_id	varchar(12)	Y	PK, RSK-NNNNNN	Y
customer_id	varchar(12)	Y	FK → Customer	N
risk_level	varchar(10)	Y	enum RiskLevel, derived from risk_score	N
risk_score	int	Y	0–100. LOW < 35, MEDIUM 35–69, HIGH ≥ 70	N
reason	varchar(200)	N		N
rated_by	varchar(40)	Y		N
effective_from	date	Y		N
effective_to	date	N	null = current; no overlapping periods per customer	N

7. ReviewCase

Field	Type	Req	Allowed values / rule	Unique
review_id	varchar(25)	Y	PK, REV-<customer_id>-YYYY	Y
customer_id	varchar(12)	Y	FK → Customer. Unique together: (customer_id, review_year)	N
review_year	int	Y		N
trigger	varchar(20)	Y	enum ReviewTrigger	N
scheduled_date	date	Y		N
due_date	date	Y	≥ scheduled_date; computed from the risk cadence	N
status	varchar(15)	Y	enum ReviewStatus	N
resolution	varchar(25)	N	enum ReviewResolution. Required when status = CLOSED.	N
assigned_to	varchar(40)	N		N
closed_at	timestamp	N	required if CLOSED, null otherwise	N

8. BeneficialOwner

Field	Type	Req	Allowed values / rule	Unique
owner_id	varchar(12)	Y	PK, BO-NNNNNN	Y
customer_id	varchar(12)	Y	FK → Customer; the customer’s type must have is_business = true	N
owner_name	varchar(200)	Y		N
nationality	char(2)	Y	ISO-3166 alpha-2	N
ownership_pct	numeric(5,2)	Y	> 0 and ≤ 100. The active rows per customer must sum to 100.	N
is_pep	boolean	Y	politically exposed person flag	N
effective_from	date	Y		N
effective_to	date	N	null = active	N

9. Alert

Field	Type	Req	Allowed values / rule	Unique
alert_id	varchar(20)	Y	PK, ALT-2026-NNNNNN	Y
customer_id	varchar(12)	Y	FK → Customer	N
document_id	varchar(25)	N	FK → Document; required for DOC_* types	N
review_id	varchar(25)	N	FK → ReviewCase; required for REVIEW_OVERDUE	N
alert_type	varchar(20)	Y	enum AlertType	N
lead_days	int	N	only for DOC_EXPIRING; one of the configured lead times	N
severity	varchar(10)	Y	enum Severity	N
status	varchar(15)	Y	enum AlertStatus	N
trigger_date	date	Y	= expiry_date − lead_days for expiry alerts	N
created_at	timestamp	Y		N

10. AuditEvent (append-only, never updated or deleted)

Field	Type	Req	Allowed values / rule	Unique
event_id	varchar(12)	Y	PK, AUD-NNNNNN	Y
customer_id	varchar(12)	Y	FK → Customer (makes the per-customer audit trail fast)	N
entity_type	varchar(30)	Y	e.g. DOCUMENT, REVIEW_CASE, CUSTOMER	N
entity_id	varchar(30)	Y	soft reference, with no FK	N
action	varchar(20)	Y	enum AuditAction	N
old_value	jsonb	N		N
new_value	jsonb	N		N
actor	varchar(40)	Y	analyst or system	N
occurred_at	timestamp	Y	non-decreasing order per entity	N

11. DocumentRequirementLink (bridge table for N:M)

Field	Type	Req	Allowed values / rule	Unique
link_id	varchar(12)	Y	PK, LNK-NNNNNN	Y
document_id	varchar(25)	Y	FK → Document	N
requirement_id	varchar(20)	Y	FK → KYCRequirement	N
linked_on	date	Y		N
			Unique together: (document_id, requirement_id)	

Ground truth (separate files, not part of the operational tables)

Field	Type	Rule
ground_truth_id	varchar	PK, e.g. GT-000001
issue_type	varchar	enum IssueType
entity_type, entity_id	varchar	the affected record
customer_id	varchar	
expected_detection	text	the rule that should catch it