# FraudShield — Putting Real Data into Production

This guide explains how to move FraudShield's ML model from the shipped
**synthetic** ensemble to one **trained on real claims**, so it detects real
fraud instead of the simulated patterns it currently learns.

> **TL;DR** — Build a labelled feature matrix `(X, y)` from real claims, call
> `train(X, y, data_source="production", …)`, drop the resulting artifacts into
> `app/modules/fraudshield/artifacts/`, and ship. Inference and explanations are
> unchanged because training and serving share one feature extractor.

---

## 1. Mental model

```
 real claims + outcomes ─▶ feature_row() ─▶ (X, y) ─▶ train() ─▶ artifacts ─▶ inference
        (your data)          (shared)                  (this script)   (baked)   (unchanged)
```

The only thing that changes between the demo and production is **the data**.
Everything downstream — feature extraction, the XGBoost + IsolationForest
ensemble, TreeSHAP explanations, the decision engine — is identical. This is
enforced by a single feature extractor, `app/modules/fraudshield/features.py`,
used by **both** training and live scoring, so there is no train/serve skew.

Key files:

| File | Role |
|------|------|
| `app/modules/fraudshield/features.py` | `FEATURE_NAMES`, `feature_row(ctx)` — the schema, shared by train + serve |
| `scripts/train_fraud_model.py` | `train(X, y, …)` — data-agnostic training/eval/persist |
| `app/modules/fraudshield/ml_trained.py` | loads the artifacts at inference |
| `app/modules/fraudshield/artifacts/` | `xgb.json`, `iforest.joblib`, `metadata.json` (baked into the image) |

---

## 2. What the model needs from each claim

### 2.1 Features (`X`)

Columns must be **exactly `FEATURE_NAMES`, in order**. Today that is:

| Feature | Source | Notes |
|---------|--------|-------|
| `shortfall_ratio` | `member_shortfall / mid(expected_shortfall)` | derived |
| `claimed_amount` | claim | |
| `provider_flags_90d` | provider | flag velocity |
| `low_trust` | `100 - provider.trust_score` | |
| `no_biometric` | `not has_biometric` | **input signal (see 2.3)** |
| `prescription_after_service` | claim | **input signal** |
| `chronic_drug_no_condition` | claim + member conditions | **input signal** |
| `syndicate_signal` | claim | **input signal** |
| `num_conditions` | `len(member.conditions)` | |
| `num_items` | `len(claim.items)` | |

Never reorder columns — the persisted model is bound to this order. To add
features, **append** to `FEATURE_NAMES` (+ `FEATURE_LABELS`) and retrain.

### 2.2 Label (`y`)

`y` is `1 = fraud`, `0 = legitimate`. Use **real adjudicated outcomes**, not a
heuristic — if you label with your own rules, the model just learns the rules
back. The natural source is investigation resolutions (see §4):

| `investigation.resolution` | label |
|----------------------------|:-----:|
| `CONFIRMED_FRAUD`, `RECOVERED` | `1` |
| `FALSE_POSITIVE`, `DATA_ERROR`, `NO_ACTION` | `0` |

Only include claims with a **settled outcome**. Unadjudicated claims are
unlabelled and must be excluded from training.

### 2.3 ⚠️ Input-signal parity at ingestion (critical)

Four features are boolean **input signals** persisted on the claim:
`syndicate_signal`, `has_biometric`, `prescription_after_service`,
`chronic_drug_no_condition`. For the model to use them in production, your
**claim-ingestion path (e.g. the NH263 webhook) must populate them on every new
claim** — the same fields the model saw in training. If real claims arrive with
defaults (`has_biometric=True`, the rest `False`), those four features carry no
information and the model is blind to them.

**This is wired.** The ingestion endpoint `POST /api/v1/claims/ingest`
(`ClaimService.ingest`) derives and persists all four signals on every claim as
it arrives, via `app/modules/fraudshield/signals.py`:

| Signal | How ingestion sets it |
|--------|-----------------------|
| `has_biometric` | from the payload (`hasBiometric`); defaults to `true` if the source omits it |
| `biometric_reported` | `false` when the source omits `hasBiometric`. The model still sees `has_biometric=true` (as trained); the `BIOMETRIC_NOT_REPORTED` rule flags the gap on claims ≥ US$75 |
| `prescription_after_service` | derived from `prescriptionDate` vs `serviceDate` |
| `chronic_drug_no_condition` | derived: a claim item is a chronic medication (`CHRONIC_MEDICATIONS`) the member has no matching registered condition for |
| `syndicate_signal` | the upstream flag **or** provider risk (`flags_90d ≥ 20` and `trust < 50`); the source can raise it, never clear it |

