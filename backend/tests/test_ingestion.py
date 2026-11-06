"""FraudShield signal derivation + claim ingestion pipeline."""
from __future__ import annotations

from httpx import AsyncClient

from app.modules.fraudshield.signals import SignalInputs, derive_input_signals


def _inp(**kw) -> SignalInputs:
    base = {
        "item_descriptions": [],
        "member_conditions": [],
        "provider_flags_90d": 0,
        "provider_trust_score": 100,
    }
    base.update(kw)
    return SignalInputs(**base)


# ─── Unit: signal derivation (pure) ──────────────────────────────────────────
def test_chronic_drug_without_condition_is_flagged():
    s = derive_input_signals(
        _inp(item_descriptions=["Insulin Human 100U/mL"], member_conditions=[])
    )
    assert s["chronic_drug_no_condition"] is True


def test_chronic_drug_with_matching_condition_is_clean():
    s = derive_input_signals(
        _inp(item_descriptions=["Amlodipine 5mg x 30"], member_conditions=["Hypertension"])
    )
    assert s["chronic_drug_no_condition"] is False


def test_non_chronic_item_is_clean():
    s = derive_input_signals(_inp(item_descriptions=["GP Consultation"], member_conditions=[]))
    assert s["chronic_drug_no_condition"] is False


def test_prescription_after_service_from_dates():
    assert derive_input_signals(
        _inp(service_date="2026-06-01", prescription_date="2026-06-05")
    )["prescription_after_service"] is True
    assert derive_input_signals(
        _inp(service_date="2026-06-05", prescription_date="2026-06-01")
    )["prescription_after_service"] is False
    assert derive_input_signals(_inp())["prescription_after_service"] is False


def test_has_biometric_defaults_true_but_respects_explicit_false():
    assert derive_input_signals(_inp())["has_biometric"] is True
    assert derive_input_signals(_inp(has_biometric=False))["has_biometric"] is False


def test_syndicate_signal_derived_from_provider_risk():
    # High flag velocity + low trust ⇒ watchlist-tier ⇒ syndicate proxy.
    assert derive_input_signals(
        _inp(provider_flags_90d=31, provider_trust_score=44)
    )["syndicate_signal"] is True
    # Trusted, clean provider ⇒ not syndicate.
    assert derive_input_signals(
        _inp(provider_flags_90d=0, provider_trust_score=97)
    )["syndicate_signal"] is False
    # Explicit upstream flag overrides the heuristic.
    assert derive_input_signals(
        _inp(provider_flags_90d=0, provider_trust_score=97, syndicate_signal=True)
    )["syndicate_signal"] is True


# ─── Integration: POST /claims/ingest ────────────────────────────────────────
async def test_ingest_requires_auth(client: AsyncClient):
    assert (await client.post("/api/v1/claims/ingest", json={})).status_code == 401


async def test_ingest_derives_and_persists_signals(client: AsyncClient, auth_headers: dict):
    # Tendai (no registered conditions) + a chronic drug at a watchlist provider.
    payload = {
        "memberNumber": "CIM-0291847",
        "providerCode": "PROV-HRE-00445",  # trust 44, flags 31 → syndicate proxy
        "serviceDate": "2026-07-05",
        "claimedAmount": 180,
        "memberShortfall": 85,
        "expectedShortfallMin": 30,
        "expectedShortfallMax": 50,
        "hasBiometric": False,
        "items": [{"description": "Insulin Human 100U/mL", "quantity": 1,
                   "unitPrice": 120, "total": 120}],
    }
    resp = await client.post("/api/v1/claims/ingest", headers=auth_headers, json=payload)
    assert resp.status_code == 200
    data = resp.json()["data"]
    ref = data["claimRef"]
    assert ref.startswith("CG-")
    assert data["riskLevel"] in {"HIGH", "CRITICAL"}
    # Derived signals surfaced as rule flags.
    assert "POTENTIAL_FRAUD_SYNDICATE_DETECTED" in data["flags"]
    assert "CHRONIC_DRUG_NO_CONDITION_REGISTERED" in data["flags"]

    # Signals were persisted → a rescore reproduces them (doesn't drop the syndicate).
    rescored = await client.post(f"/api/v1/claims/{ref}/rescore", headers=auth_headers)
    assert rescored.status_code == 200
    detail = (await client.get(f"/api/v1/claims/{ref}", headers=auth_headers)).json()["data"]
    assert "POTENTIAL_FRAUD_SYNDICATE_DETECTED" in detail["flags"]


async def test_ingest_clean_claim_scores_low(client: AsyncClient, auth_headers: dict):
    # Hypertensive member on hypertension meds at a trusted provider → clean.
    payload = {
        "memberNumber": "CIM-0441209",           # Joseph Chikwanda, Hypertension
        "providerCode": "PROV-HRE-00023",        # trust 97, flags 0
        "serviceDate": "2026-07-05",
        "claimedAmount": 30,
        "memberShortfall": 9,
        "expectedShortfallMin": 7,
        "expectedShortfallMax": 12,
        "items": [{"description": "Amlodipine 5mg x 30", "quantity": 1,
                   "unitPrice": 15, "total": 15}],
    }
    resp = await client.post("/api/v1/claims/ingest", headers=auth_headers, json=payload)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["riskLevel"] == "LOW"
    assert "CHRONIC_DRUG_NO_CONDITION_REGISTERED" not in data["flags"]
    assert "POTENTIAL_FRAUD_SYNDICATE_DETECTED" not in data["flags"]


async def test_ingest_is_idempotent_by_claim_ref(client: AsyncClient, auth_headers: dict):
    payload = {
        "claimRef": "CG-INGEST-1",
        "memberNumber": "CIM-0291847",
        "providerCode": "PROV-HRE-00023",
        "serviceDate": "2026-07-05",
        "claimedAmount": 40,
        "memberShortfall": 10,
        "expectedShortfallMin": 8,
        "expectedShortfallMax": 12,
        "items": [{"description": "GP Consultation", "quantity": 1, "unitPrice": 40, "total": 40}],
    }
    first = await client.post("/api/v1/claims/ingest", headers=auth_headers, json=payload)
    second = await client.post("/api/v1/claims/ingest", headers=auth_headers, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]


async def test_ingest_unknown_member_404(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/v1/claims/ingest",
        headers=auth_headers,
        json={"memberNumber": "CIM-NOPE", "providerCode": "PROV-HRE-00023",
              "claimedAmount": 10, "memberShortfall": 2,
              "expectedShortfallMin": 1, "expectedShortfallMax": 3,
              "items": [{"description": "GP Consultation", "quantity": 1,
                         "unitPrice": 10, "total": 10}]},
    )
    assert resp.status_code == 404
