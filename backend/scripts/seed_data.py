"""Static Zimbabwe healthcare demo dataset (mirrors the frontend demoData.ts)."""
from __future__ import annotations

ROLES = {
    "admin": "Full platform administration",
    "analyst": "Fraud analytics & TrustScore management",
    "agent": "Investigation queue handling",
    "auditor": "Read-only audit & compliance access",
    "provider": "Provider portal: own TrustScore, claims and appeals only",
}

PERMISSIONS = {
    "claim:read": "View claims",
    "claim:approve": "Approve claims",
    "claim:reject": "Reject claims",
    "investigation:manage": "Manage investigations",
    "provider:manage": "Manage providers / TrustScore",
    "user:manage": "Manage users",
    "audit:read": "Read audit logs",
}

ROLE_PERMISSIONS = {
    "admin": list(PERMISSIONS.keys()),
    "analyst": ["claim:read", "provider:manage", "investigation:manage", "audit:read"],
    "agent": ["claim:read", "claim:approve", "claim:reject", "investigation:manage"],
    "auditor": ["claim:read", "audit:read"],
    "provider": [],  # scoped by users.provider_id, never by staff permissions
}

# Demo provider-portal logins: (full name, email, provider seed id).
PROVIDER_USERS = [
    ("City Pharmacy Bulawayo", "portal@citypharmacy.co.zw", "prov-001"),
]

MEMBERS = [
    {
        "id": "mem-001", "member_number": "CIM-0291847", "name": "Tendai Moyo",
        "plan": "GOLD", "city": "Bulawayo", "annual_benefit": 2500, "benefit_used": 652.50,
        "conditions": [], "phone": "+263 77 291 8470",
        "email": "tendai.moyo@email.co.zw", "date_of_birth": "1985-03-14",
    },
    {
        "id": "mem-002", "member_number": "CIM-0441209", "name": "Joseph Chikwanda",
        "plan": "SILVER", "city": "Harare", "annual_benefit": 1800, "benefit_used": 923.00,
        "conditions": ["Hypertension"], "phone": "+263 71 441 2090",
        "email": "j.chikwanda@email.co.zw", "date_of_birth": "1972-11-08",
    },
    {
        "id": "mem-003", "member_number": "CIM-0882341", "name": "Mary Dzivaguru",
        "plan": "GOLD", "city": "Harare", "annual_benefit": 2500, "benefit_used": 1102.50,
        "conditions": ["Hypertension"], "phone": "+263 78 882 3410",
        "email": "mary.dzivaguru@email.co.zw", "date_of_birth": "1968-07-22",
    },
]