Rules that read claim history rather than the payload (computed at every score,
not features of the trained model):

| Rule | Looks at |
|------|----------|
| `DUPLICATE_CLAIM`, `CLAIM_SPLITTING_SUSPECTED`, `HIGH_CLAIM_FREQUENCY` | the member's other claims (same day / past 7 days) |
| `SHORTFALL_ABOVE_PEERS` | median shortfall ÷ claimed for the claim's main item (NAPPI code, else description) at **other** providers, once there are ≥ 5 of them. Independent of `expectedShortfallMin/Max`, which the submitter sends. Replace with a tariff table when Cimas/NH263 rates are available |

To adapt for your real feed: point your NH263/claims webhook at `ingest()` (or
map your payload to the `ClaimIngest` schema), and refine the derivation rules in
`signals.py` — extend `CHRONIC_MEDICATIONS`, and replace the `syndicate_signal`
heuristic with a real cross-claim/network detector when you have one.

---

## 3. Build `(X, y)` from the app's own database

If your labelled history lives in the ClaimGuard DB (claims + resolved
investigations), build the matrix directly with the same context builder the
scorer uses. Save as `scripts/build_training_set.py`:

```python
"""Build (X, y) from adjudicated claims in the ClaimGuard DB."""
import asyncio
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import SessionFactory
from app.models.claim import Claim
from app.models.investigation import Investigation
from app.modules.fraudshield.context import ScoringContext
from app.modules.fraudshield.features import feature_row

FRAUD = {"CONFIRMED_FRAUD", "RECOVERED"}
LEGIT = {"FALSE_POSITIVE", "DATA_ERROR", "NO_ACTION"}


def context_from_claim(claim: Claim) -> ScoringContext:
    mid = (float(claim.expected_shortfall_min) + float(claim.expected_shortfall_max)) / 2 or 1.0
    return ScoringContext(
        claim_ref=claim.claim_ref,
        claimed_amount=float(claim.claimed_amount),
        member_shortfall=float(claim.member_shortfall),
        expected_shortfall_min=float(claim.expected_shortfall_min),
        expected_shortfall_max=float(claim.expected_shortfall_max),
        provider_trust_score=claim.provider.trust_score if claim.provider else 100,
        provider_flags_90d=claim.provider.flags_90d if claim.provider else 0,
        member_conditions=claim.member.conditions if claim.member else [],
        item_descriptions=[i.description for i in claim.items],
        prescription_after_service=claim.prescription_after_service,
        has_biometric=claim.has_biometric,
        chronic_drug_no_condition=claim.chronic_drug_no_condition,
        syndicate_signal=claim.syndicate_signal,
    )


async def build():
    async with SessionFactory() as s:
        # Map claim_id -> label from *resolved* investigations only.
        invs = (await s.execute(
            select(Investigation).where(Investigation.resolution.isnot(None))
        )).scalars().all()
        label = {}
        for inv in invs:
            if inv.resolution in FRAUD:
                label[inv.claim_id] = 1
            elif inv.resolution in LEGIT:
                label[inv.claim_id] = 0

        claims = (await s.execute(
            select(Claim)
            .where(Claim.id.in_(label))
            .options(selectinload(Claim.provider), selectinload(Claim.member),
                     selectinload(Claim.items))
        )).scalars().all()

        X = np.asarray([feature_row(context_from_claim(c)) for c in claims], dtype=np.float32)
        y = np.asarray([label[c.id] for c in claims], dtype=int)
        np.savez("training_set.npz", X=X, y=y)
        print(f"built {len(y):,} rows, fraud={y.mean():.2%}")

if __name__ == "__main__":
    asyncio.run(build())
```

> Note: `context_from_claim` here mirrors the one in `app/services/claims.py`;
> reuse that method if you prefer (`ClaimService(session).context_from_claim`).

You will typically need **thousands** of labelled claims with a **few hundred**
confirmed frauds before a trained model beats the rules. With only a handful of
resolved cases, keep `ML_ENGINE=heuristic` until enough labels accumulate.

---

## 4. Build `(X, y)` from an external export

If labels live outside the app (a CSV/parquet export from the claims system),
construct a `ScoringContext` per row and call `feature_row()` — do **not**
hand-build feature vectors, so the transform stays identical to serving:

