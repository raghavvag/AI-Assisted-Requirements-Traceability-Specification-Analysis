# Semantic Alignment Architecture & Roadmap

> **Status:** Design / conceptual groundwork
> **Owner:** Principal Full-Stack AI Engineer
> **Last updated:** 2026-08-31
> **Relates to:** `knowledge.md`, `roadmap.md`, `src/alignment/`

---

## 1. Why this document exists

The v1.0 prototype answers one question:

> *Does this **OpenAPI endpoint** semantically satisfy this **SRS requirement**?*

The next evolution generalises that to:

> *Does this **code change** semantically satisfy the **business mandate** (Jira ticket / issue) it claims to implement?*

Both are the **same shape of problem**: encode two natural-language-ish artefacts into a
shared semantic space, score their alignment, and gate a workflow on the result. This
document defines a single abstraction — the **Semantic Alignment Engine** — that both use
cases plug into, so we never rewrite the core twice.

---

## 2. The core abstraction

```
        ContextSource                         ArtifactSource
   (what the business asked for)         (what engineering produced)
   ┌───────────────────────────┐        ┌───────────────────────────┐
   │ SRS requirement           │        │ OpenAPI endpoint          │
   │ Jira story + AC           │        │ Python function / class   │
   │ RFC / ADR paragraph       │        │ Changed hunk / docstring  │
   └────────────┬──────────────┘        └────────────┬──────────────┘
                │  ContextUnit[]                     │  ArtifactUnit[]
                └───────────────┬───────────────────┘
                                ▼
                     AlignmentScorer.score()
             (embedding cosine  |  cross-encoder  |  Siamese-BERT)
                                │  AlignmentResult[]  (score ∈ [0,1] + evidence)
                                ▼
                        AlignmentGate.evaluate()
             (threshold policy → ALLOW / WARN / BLOCK + Markdown comment)
                                │
                ┌───────────────┴───────────────┐
                ▼                               ▼
      Streamlit / React dashboard      GitHub Actions PR check
```

The contracts live in [`src/alignment/base.py`](../../src/alignment/base.py):

| Protocol / class      | Responsibility                                                            |
|-----------------------|--------------------------------------------------------------------------|
| `ContextUnit`         | One atomic requirement/story/AC — `id`, `title`, `text`, `metadata`      |
| `ArtifactUnit`        | One atomic implementation unit — `id`, `kind`, `text`, `location`        |
| `ContextSource`       | Yields `ContextUnit`s from a source (SRS file, Jira API, …)              |
| `ArtifactSource`      | Yields `ArtifactUnit`s (OpenAPI spec, git diff, AST walk, …)             |
| `AlignmentScorer`     | `score(context, artifacts) -> list[AlignmentResult]`                     |
| `AlignmentResult`     | `context_id`, `artifact_id`, `score`, `evidence`, `method`              |
| `AlignmentGate`       | Turns a result set + policy into a `GateDecision` (+ Markdown comment)   |

The **existing** traceability pipeline is re-expressed against these interfaces without
behaviour change:

* `SRSContextSource`  → wraps `src.parser.extract_requirements`
* `OpenAPIArtifactSource` → wraps `src.parser.extract_endpoints`
* `EmbeddingAlignmentScorer` → wraps `src.embedder.SemanticEmbedder` + cosine
* `ThresholdAlignmentGate` → the Green/Amber/Red policy from `src.similarity`

---

## 3. Roadmap

### Phase A — Refactor to the abstraction *(low risk, high leverage)*
- [x] Define `src/alignment/base.py` protocols + dataclasses
- [x] Provide `SRSContextSource`, `OpenAPIArtifactSource`, `EmbeddingAlignmentScorer`, `ThresholdAlignmentGate`
- [ ] Route the FastAPI `/analyze` pipeline through `AlignmentScorer` (keep NumPy/Qdrant back-ends behind it)
- [ ] Add contract tests: any `AlignmentScorer` must satisfy the same invariants (bounds, symmetry of shape, determinism)