PROVIDERS = [
    {
        "id": "prov-001", "code": "PROV-BYO-00441", "name": "City Pharmacy Bulawayo",
        "type": "PHARMACY", "city": "Bulawayo", "trust_score": 81, "badge": "STANDARD",
        "shortfall_index": 1.71, "dispute_rate": 8.3, "flags_90d": 14,
        "total_claims": 847, "average_claim_value": 62.40, "phone": "+263 29 2882 441",
        "address": "12 Fort Street, Bulawayo CBD",
        "registration_date": "2019-03-01", "last_audit_date": "2025-09-15",
    },
    {
        "id": "prov-002", "code": "PROV-HRE-00187", "name": "Harare Medical Centre GP",
        "type": "GP", "city": "Harare", "trust_score": 73, "badge": "STANDARD",
        "shortfall_index": 2.08, "dispute_rate": 2.1, "flags_90d": 7,
        "total_claims": 1203, "average_claim_value": 48.60, "phone": "+263 24 2700 187",
        "address": "45 Samora Machel Ave, Harare",
        "registration_date": "2017-06-15", "last_audit_date": "2025-11-20",
    },
    {
        "id": "prov-003", "code": "PROV-HRE-00023", "name": "Avenues Pharmacy Harare",
        "type": "PHARMACY", "city": "Harare", "trust_score": 97, "badge": "VERIFIED",
        "shortfall_index": 0.95, "dispute_rate": 0, "flags_90d": 0,
        "total_claims": 3412, "average_claim_value": 38.20, "phone": "+263 24 2720 023",
        "address": "2 Baines Ave, Harare Avenues",
        "registration_date": "2015-01-10", "last_audit_date": "2026-01-05",
    },
    {
        "id": "prov-004", "code": "PROV-GWE-00312", "name": "QuickCare Pharmacy Gweru",
        "type": "PHARMACY", "city": "Gweru", "trust_score": 78, "badge": "STANDARD",
        "shortfall_index": 1.10, "dispute_rate": 3.1, "flags_90d": 3,
        "total_claims": 562, "average_claim_value": 41.80, "phone": "+263 25 4312 000",
        "address": "8 Robert Mugabe Way, Gweru",
        "registration_date": "2020-08-22", "last_audit_date": "2025-07-30",
    },
    {
        "id": "prov-005", "code": "PROV-HRE-00098", "name": "St Annes Hospital",
        "type": "HOSPITAL", "city": "Harare", "trust_score": 91, "badge": "VERIFIED",
        "shortfall_index": 1.02, "dispute_rate": 0.8, "flags_90d": 2,
        "total_claims": 4821, "average_claim_value": 285.00, "phone": "+263 24 2704 765",
        "address": "3 Churchill Ave, Harare",
        "registration_date": "2010-05-12", "last_audit_date": "2026-02-14",
    },
    {
        "id": "prov-006", "code": "PROV-HRE-00445", "name": "Parirenyatwa Specialists Clinic",
        "type": "SPECIALIST", "city": "Harare", "trust_score": 44, "badge": "REVIEW",
        "shortfall_index": 3.21, "dispute_rate": 14.7, "flags_90d": 31,
        "total_claims": 398, "average_claim_value": 124.50, "phone": "+263 24 2794 111",
        "address": "1 Mazowe St, Parirenyatwa, Harare",
        "registration_date": "2018-09-01", "last_audit_date": "2025-06-01",
    },
]

