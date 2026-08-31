# Aurora Digital Banking — upload test dataset

A second, **different-domain** dataset (retail banking, not e-commerce) so that after
you upload it the dashboard numbers visibly change — proof the upload was actually used
and the built-in demo data was bypassed.

| Upload slot | File | What it is |
|---|---|---|
| **Requirements SRS** | `aurora_banking_srs.md` | 8 requirements, prefixed IDs `BNK-REQ-01 … BNK-REQ-08` |
| **OpenAPI Spec v1** | `aurora_v1.json` | 15 endpoints, fully compliant baseline |
| **OpenAPI Spec v2** | `aurora_v2.json` | 13 endpoints — step-up auth removed, transfer idempotency weakened |

## Expected results (threshold 0.55) — verified by running the real pipeline

### Traceability (v1)
| Metric | Value |
|---|---|
| Overall compliance | **100.0%** |
| Requirements | **8** |
| Endpoints | **15** |
| Compliant pairs (cells ≥ 0.55) | **15** |
| 3D graph nodes | ~23 (8 req + 15 endpoint) |

Best match per requirement, all above threshold:

| Req | Best endpoint | Score |
|---|---|---|
| BNK-REQ-01 | `POST /auth/login` | 0.68 |
| BNK-REQ-02 | `GET /accounts/{id}/statement` | 0.71 |
| BNK-REQ-03 | `POST /beneficiaries` | 0.69 |
| BNK-REQ-04 | `POST /transfers` | 0.85 |
| BNK-REQ-05 | `POST /standing-instructions` | 0.76 |
| BNK-REQ-06 | `PUT /cards/{id}/controls` | 0.81 |
| BNK-REQ-07 | `POST /auth/step-up/challenge` | 0.79 |
| BNK-REQ-08 | `POST /disputes` | 0.80 |

### Drift (v1 → v2)
| KPI | Value |
|---|---|
| Compliance v1 | 100.0% |
| Compliance v2 | **87.5%** (7 / 8) |
| Compliance delta | **−12.5%** |
| Drifted pairs | 17 |
| Critical drifts | **2** |
| Uncovered requirements in v2 | 1 → **BNK-REQ-07** |

- **BNK-REQ-07 (step-up auth)** — both `step-up/challenge` and `step-up/verify` removed in v2 →
  2× *Critical Drift*, requirement's best score collapses 0.79 → 0.47 (now uncovered).
- **BNK-REQ-04 (transfer idempotency)** — `POST /transfers` drops **0.85 → 0.70, Δ −0.15**
  (*Minor Drift*, and unlike the e-commerce REQ-04 this one **does** cross the −0.10
  drift-page threshold, so it shows on the Drift page too).

### Benchmarks page — known limitation
Ground truth is only shipped for the e-commerce demo (`data/ground_truth/traceability_labels.csv`),
and the upload UI has no slot for a labels file. An uploaded banking project therefore has
**no matching ground-truth rows**, so the Benchmarks page will show empty / zero IR metrics.
That is expected, not a bug. Traceability, Drift and PR Bot pages are unaffected.