### Phase B — Git-diff artefact source *(unlocks PR-time checks on real repos)*
- [ ] `GitDiffArtifactSource(repo, base_ref, head_ref)` → emits changed files/hunks as `ArtifactUnit`s
- [ ] `DocstringArtifactSource` → extracts module/class/function docstrings from changed Python files
- [ ] GitHub Action wrapper: on `pull_request`, diff → score against linked requirements → post comment / set check status

### Phase C — Jira context source *(the "business mandate" side)*
- [ ] `JiraContextSource(base_url, jql | issue_key)` via Jira REST v3
- [ ] Field extraction: `summary`, `description` (ADF → text), custom **Acceptance Criteria** field, linked epics
- [ ] Story decomposition: split AC bullets into individual `ContextUnit`s so partial coverage is visible
- [ ] Ticket ↔ PR linkage: parse `PROJ-123` from branch name / PR title / commit trailers

### Phase D — Code-aware scoring *(precision upgrade)*
- [ ] `SiameseBertAlignmentScorer` using a code-aware bi-encoder
      (e.g. `microsoft/codebert-base`, `Salesforce/codet5p-110m-embedding`, or
      `jinaai/jina-embeddings-v2-base-code`) — same interface, swap the model
- [ ] AST-level artefacts: `PythonASTArtifactSource` emits per-symbol units with
      signature + body summary + call graph neighbours, not raw text
- [ ] Optional cross-encoder re-ranker for the top-K pairs (`AlignmentScorer` decorator)
- [ ] Calibrate thresholds on a labelled ticket↔commit set; report P/R/F1/MAP exactly
      as the current `src/evaluator.py` does

### Phase E — Autonomous PR gate *(the product)*
- [ ] `AlignmentGate` policy config per repo (`.alignment.yml`): min score, required
      coverage %, severity → check-run conclusion mapping
- [ ] "Explain + suggest" comment: for each unmet AC, the LLM names the missing behaviour
      and points at the file/symbol that should implement it
- [ ] Block merge when `coverage < policy.min_coverage` or any AC scores `BLOCK`
- [ ] Feedback loop: engineers mark false positives → stored as hard negatives for
      threshold re-calibration / future fine-tuning

---

## 4. Design decisions & rationale

| Decision | Rationale |
|---|---|
| One `AlignmentScorer` interface for embeddings **and** Siamese-BERT **and** cross-encoders | The model is the cheap part to change; the plumbing (sources, gate, reporting, CI) is the expensive part. Keep the expensive part model-agnostic. |
| `ContextUnit` / `ArtifactUnit` carry `metadata` + `location` | The gate's Markdown comment needs to link "unmet AC" → "file:line". Losing provenance early makes good comments impossible. |
| Decompose acceptance criteria into separate units | "PR implements 3 of 5 AC" is far more actionable than one blended score. |
| Scoring stays **retrieval-style** (rank all artefacts per context, threshold) | Reuses the existing MAP/Recall evaluation methodology unchanged. |
| LLM is used only for **explanation**, never for the score | Keeps the score reproducible and auditable; the LLM output is grounded in the already-computed pairs. |
| AST over raw text for code | Raw diff text is noisy (imports, formatting). A symbol's signature + docstring + neighbourhood is a cleaner semantic fingerprint — the same reasoning as concatenating OpenAPI fields today. |

---

## 5. Non-goals (for now)

- Real-time Jira webhooks (poll / on-demand only)
- Fine-tuning a bespoke model (use off-the-shelf code encoders first; only fine-tune
  once a labelled corpus exists and off-the-shelf F1 is the bottleneck)
- Languages beyond Python for the AST source (the interface is language-agnostic;
  implementations are added per-language on demand)
- Replacing human review — the gate produces a **signal + explanation**, the team sets
  the policy for whether it's advisory or blocking
