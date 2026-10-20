# Validation Results — v1.0 Prototype

> **Generated:** 2026-08-31
> **Dataset:** synthetic E-Commerce SRS (10 requirements) · `store_v1.json` / `store_v2.json` (20 endpoints) · `traceability_labels.csv` (200 labels)
> **Model:** `sentence-transformers/all-mpnet-base-v2` (768-dim, L2-normalised, CPU)
> **Reproduce:** `pytest tests/ -q` (59 tests) and the snippet in §5

---

## 1. Success-criteria checklist

| Check | Target | Result | Status |
|---|---|---|---|
| Parser extracts 10 requirements | 10 | 10 | ✅ |
| Parser extracts 20 endpoints (v1 & v2) | 20 / 20 | 20 / 20 | ✅ |
| Embeddings L2-normalised, shape `(N, 768)` | yes | yes | ✅ |
| Similarity matrix values ∈ [0, 1] | yes | yes | ✅ |
| REQ-04 top match = refund endpoint | yes | `POST /api/v1/refund` | ✅ |
| REQ-01 top match = auth endpoint | yes | `POST /api/v1/auth/login` | ✅ |
| Drift: REQ-09 (2FA removed) detected | yes | delta −0.111, Minor Drift | ✅ |
| Drift: REQ-04 (idempotency removed) detected by matrix drift detector | yes | **partial** — see §4 | ⚠️ |
| Drift: REQ-04 detected by PR Reviewer (tiered rule) | yes | delta −0.069, flagged | ✅ |
| False positives on non-breaking PR (`add_endpoint`) | 0 | 0 | ✅ |
| Transformer F1 > TF-IDF F1 | yes | 0.71 vs 0.34 | ✅ |
| Latency, matrix gen (< 50 endpoints, CPU) | < 5 s | ~1.5 s (model warm) | ✅ |
| Every flagged violation has an explanation | 100 % | 100 % (LLM or template fallback) | ✅ |
| Test suite | green | 59 passed | ✅ |

---

## 2. IR metrics — Transformer vs TF-IDF baseline

Retrieval threshold τ = 0.40 (calibrated to the transformer's realistic
cross-domain score band; TF-IDF evaluated at the same τ for fairness).

| Method | Precision | Recall | F1-Score | MAP |
|---|---|---|---|---|
| **Transformer (`all-mpnet-base-v2`)** | 0.620 | **1.000** | **0.710** | 1.000 |
| TF-IDF baseline (1–2 grams, sublinear TF) | 0.500 | 0.275 | 0.340 | 1.000 |

**Reading of the results**

- **Recall is the decisive metric here.** The transformer retrieves *every*
  ground-truth requirement→endpoint link (Recall 1.0); TF-IDF misses ~72 % of
  them because analyst prose and API contract text rarely share surface tokens
  (e.g. "idempotent processing" vs `transaction_id`).
- **F1 +0.37 absolute (+109 % relative)** over the lexical baseline.
- **Precision 0.62** — the transformer also surfaces plausible-but-not-labelled
  endpoints (e.g. `checkout` for REQ-06 *payment*). Raising τ trades recall for
  precision; the dashboard exposes τ as a slider.
- **MAP = 1.0 for both** methods on this dataset: with 1–4 relevant endpoints out
  of 20 and clean domain separation, the single top-ranked endpoint is always
  relevant, so MAP saturates. MAP becomes discriminative only on larger/noisier
  corpora — it is retained for that reason.

---

## 3. Drift detection — `store_v1.json` → `store_v2.json`

Two breaking changes were injected. Matrix-level drift detector, τ_drift = −0.10:

| Requirement | Endpoint | score v1 | score v2 | Δ | Verdict |
|---|---|---|---|---|---|
| REQ-09 (2FA enforcement) | `POST /api/v2/auth/2fa/challenge` | 0.809 | 0.698 | **−0.111** | Minor Drift ✅ |
| REQ-03 (cart) | `POST /api/v2/auth/2fa/challenge` | 0.277 | 0.168 | −0.109 | Minor Drift (incidental) |

Overall compliance (fraction of requirements with a ≥ 0.50 match): **v1 = 100 %,
v2 = 100 %** — see the caveat below.

---

## 4. Known limitation — REQ-04 sub-threshold drift

The REQ-04 violation (refund drops the client-provided `transaction_id`) is a
**real but small** semantic signal:

```
REQ-04  ↔  refund endpoint     v1 = 0.819     v2 = 0.750     Δ = −0.069   (PR scenario #42)
```

The v2 refund endpoint still describes refunds, orders, amounts and reasons, so
`all-mpnet-base-v2` keeps it well above the 0.50 compliance line — the matrix
drift detector (τ_drift = −0.10) therefore does **not** flag it, and overall
compliance stays at 100 %.

**Mitigations in place**

1. **The review engine uses a tiered relative-drop rule** (`src/review/engine.py`
   `is_regression`, thresholds in `.traceability.yml`): for a
   strongly-linked pair (score_v1 ≥ 0.60) a drop of ≥ 0.05 is a violation. Under
   this rule the `remove_idempotency` PR scenario **is** blocked on REQ-04.
2. **Roadmap Phase D** (`docs/architecture/semantic-alignment-roadmap.md`):
   schema-field-level artefacts + a code/schema-aware encoder will make the
   removal of a *named required field* a first-class signal rather than a diffuse
   one.

This is documented rather than hidden: cross-domain sentence-embedding cosine has
a compressed dynamic range, and honest calibration (per-pair relative drop, not a
single global absolute threshold) is the correct engineering response.

---

## 5. Reproduction snippet

```python
from src.parser import extract_requirements, extract_endpoints
from src.embedder import SemanticEmbedder
from src.similarity import compute_similarity_matrix, build_traceability_matrix
from src.evaluator import load_ground_truth, generate_evaluation_report

reqs = extract_requirements("data/requirements/ecommerce_srs.md")
eps  = extract_endpoints("data/api_specs/store_v1.json")
emb  = SemanticEmbedder()
m    = build_traceability_matrix(
    reqs, eps, compute_similarity_matrix(emb.encode_requirements(reqs),
                                         emb.encode_endpoints(eps)))
gt   = load_ground_truth("data/ground_truth/traceability_labels.csv")
print(generate_evaluation_report(reqs, eps, m, gt, threshold=0.40))
```