```python
import numpy as np, pandas as pd
from app.modules.fraudshield.context import ScoringContext
from app.modules.fraudshield.features import feature_row

df = pd.read_parquet("claims_export.parquet")   # your columns
rows, y = [], []
for r in df.itertuples():
    ctx = ScoringContext(
        claim_ref=r.claim_ref,
        claimed_amount=r.claimed_amount,
        member_shortfall=r.member_shortfall,
        expected_shortfall_min=r.expected_min,
        expected_shortfall_max=r.expected_max,
        provider_trust_score=r.provider_trust_score,
        provider_flags_90d=r.provider_flags_90d,
        member_conditions=list(r.member_conditions or []),
        item_descriptions=list(r.item_descriptions or []),
        prescription_after_service=bool(r.rx_after_service),
        has_biometric=bool(r.has_biometric),
        chronic_drug_no_condition=bool(r.chronic_no_condition),
        syndicate_signal=bool(r.syndicate_signal),
    )
    rows.append(feature_row(ctx)); y.append(int(r.is_fraud))
X, y = np.asarray(rows, dtype=np.float32), np.asarray(y, dtype=int)
```

---

## 5. Train and persist

```python
import numpy as np
from scripts.train_fraud_model import train

d = np.load("training_set.npz")
train(
    d["X"], d["y"],
    data_source="production",
    model_version="fraudshield-prod-2026-07",   # bump every retrain
)   # writes xgb.json, iforest.joblib, metadata.json to the artifacts dir
```

`train()` handles the train/test split, class-imbalance weighting, operating-
threshold selection, IsolationForest fit (contamination = observed fraud rate),
and metric reporting (ROC-AUC, PR-AUC, precision, recall, F1). `metadata.json`
records provenance — `data: "production"`, `model_version`, `fraud_rate`, and
the metrics — which surface in the audit trail via the model name on each score.

**Prefer a time-based split for the real validation read** (train on the past,
test on the most recent months) rather than the built-in random split, which is
optimistic for fraud that evolves over time.

---

## 6. Deploy the new model

The artifacts are baked into the backend image (`COPY . .`) and selected by
`ML_ENGINE`:

| `ML_ENGINE` | Behaviour |
|-------------|-----------|
| `auto` (default) | use the trained ensemble if artifacts are present, else the heuristic |
| `trained` | force the trained ensemble (error if artifacts missing) |
| `heuristic` | force the dependency-free logistic model |

1. Commit the regenerated `app/modules/fraudshield/artifacts/` (or mount them and
   point `ML_ARTIFACTS_DIR` at the mount for zero-rebuild swaps).
2. Rebuild/redeploy the `api` + `worker` images.
3. Confirm the active model: a scored claim's `modelName` should be your new
   `model_version` (visible on the claim detail / audit trail).
4. Backfill existing scores if desired via `POST /api/v1/claims/{ref}/rescore`.

**Zero-rebuild option:** set `ML_ARTIFACTS_DIR=/models/fraudshield` and mount a
volume there; drop new artifacts in and restart the container.

---

## 7. Validate, roll out, roll back

- **Gate on metrics** before promoting: e.g. require PR-AUC and recall on the
  time-based holdout to meet your bar; store them in `metadata.json`.
- **Shadow first** (recommended): keep `ML_ENGINE=heuristic` live while you
  rescore a sample with the trained model and compare flagged rates / precision
  against investigation outcomes.
- **Instant rollback:** set `ML_ENGINE=heuristic` (or `auto` with the artifacts
  removed) and restart — the pipeline degrades gracefully, no code change.
- **Calibration:** if the trained probabilities shift the approve/verify/
  investigate mix undesirably, re-tune the ensemble weights (`metadata.json`
  `ensemble_weights`) and the `DecisionEngine` fusion/thresholds
  (`app/modules/fraudshield/decision_engine.py`) — the runtime decision is
  `0.6 * rules + 0.4 * model`, so the model's calibration matters.

---

## 8. Production readiness checklist

- [ ] **Labels** are real adjudicated outcomes, not heuristics (§2.2).
- [ ] **Ingestion** populates the four input signals on every new claim (§2.3).
- [ ] Enough labelled history (thousands of claims, hundreds of frauds).
- [ ] `FEATURE_NAMES` enriched with history/velocity/network features if needed.
- [ ] **Time-based** validation, metrics meet your bar and are recorded.
- [ ] `model_version` bumped; `data: "production"` in `metadata.json`.
- [ ] Ensemble weights / decision thresholds recalibrated on the real distribution.
- [ ] Rollback path (`ML_ENGINE=heuristic`) tested.
- [ ] Retraining cadence scheduled (fraud drifts — retrain monthly/quarterly on
      fresh outcomes).

---

## 9. Retraining cadence

Fraud patterns drift, so treat the model as perishable: retrain on a schedule
(monthly or quarterly) using the latest resolved investigations, validate on the
newest window, bump `model_version`, and redeploy. Keep prior artifacts +
`metadata.json` for auditability and quick rollback.