# Claims (with items, flags, shap, timeline) — keyed to member/provider seed ids.
CLAIMS = [
    {
        "id": "clm-001", "claim_ref": "CG-00291", "nh263_ref": "NH263-2026-0044821",
        "member_id": "mem-001", "provider_id": "prov-001",
        "service_date": "2026-06-28", "submitted_at": "2026-06-30T18:42:00Z",
        "claimed_amount": 88.00, "member_shortfall": 22.00,
        "expected_shortfall_min": 12, "expected_shortfall_max": 18,
        "risk_score": 89, "risk_level": "CRITICAL", "decision": "PEND_INVESTIGATE",
        "priority": "CRITICAL", "latency_ms": 680,
        "member_notification_sent": True, "member_notification_channel": "WHATSAPP",
        "member_response": "DISPUTED", "sla_deadline": "2026-07-02T18:42:00Z",
        "ai_explanation": (
            "Prescription was written 3 days after medication was dispensed. Member "
            "Tendai Moyo has no diabetes registration on file, yet three chronic "
            "diabetic medications were dispensed. No biometric confirmation was "
            "captured for a high-value transaction."
        ),
        "flags": [
            ("PRESCRIPTION_DATE_AFTER_SERVICE", "HIGH"),
            ("CHRONIC_DRUG_NO_CONDITION_REGISTERED", "HIGH"),
            ("HIGH_VALUE_NO_BIOMETRIC", "HIGH"),
        ],
        "items": [
            ("Insulin Human 100U/mL", 2, 19.00, 38.00, None, "726125001"),
            ("Metformin 500mg x 30", 1, 12.00, 12.00, None, "726044001"),
            ("Glibenclamide 5mg x 30", 1, 18.00, 18.00, None, "726098001"),
            ("Lisinopril 10mg x 30", 1, 20.00, 20.00, None, "726101001"),
        ],
        "shap": [
            ("Prescription date vs service date", 0.42, "positive"),
            ("Chronic condition not registered", 0.31, "positive"),
            ("Biometric confirmation absent", 0.18, "positive"),
            ("Claim value vs member history", 0.09, "positive"),
        ],
    },
    {
        "id": "clm-002", "claim_ref": "CG-00441", "nh263_ref": "NH263-2026-0044799",
        "member_id": "mem-002", "provider_id": "prov-002",
        "service_date": "2026-06-30", "submitted_at": "2026-06-30T14:18:00Z",
        "claimed_amount": 45.00, "member_shortfall": 25.00,
        "expected_shortfall_min": 8, "expected_shortfall_max": 12,
        "risk_score": 67, "risk_level": "HIGH", "decision": "PEND_VERIFY",
        "priority": "HIGH", "latency_ms": 420,
        "member_notification_sent": True, "member_notification_channel": "WHATSAPP",
        "member_response": "PENDING", "sla_deadline": "2026-07-03T14:18:00Z",
        "auto_approve_at": "2026-07-01T14:18:00Z",
        "ai_explanation": (
            "The member shortfall of $25 is significantly above the expected range of "
            "$8-$12 for a standard GP consultation at this provider tier. This pattern "
            "has been observed 4 times in the past 90 days at this provider."
        ),
        "flags": [("SHORTFALL_INFLATION_SUSPECTED", "MEDIUM")],
        "items": [("GP Consultation", 1, 45.00, 45.00, "I10", None)],
        "shap": [
            ("Shortfall vs expected range", 0.58, "positive"),
            ("Provider shortfall pattern", 0.24, "positive"),
            ("Member benefit utilisation", 0.18, "negative"),
        ],
    },
    {
        "id": "clm-003", "claim_ref": "CG-00882", "nh263_ref": "NH263-2026-0044805",
        "member_id": "mem-003", "provider_id": "prov-003",
        "service_date": "2026-06-30", "submitted_at": "2026-06-30T09:05:00Z",
        "claimed_amount": 22.00, "approved_amount": 22.00, "member_shortfall": 8.00,
        "expected_shortfall_min": 7, "expected_shortfall_max": 12,
        "risk_score": 18, "risk_level": "LOW", "decision": "APPROVE",
        "priority": "LOW", "latency_ms": 310,
        "member_notification_sent": False, "member_response": "CONFIRMED",
        "member_rating": 5, "sla_deadline": "2026-07-03T09:05:00Z",
        "ai_explanation": (
            "This claim meets all verification criteria. The medications are consistent "
            "with the member's registered hypertension condition. Shortfall is within "
            "the expected range. Provider Avenues Pharmacy Harare has a TrustScore of 97."
        ),
        "flags": [],
        "items": [
            ("Amlodipine 5mg x 30", 1, 14.00, 14.00, None, "726200001"),
            ("Hydrochlorothiazide 12.5mg x 30", 1, 8.00, 8.00, None, "726201001"),
        ],
        "shap": [
            ("Condition matches medication", -0.35, "negative"),
            ("Provider TrustScore 97", -0.28, "negative"),
            ("Shortfall within range", -0.25, "negative"),
            ("Member claim history consistent", -0.12, "negative"),
        ],
    },
    {
        "id": "clm-004", "claim_ref": "CG-00112", "nh263_ref": "NH263-2026-0044700",
        "member_id": "mem-001", "provider_id": "prov-006",
        "service_date": "2026-06-29", "submitted_at": "2026-06-29T16:22:00Z",
        "claimed_amount": 180.00, "member_shortfall": 85.00,
        "expected_shortfall_min": 30, "expected_shortfall_max": 50,
        "risk_score": 94, "risk_level": "CRITICAL", "decision": "PEND_INVESTIGATE",
        "priority": "CRITICAL", "latency_ms": 512,
        "member_notification_sent": True, "member_notification_channel": "WHATSAPP",
        "member_response": "PENDING", "sla_deadline": "2026-07-01T16:22:00Z",
        "ai_explanation": (
            "Multiple syndicate signals detected. This provider has 31 flags in 90 days. "
            "Member shortfall is 70% above expected. Statistical pattern matches 3 other "
            "members flagged this week."
        ),
        "flags": [
            ("STATISTICAL_ANOMALY_DETECTED", "MEDIUM"),
            ("POTENTIAL_FRAUD_SYNDICATE_DETECTED", "CRITICAL"),
            ("HIGH_VALUE_NO_BIOMETRIC", "HIGH"),
        ],
        "items": [
            ("Specialist Consultation", 1, 120.00, 120.00, None, None),
            ("Lab Panel CBC + Lipids", 1, 60.00, 60.00, None, None),
        ],
        "shap": [
            ("Syndicate pattern match", 0.45, "positive"),
            ("Provider flag frequency", 0.32, "positive"),
            ("Shortfall deviation", 0.23, "positive"),
        ],
    },
    {
        "id": "clm-005", "claim_ref": "CG-00088", "nh263_ref": "NH263-2026-0044680",
        "member_id": "mem-002", "provider_id": "prov-004",
        "service_date": "2026-06-28", "submitted_at": "2026-06-28T11:10:00Z",
        "claimed_amount": 35.00, "approved_amount": 35.00, "member_shortfall": 6.00,
        "expected_shortfall_min": 5, "expected_shortfall_max": 10,
        "risk_score": 22, "risk_level": "LOW", "decision": "APPROVE",
        "priority": "LOW", "latency_ms": 290,
        "member_notification_sent": False, "member_response": "CONFIRMED",
        "member_rating": 4, "sla_deadline": "2026-07-01T11:10:00Z",
        "ai_explanation": (
            "Clean claim. Medications align with registered hypertension condition. "
            "Provider TrustScore 78 is within acceptable range."
        ),
        "flags": [],
        "items": [
            ("Atenolol 50mg x 30", 1, 18.00, 18.00, None, None),
            ("Aspirin 100mg x 30", 1, 9.00, 9.00, None, None),
            ("Dispensing fee", 1, 8.00, 8.00, None, None),
        ],
        "shap": [
            ("Condition registered matches Rx", -0.40, "negative"),
            ("Claim value within norms", -0.35, "negative"),
            ("Provider history acceptable", -0.25, "negative"),
        ],
    },
    {
        # Sent back to City Pharmacy (the demo portal login) for a prescription.
        "id": "clm-006", "claim_ref": "CG-00517", "nh263_ref": "NH263-2026-0044850",
        "member_id": "mem-003", "provider_id": "prov-001",
        "service_date": "2026-07-01", "submitted_at": "2026-07-01T10:30:00Z",
        "claimed_amount": 64.00, "member_shortfall": 14.00,
        "expected_shortfall_min": 8, "expected_shortfall_max": 12,
        "risk_score": 58, "risk_level": "MEDIUM", "decision": "INFO_NEEDED",
        "priority": "MEDIUM", "latency_ms": 380,
        "member_response": "PENDING", "sla_deadline": None,
        "ai_explanation": (
            "Two months of a chronic medication were dispensed at once with no "
            "prescription on file for the second month. Shortfall is slightly above "
            "the expected range for this provider."
        ),
        "flags": [("STATISTICAL_ANOMALY_DETECTED", "MEDIUM")],
        "items": [("Metformin 500mg x 60", 2, 32.00, 64.00, None, "726300001")],
        "shap": [
            ("Quantity vs usual supply", 0.31, "positive"),
            ("Shortfall vs expected range", 0.17, "positive"),
            ("Member claim history consistent", -0.12, "negative"),
        ],
        "info_request": {
            "at": "2026-07-01T15:00:00Z", "actor": "Farai Nyathi",
            "message": "Please send the prescription covering the second month of Metformin.",
            "previous_decision": "PEND_VERIFY", "sla_left_hours": 30,
        },
    },
    {
        # Confirmed and rated by the member, so the portal's satisfaction score has data.
        "id": "clm-007", "claim_ref": "CG-00603", "nh263_ref": "NH263-2026-0044871",
        "member_id": "mem-002", "provider_id": "prov-001",
        "service_date": "2026-07-02", "submitted_at": "2026-07-02T09:15:00Z",
        "claimed_amount": 18.00, "approved_amount": 18.00, "member_shortfall": 4.00,
        "expected_shortfall_min": 3, "expected_shortfall_max": 6,
        "risk_score": 21, "risk_level": "LOW", "decision": "APPROVE",
        "priority": "LOW", "latency_ms": 300,
        "member_notification_sent": True, "member_notification_channel": "USSD",
        "member_response": "CONFIRMED", "member_rating": 4,
        "sla_deadline": "2026-07-04T09:15:00Z",
        "ai_explanation": (
            "Routine refill consistent with the member's registered hypertension. "
            "Shortfall is within the expected range."
        ),
        "flags": [],
        "items": [("Amlodipine 5mg x 30", 1, 18.00, 18.00, None, "726200001")],
        "shap": [
            ("Condition matches medication", -0.32, "negative"),
            ("Shortfall within range", -0.22, "negative"),
        ],
    },
]
