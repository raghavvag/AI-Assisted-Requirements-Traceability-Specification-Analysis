# AI-Assisted Requirements Traceability and Software Specification Analysis

> **Document Type:** Project Knowledge Base — Single Source of Truth  
> **Version:** 1.0  
> **Last Updated:** 2026-08-30  
> **Status:** Approved for Execution  
>
> **Update 2026-10-16 (Project-II, M2):** the Streamlit dashboard (`app/streamlit_app.py`, with
> `src/auth.py` / `src/database.py`) has been **retired**. The product UI is the React + FastAPI
> app (`frontend/` + `backend/`), which uses the same `src/` pipeline. Streamlit mentions below are
> kept as the record of the v1.0 prototype. Current plan: `docs/plan.md`; checklist: `docs/leftover.txt`.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Proposed Solution](#3-proposed-solution)
4. [Key Objectives](#4-key-objectives)
5. [Addressed Research Gaps](#5-addressed-research-gaps)
6. [Expected Outcomes](#6-expected-outcomes)
7. [Technical Architecture Overview](#7-technical-architecture-overview)
8. [Scope & Boundaries](#8-scope--boundaries)
9. [Definitions & Terminology](#9-definitions--terminology)
10. [References & Prior Art](#10-references--prior-art)

---

## 1. Executive Summary

Modern software teams operate in an environment of constant change — sprint cycles compress delivery timelines, microservice architectures fragment system contracts across dozens of APIs, and business stakeholders continuously revise functional requirements. The gap that forms between *what the business intended* and *what the software actually implements* is not merely a documentation problem; it is a correctness problem that leads to silent failures, compliance violations, and costly rework.

This project designs and develops an **AI-Powered Requirement Engineering Framework** that continuously verifies whether evolving software implementations conform to their original business requirements. The framework employs **transformer-based semantic embeddings** to bridge the linguistic gap between natural language requirements (written by analysts) and machine-readable API specifications (written by engineers), enabling fully automated, explainable compliance verification at every stage of the software development lifecycle.

The system targets three primary user personas: **QA Leads** who need requirement-to-implementation traceability matrices, **Software Architects** who need to audit API specification drift across versions, and **Developers** who need real-time PR feedback before code is merged.

---

## 2. Problem Statement

### 2.1 The Core Challenge: Specification Drift

Software development is an inherently iterative process. Business requirements defined at project inception evolve continuously through stakeholder negotiations, market changes, and technical discoveries. API specifications — the machine-readable contracts that translate requirements into implementable behaviour — are modified, extended, deprecated, and occasionally broken with every sprint.

**Specification Drift** is the phenomenon where the implemented software's API contract semantically diverges from the original business requirements without any formal review or traceability record. This drift is:

- **Silent:** No automated system raises an alert when an API change violates a business rule.
- **Cumulative:** Small semantic deviations compound across dozens of sprints into major compliance failures.
- **Asymmetric:** Engineers modify APIs using technical terminology; analysts write requirements in business language. No direct lexical mapping exists between the two.
- **Consequential:** In regulated domains (finance, healthcare, e-commerce), undetected drift causes audit failures, regulatory fines, and customer-impacting defects.

### 2.2 Illustrative Failure Scenario

Consider an e-commerce platform with the following business requirement:

> **REQ-04:** "All refund transactions must include a unique `transaction_id` for idempotent processing and financial audit compliance."

In API version `v1.0`, the endpoint `POST /api/v1/refund` correctly accepts `transaction_id` as a required request body parameter. In `v2.0`, a developer refactors the endpoint to `POST /api/v2/refund` and silently removes `transaction_id` in favour of a server-generated UUID, believing this is an internal implementation detail. No test fails. No lint rule triggers. But REQ-04 is now violated — the audit trail is broken, and the system is non-compliant.

Traditional tools cannot detect this because:
1. **Keyword search** fails: "transaction_id" no longer appears in the spec, but the *concept* of unique transaction identification is gone.
2. **Schema validators** only verify structural well-formedness, not semantic intent.
3. **Manual review** is impractical at scale: teams may manage 50–200 API endpoints and hundreds of requirements.

### 2.3 Scale of the Problem

| Metric | Industry Reality |
|---|---|
| Average enterprise API endpoints per service | 40–120 |
| Average sprint velocity (API changes/week) | 5–15 endpoint modifications |
| Requirement-to-implementation verification cadence | Typically once per release (if at all) |
| Cost of post-release compliance failure | $50K–$2M per incident (regulated domains) |
| Existing automated semantic verification tools | **None mainstream** |

### 2.4 Why Existing Approaches Fall Short

| Approach | Limitation |
|---|---|
| Traditional Requirements Traceability Tools (e.g., Jama, Jira linking) | Link-based, manual, no semantic understanding; breaks silently when APIs change |
| API Linting (e.g., Spectral, Swagger Validator) | Validates structural format only; no awareness of business requirements |
| Static Code Analysis | Source code level; cannot reason about contract semantics or business intent |
| Keyword/TF-IDF Matching | Surface-level lexical overlap; fails for synonyms, paraphrases, and domain terminology mismatches |
| Manual Peer Review | Unscalable, inconsistent, and error-prone under sprint pressure |

---

## 3. Proposed Solution

### 3.1 The AI-Powered Requirement Engineering Framework

The proposed framework is an **end-to-end automated compliance verification system** that operates across three interconnected layers:

```
+------------------------------------------------------------------+
|                    INGESTION LAYER                               |
|  Natural Language SRS / Markdown / User Stories                  |
|  OpenAPI / Swagger JSON / YAML Specifications (multi-version)    |
+----------------------------+-------------------------------------+
                             |
+----------------------------v-------------------------------------+
|                 SEMANTIC EMBEDDING LAYER                         |
|  sentence-transformers (all-mpnet-base-v2 / text-embedding-3)    |
|  Chunking -> Dense Vector Encoding -> FAISS / Cosine Similarity  |
+----------------------------+-------------------------------------+
                             |
+----------------------------v-------------------------------------+
|            ANALYSIS & REASONING LAYER                            |
|  Traceability Matrix Generator                                   |
|  Multi-Version Drift Detector                                    |
|  LLM Explainability Engine (Groq / OpenAI GPT-4o-mini)          |
|  CI/CD PR Review Simulator                                       |
+----------------------------+-------------------------------------+
                             |
+----------------------------v-------------------------------------+
|                  PRESENTATION LAYER                              |
|  React + FastAPI web app (Heatmaps, 3D graph, KPI cards, PR Bot) |
+------------------------------------------------------------------+
```

### 3.2 Core Mechanism: Semantic Traceability

The framework addresses the *linguistic gap* between requirements and API specs by encoding both into a shared high-dimensional semantic embedding space using transformer models. Once embedded, **cosine similarity** measures the degree to which an API endpoint's contract semantically satisfies a business requirement, regardless of surface-level vocabulary differences.

**Similarity Score Interpretation:**

| Score Range | Compliance Category | Colour Code | Action |
|---|---|---|---|
| >= 0.75 | Fully Compliant | Green | No action required |
| 0.50 – 0.74 | Partially Compliant | Amber | Flag for human review |
| < 0.50 | Non-Compliant / Orphaned | Red | Mandatory remediation |

### 3.3 Drift Detection Mechanism

The framework maintains a **historical alignment score baseline** for each requirement–endpoint pair. When a new API version is uploaded or a PR diff is submitted:

1. Re-embed the modified endpoints.
2. Recompute similarity scores against the existing requirement baseline.
3. Calculate delta scores: `delta = score_new - score_baseline`.
4. Flag pairs where `delta < -0.10` (configurable threshold) as **drifted**.
5. Invoke the LLM explainability module to generate a human-readable root cause analysis.

### 3.4 LLM Explainability Engine

For every flagged drift or compliance violation, the system constructs a structured prompt to an LLM (**OpenAI GPT-4o-mini primary**, Groq Llama-3.3-70B fallback):

```
System: You are a software compliance auditor. Analyze the following and
        explain the exact semantic failure.

Requirement: {requirement_text}
API v1.0 Endpoint: {endpoint_v1_schema}
API v2.0 Endpoint: {endpoint_v2_schema}
Similarity Score Drop: {delta}

Explain: (1) What specific contract element changed, (2) Which part of the
requirement is now unmet, (3) What the developer must do to restore compliance.
```

This guarantees **zero ungrounded black-box scores** — every alignment decision carries a traceable, human-readable justification.

### 3.5 CI/CD Integration Model

The PR Review Simulator models the behaviour of a GitHub Actions bot. When a developer submits a PR modifying an API spec:

1. The system parses the diff to extract changed endpoint schemas.
2. Re-runs the compliance engine against the requirement baseline.
3. Generates a structured PR comment mimicking GitHub's review annotation format.

---

## 4. Key Objectives

### Objective 1 — Semantic Traceability Establishment

> **Establish semantic traceability between software requirements and OpenAPI specifications using transformer-based embeddings.**

**Technical Approach:**
- Parse natural language SRS documents into discrete, semantically coherent requirement chunks (one requirement per chunk, preserving context).
- Parse OpenAPI JSON/YAML specifications into endpoint-level descriptors, combining HTTP method, path, `summary`, `description`, `requestBody` schema, and `responses` schema into a unified text representation.
- Encode all chunks using `sentence-transformers/all-mpnet-base-v2` (768-dimensional dense vectors) or OpenAI `text-embedding-3-small` (1536-dimensional vectors).
- Construct a bidirectional `N x M` Traceability Matrix where `N` = number of requirements and `M` = number of API endpoints, with each cell containing the cosine similarity score.

**Success Criterion:** The matrix correctly identifies ground-truth requirement–endpoint links with Precision >= 0.85 and Recall >= 0.85 (F1 >= 0.85) on the synthetic E-Commerce SRS dataset.

---

### Objective 2 — Automated Drift Detection

> **Design a semantic similarity-based verification mechanism to automatically detect requirement non-compliance and specification drift during software evolution.**

**Technical Approach:**
- Maintain versioned snapshots of alignment score matrices (stored as NumPy arrays or Pandas DataFrames, serialised to CSV/pickle).
- Implement a Drift Detector module that performs element-wise delta computation between consecutive version matrices.
- Classify drift severity using configurable thresholds: Minor (delta < -0.10), Major (delta < -0.25), Critical (delta < -0.50).
- Generate a Drift Report listing: drifted endpoint, affected requirement(s), old/new scores, delta magnitude, and a severity classification.

**Success Criterion:** The drift detector correctly identifies all 2 intentionally introduced breaking changes in the synthetic `store_v1.json -> store_v2.json` dataset with zero false negatives.

---

### Objective 3 — CI/CD Integration with Explainable PR Reviews

> **Integrate with CI/CD workflows to provide explainable pull request reviews and real-time developer feedback before deployment.**

**Technical Approach:**
- Develop a `pr_reviewer.py` module that accepts a "PR diff" (simulated as a JSON payload containing before/after endpoint schemas) as input.
- Re-run the compliance engine on modified endpoints only (partial re-embedding for performance).
- For each regression (score drop below threshold), invoke the LLM explainability engine with a structured prompt.
- Output a formatted PR review comment (Markdown) that can be posted by a GitHub Actions workflow via the GitHub REST API.
- Simulate the full flow in the web app's "PR Bot" page.

**Success Criterion:** The PR simulator correctly generates actionable, non-hallucinated explanations for all injected drift scenarios, verified by manual review against ground-truth change descriptions.

---

### Objective 4 — Evaluation with IR Metrics

> **Evaluate the framework using standard information retrieval metrics (Precision, Recall, F1-score, and MAP).**

**Technical Approach:**
- Construct a **Ground Truth** annotation set: for each of the 10 synthetic requirements, manually annotate which API endpoints satisfy it (binary relevance labels).
- Define retrieval as: for each requirement, rank all API endpoints by cosine similarity score (descending).
- Compute at threshold tau = 0.70:
  - **Precision@K:** Fraction of retrieved endpoints that are relevant.
  - **Recall@K:** Fraction of relevant endpoints that are retrieved.
  - **F1-Score:** Harmonic mean of Precision and Recall.
  - **MAP (Mean Average Precision):** Average of Average Precision scores across all requirements.
- Compare against two baseline approaches: **Keyword TF-IDF** matching and **BM25** retrieval.

**Success Criterion:** Transformer-based approach achieves MAP >= 0.85, outperforming TF-IDF and BM25 baselines by >= 15 percentage points.

---

## 5. Addressed Research Gaps

### Gap 1 — Lack of End-to-End Semantic Verification

**Current State of the Art:** Existing requirements traceability tools (IBM DOORS, Jama Connect, Jira) operate on link-based, manually maintained traceability artefacts. They have no mechanism to semantically verify whether an implementation *actually satisfies* the intent of a requirement.

**How This Project Addresses It:** The framework provides a fully automated, end-to-end pipeline from natural language requirement ingestion through transformer embedding to cosine similarity scoring, delivering semantic (not just syntactic) verification without any manual linking.

---

### Gap 2 — Limited Semantic Specification Drift Detection

**Current State of the Art:** API change management tools (e.g., OpenAPI Diff, Bump.sh) detect *structural* changes (added/removed fields, changed types) but cannot assess whether a structural change *semantically violates* a business requirement. A field rename that preserves business logic is flagged the same as one that destroys it.

**How This Project Addresses It:** By comparing semantic similarity scores before and after API version changes, the framework detects *meaningful* drift — changes that materially alter the degree to which an endpoint satisfies its linked requirements — rather than surface-level structural churn.

---

### Gap 3 — Limited Integration with Modern CI/CD Pipelines

**Current State of the Art:** Requirement verification is typically a phase-gate activity performed during formal review milestones (SIT, UAT), not embedded in the continuous delivery pipeline. By the time drift is detected, significant rework is required.

**How This Project Addresses It:** The PR Review Simulator integrates semantic compliance checking directly into the pull request workflow, providing developer feedback *before* code is merged — aligning with shift-left testing principles and enabling zero-drift continuous delivery.

---

### Gap 4 — Limited Use of API Specifications for Requirement Compliance

**Current State of the Art:** Most requirement compliance research focuses on source code (software traceability literature) or test case coverage. OpenAPI/Swagger specifications — which are the primary *contract artefact* in API-first development — are almost entirely absent from the requirements engineering literature.

**How This Project Addresses It:** This framework treats OpenAPI specifications as first-class compliance artefacts, parsing them at the endpoint, parameter, and schema level to construct rich semantic representations that can be directly compared against requirement embeddings.

---

### Gap 5 — Lack of Explainable Compliance Assessment

**Current State of the Art:** Even where semantic similarity scoring is applied (in academic prototypes), the output is a numeric score with no human-readable justification. Practitioners cannot act on a number alone — they need to understand *why* a score changed and *what* to do about it.

**How This Project Addresses It:** Every compliance score is backed by an LLM-generated natural language explanation that identifies the specific requirement clause unmet, the specific API schema element responsible, and a concrete remediation recommendation. This provides the explainability required for audit trails and developer communication.

---

## 6. Expected Outcomes

### 6.1 Primary Deliverable — Functional AI-Assisted Prototype

A fully operational software prototype with the following verified capabilities:

| Capability | Verification Method |
|---|---|
| Ingest SRS Markdown and OpenAPI JSON/YAML | Manual upload test with synthetic dataset |
| Generate N×M Traceability Matrix with cosine similarity scores | Output validation against ground truth |
| Detect specification drift across 2 API versions | Correct identification of all injected breaking changes |
| Generate LLM-powered PR review comments for drift violations | Manual review for factual accuracy and actionability |
| Display interactive Plotly heatmap and drift timeline in the web UI | UI functional test |
| Report Precision, Recall, F1, and MAP metrics | Automated metric computation script |

### 6.2 Research & IP Outcomes

| Outcome | Target | Timeline |
|---|---|---|
| **Provisional Patent Filing** | "System and Method for AI-Assisted Semantic Requirements Traceability and Specification Drift Detection" | Within 3 months of prototype completion |
| **Conference Manuscript** | Scopus-indexed venue (e.g., ICSE, ASE, ISSTA, or equivalent) | Submission within 2 months of prototype completion |
| **Journal Manuscript** | Scopus-indexed journal (e.g., JSS, IST, IEEE TSE) | Submission within 4 months of prototype completion |

### 6.3 Performance Targets

| Metric | Target Value | Measurement Condition |
|---|---|---|
| F1-Score (Traceability) | > 0.85 | Synthetic E-Commerce SRS, 10 requirements, >= 20 endpoints |
| MAP (Traceability Ranking) | > 0.85 | Same dataset, tau = 0.70 threshold |
| Drift Detection Recall | 100% | All injected breaking changes in v1.0 -> v2.0 transition |
| Latency (Matrix Generation) | < 5 seconds | Standard OpenAPI spec, < 50 endpoints |
| Explainability Coverage | 100% | Every flagged violation has LLM-generated justification |
| False Positive Rate (Drift) | < 10% | Non-breaking changes correctly classified as non-drifted |

---

## 7. Technical Architecture Overview

### 7.1 Project Directory Structure

```
project/
├── data/
│   ├── requirements/
│   │   └── ecommerce_srs.md          # Synthetic SRS (10 requirements)
│   ├── api_specs/
│   │   ├── store_v1.json             # OpenAPI v1.0 (baseline, compliant)
│   │   └── store_v2.json             # OpenAPI v2.0 (with 2 breaking drifts)
│   └── ground_truth/
│       └── traceability_labels.csv   # Manual relevance annotations
├── src/
│   ├── parser.py                     # SRS + OpenAPI ingestion & chunking
│   ├── embedder.py                   # Transformer embedding engine
│   ├── similarity.py                 # Cosine similarity & matrix builder
│   ├── drift_detector.py             # Version comparison & drift scoring
│   ├── explainer.py                  # LLM prompt builder & response parser
│   ├── pr_reviewer.py                # PR diff simulator & review generator
│   └── evaluator.py                  # Precision, Recall, F1, MAP calculator
├── backend/                          # FastAPI API (auth, projects, analysis)
├── frontend/                         # React 18 + Vite web app
├── tests/
│   └── test_pipeline.py              # Unit + integration tests
├── requirements.txt
├── .env.example                      # API key template
├── knowledge.md                      # This document
└── roadmap.md                        # Execution roadmap
```

### 7.2 Data Flow Summary

```
[SRS .md] ─────────────────────────────────────────────────┐
                                                            v
                                               [parser.py: extract_requirements()]
                                                            |
                                                            | List[str] requirement chunks
[OpenAPI .json/.yaml] ──────────────────────┐              v
                                            v    [embedder.py: encode()]
                               [parser.py:  |          | np.ndarray (N x 768)
                           extract_endpoints()]         |
                                            |    [embedder.py: encode()]
                                            |          | np.ndarray (M x 768)
                                            └──────────┤
                                                       v
                                          [similarity.py: cosine_matrix()]
                                                       | np.ndarray (N x M)
                                           ┌───────────┴────────────┐
                                           v                        v
                               [drift_detector.py]         [evaluator.py]
                               compare v1 vs v2            Precision/Recall/F1/MAP
                                           |
                                           v
                                [explainer.py: LLM call]
                                           |
                                           v
                                [pr_reviewer.py: format]
                                           |
                                           v
                                [React web app: display]
```

---

## 8. Scope & Boundaries

### In Scope

- Natural language SRS ingestion (Word .docx, PDF, Markdown, plain text; see docs/srs-format-guide.md)
- OpenAPI 3.0 / Swagger 2.0 JSON and YAML parsing
- Transformer-based semantic embedding (local `sentence-transformers` or API-based)
- Cosine similarity-based traceability matrix generation
- Multi-version API drift detection (2-version comparison)
- LLM-powered explainability for flagged violations
- Interactive web dashboard (React + FastAPI)
- Simulated PR review comment generation
- IR evaluation metrics (Precision, Recall, F1, MAP)

### Out of Scope (v1.0 Prototype)

- Real-time GitHub Actions webhook integration (simulated only)
- Source code analysis (traceability is limited to API specifications)
- Support for non-OpenAPI specification formats (RAML, WSDL, gRPC)
- Multi-tenant / cloud-hosted production deployment
- Database persistence of historical matrices across sessions
- Fine-tuning of the embedding model on domain-specific data

---

## 9. Definitions & Terminology

| Term | Definition |
|---|---|
| **SRS** | Software Requirements Specification — a structured document describing the functional and non-functional requirements of a system |
| **OpenAPI Specification** | A standard, language-agnostic interface description for REST APIs (formerly Swagger), defined in JSON or YAML |
| **Specification Drift** | The phenomenon where an API's implemented contract diverges from the business requirements that originally motivated it |
| **Semantic Embedding** | A dense, real-valued vector representation of text that encodes contextual meaning, generated by a transformer model |
| **Cosine Similarity** | A measure of similarity between two non-zero vectors in an inner product space, computed as the cosine of the angle between them; ranges from -1 to 1 |
| **Traceability Matrix** | A table that maps requirements to their implementing API endpoints, populated with compliance scores |
| **Transformer Model** | A deep learning architecture based on self-attention mechanisms, used here via the `sentence-transformers` library for encoding text into semantic embeddings |
| **MAP (Mean Average Precision)** | An information retrieval metric that averages the precision at each relevant item's rank position, then averages across all queries |
| **PR (Pull Request)** | A mechanism in version control workflows (e.g., GitHub) for proposing and reviewing code changes before merging |
| **CI/CD** | Continuous Integration / Continuous Deployment — automated pipelines for building, testing, and deploying software |
| **Drift Threshold** | A configurable minimum cosine similarity delta below which a change is classified as a compliance drift |
| **Ground Truth** | Manually annotated, authoritative labels indicating which requirement–endpoint pairs are genuinely compliant, used to evaluate model performance |

---

## 10. References & Prior Art

### Foundational Literature

1. **Reimers, N. & Gurevych, I. (2019).** Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks. *Proceedings of EMNLP 2019.* — Foundational paper for the `sentence-transformers` library used in this project.

2. **Cleland-Huang, J., Gotel, O., & Zisman, A. (2012).** *Software and Systems Traceability.* Springer. — Comprehensive reference for requirements traceability methodology.

3. **Hayes, J.H., Dekhtyar, A., & Sundaram, S.K. (2006).** Advancing Candidate Link Generation for Requirements Traceability: The Study of Methods. *IEEE Transactions on Software Engineering, 32(1).* — Seminal IR-based traceability paper establishing Precision/Recall evaluation methodology.

4. **OpenAPI Initiative (2023).** OpenAPI Specification v3.1.0. https://spec.openapis.org/oas/v3.1.0

### Related Tools & Libraries

| Tool / Library | Version | Role |
|---|---|---|
| `sentence-transformers` | >= 2.2.0 | Transformer-based semantic embedding |
| `faiss-cpu` | >= 1.7.0 | Approximate nearest neighbour similarity search |
| `scikit-learn` | >= 1.3.0 | Cosine similarity computation, TF-IDF baseline |
| `prance` / `openapi-spec-validator` | Latest | OpenAPI JSON/YAML parsing and validation |
| React 18 + `plotly.js` | Latest | Web dashboard, heatmap and drift timeline (frontend/) |
| `fastapi` | >= 0.111 | API backend (backend/) |
| `openai` / `groq` | Latest | LLM prompt orchestration and API access |
| `numpy` / `pandas` | Latest | Numerical computation and matrix management |
| `pytest` | >= 7.0 | Automated testing framework |

---

*This document serves as the authoritative project knowledge base. All design decisions, implementation choices, and evaluation criteria described herein are binding for the duration of the v1.0 prototype build. Any scope changes must be reflected in an updated version of this document.*
