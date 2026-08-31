"""
explainer.py — LLM Compliance Explainability Engine
=====================================================
Responsibility: For each flagged drift or compliance violation, construct a
                structured LLM prompt and parse the response into a human-readable
                root cause analysis with remediation steps.

Provider strategy:
  1. OpenAI  (OPENAI_API_KEY) — PRIMARY. Default model: gpt-4o-mini
  2. Groq    (GROQ_API_KEY)   — optional fallback, used only when OpenAI is not
                                configured. Default model: llama-3.3-70b-versatile
  3. Neither configured       — deterministic template fallback (never crashes)

Both the OpenAI and Groq Python SDKs expose an identical
`client.chat.completions.create(...)` surface, so a single `_call_llm` handles both.

Anti-hallucination guardrails:
  - Prompt strictly limits the model to information provided in the context.
  - Fallback response is returned if the API call fails (no silent failures).
  - Response is validated to contain all 3 required sections.
"""

import logging
import os
from typing import Dict, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_PLACEHOLDER_PREFIXES = ("sk-your", "sk-xxx", "gsk_your", "your-", "changeme", "change-me", "<", "paste")


def _looks_like_placeholder(value: str) -> bool:
    v = (value or "").strip().lower()
    return (not v) or v.startswith(_PLACEHOLDER_PREFIXES)


# ---------------------------------------------------------------------------
# Provider detection & client initialisation
# ---------------------------------------------------------------------------

def _init_llm_client() -> Tuple[Optional[object], Optional[str], Optional[str]]:
    """
    Initialise an LLM client. OpenAI is tried first (primary); Groq is the
    fallback. Returns (client, model_name, provider) or (None, None, None).
    """
    openai_key   = os.getenv("OPENAI_API_KEY", "")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    groq_key     = os.getenv("GROQ_API_KEY", "")
    groq_model   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # --- 1. OpenAI (primary) ------------------------------------------------
    if not _looks_like_placeholder(openai_key):
        try:
            from openai import OpenAI  # type: ignore
            client = OpenAI(api_key=openai_key)
            logger.info("LLM provider: OpenAI (%s)", openai_model)
            return client, openai_model, "openai"
        except ImportError:
            logger.warning("openai package not installed. Run: pip install openai")

    # --- 2. Groq (fallback) ----------------------------------------------------
    if not _looks_like_placeholder(groq_key):
        try:
            from groq import Groq  # type: ignore
            client = Groq(api_key=groq_key)
            logger.info("LLM provider: Groq fallback (%s)", groq_model)
            return client, groq_model, "groq"
        except ImportError:
            logger.warning("groq package not installed. Run: pip install groq")

    logger.warning(
        "No LLM API key configured (OPENAI_API_KEY primary, GROQ_API_KEY fallback). "
        "Explainability will use the deterministic template fallback."
    )
    return None, None, None


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

DRIFT_EXPLANATION_PROMPT = """\
You are a senior software compliance auditor specialising in API contract governance \
and requirements engineering. Analyze the following requirement–API change pair \
and provide a structured compliance analysis.

Do NOT invent requirements, API fields, or functionality not shown below. \
Base your analysis strictly on the information provided.

---
REQUIREMENT ID: {requirement_id}
REQUIREMENT TEXT:
{requirement_text}

API ENDPOINT (v1.0 — BASELINE COMPLIANT):
{endpoint_v1_text}

API ENDPOINT (v2.0 — MODIFIED VERSION):
{endpoint_v2_text}

OBSERVED SIMILARITY SCORE DROP:
  v1.0 Score : {score_v1:.4f}  (Compliant)
  v2.0 Score : {score_v2:.4f}  (Non-Compliant)
  Delta      : {delta:.4f}
  Severity   : {severity}
---

Provide your analysis in this EXACT structure (use these exact headings):

**1. What Changed:** [One sentence describing the specific schema or contract element that changed between v1.0 and v2.0]

**2. Why It Violates the Requirement:** [One to two sentences linking the specific change to the exact requirement clause it breaks]

**3. Remediation Action:** [A concrete, specific change the developer must make to the API schema to restore full compliance with this requirement]
"""

VIOLATION_EXPLANATION_PROMPT = """\
You are a senior software compliance auditor specialising in API contract governance.
Analyze why the following API endpoint does NOT fully satisfy the stated requirement.

Do NOT invent information. Base your analysis strictly on the provided content.

---
REQUIREMENT ID: {requirement_id}
REQUIREMENT TEXT:
{requirement_text}

API ENDPOINT:
{endpoint_text}

COMPLIANCE SCORE: {score:.4f} (below threshold of {threshold:.2f} — Non-Compliant)
---

Provide your analysis in this EXACT structure:

**1. What Is Missing:** [One sentence identifying the specific requirement clause or expected API contract element that is absent or insufficient]

**2. Why the Score Is Low:** [One sentence explaining the semantic mismatch between the requirement and the API spec]

**3. Remediation Action:** [A concrete, actionable change the developer must make to the API spec to improve compliance]
"""

COMPLIANCE_EXPLANATION_PROMPT = """\
You are a senior software compliance auditor specialising in API contract governance.
Assess how well the following API endpoint satisfies the stated business requirement.

Do NOT invent information. Base your analysis strictly on the provided content.

---
REQUIREMENT ID: {requirement_id}
REQUIREMENT TITLE: {requirement_title}
REQUIREMENT TEXT:
{requirement_text}

BEST-MATCHING API ENDPOINT: {endpoint_id}
ENDPOINT CONTRACT TEXT:
{endpoint_text}

SEMANTIC ALIGNMENT SCORE: {score:.4f}
CLASSIFICATION: {category}
---

Provide your analysis in this EXACT structure:

**1. Coverage Assessment:** [One to two sentences stating which parts of the requirement this endpoint covers and which, if any, are unmet]

**2. Evidence:** [Cite the specific endpoint fields / description phrases that support (or undermine) the alignment]

**3. Recommended Action:** [If Compliant: state it is adequately covered and note any hardening. If Partial/Non-Compliant: the concrete API change needed.]
"""


# ---------------------------------------------------------------------------
# Main explainer class
# ---------------------------------------------------------------------------

class ComplianceExplainer:
    """
    Generates LLM-powered natural language explanations for compliance violations,
    specification drift events, and single-version coverage assessments.

    Usage:
        explainer = ComplianceExplainer()
        explanation = explainer.explain_drift(drift_pair, ep_v1_text, ep_v2_text)
    """

    def __init__(self):
        self.client, self.model, self.provider = _init_llm_client()
        self._available = self.client is not None

    # ── LLM plumbing ─────────────────────────────────────────────────────────

    def _call_llm(self, prompt: str) -> str:
        """Call the configured LLM provider. Returns text, or a fallback string."""
        if not self._available:
            return self._fallback_response("no LLM API key configured")

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=512,
            )
            content = response.choices[0].message.content.strip()
            logger.debug("LLM response (%d chars): %s...", len(content), content[:100])
            return content
        except Exception as e:  # noqa: BLE001 — we deliberately never raise from here
            logger.error("LLM call failed (%s): %s", self.provider, e)
            return self._fallback_response(str(e))

    @staticmethod
    def _fallback_response(reason: str) -> str:
        """Deterministic structured fallback when the LLM is unavailable."""
        return (
            f"**1. What Changed:** AI explanation unavailable — {reason}.\n\n"
            f"**2. Why It Violates the Requirement:** Manual review required — compare the "
            f"v1 and v2 endpoint schemas and check the score delta above.\n\n"
            f"**3. Remediation Action:** Set `OPENAI_API_KEY` (primary) or `GROQ_API_KEY` "
            f"(fallback) in the environment to enable AI-powered explanations."
        )

    @staticmethod
    def _validate_response(response: str) -> bool:
        """Check that the response contains all 3 expected numbered sections."""
        return all(marker in response for marker in ("**1.", "**2.", "**3."))

    def _explain(self, prompt: str, tag: str) -> str:
        response = self._call_llm(prompt)
        if not self._validate_response(response):
            logger.warning("LLM response missing required sections for %s. Using fallback.", tag)
            response = self._fallback_response("the model returned an incomplete response")
        return response

    # ── Public API ───────────────────────────────────────────────────────────

    def explain_drift(
        self,
        drift_pair: Dict,
        endpoint_v1_text: str,
        endpoint_v2_text: str,
    ) -> str:
        """Explain a specification drift event (v1 → v2 regression)."""
        prompt = DRIFT_EXPLANATION_PROMPT.format(
            requirement_id=drift_pair.get("requirement_id", ""),
            requirement_text=drift_pair.get("requirement_text", ""),
            endpoint_v1_text=endpoint_v1_text or "(endpoint not present in v1)",
            endpoint_v2_text=endpoint_v2_text or "(endpoint not present in v2)",
            score_v1=drift_pair.get("score_v1", 0.0),
            score_v2=drift_pair.get("score_v2", 0.0),
            delta=drift_pair.get("delta", 0.0),
            severity=drift_pair.get("severity", "Unknown"),
        )
        return self._explain(prompt, drift_pair.get("requirement_id", "drift"))

    def explain_violation(
        self,
        requirement: Dict,
        endpoint: Dict,
        score: float,
        threshold: float = 0.75,
    ) -> str:
        """Explain a single-version compliance violation (score below threshold)."""
        prompt = VIOLATION_EXPLANATION_PROMPT.format(
            requirement_id=requirement.get("id", ""),
            requirement_text=requirement.get("text", ""),
            endpoint_text=endpoint.get("text", ""),
            score=score,
            threshold=threshold,
        )
        return self._explain(
            prompt, f"{requirement.get('id')}x{endpoint.get('id')}"
        )

    def explain_compliance(
        self,
        requirement: Dict,
        endpoint_id: str,
        score: float,
        category: str,
        endpoint_text: str = "",
    ) -> str:
        """
        Single-version coverage assessment for a requirement and its best-matching
        endpoint. Works for Compliant, Partial and Non-Compliant classifications.

        Used by the FastAPI `GET /api/projects/{id}/explain/{req_id}` route.
        """
        prompt = COMPLIANCE_EXPLANATION_PROMPT.format(
            requirement_id=requirement.get("id", ""),
            requirement_title=requirement.get("title", requirement.get("id", "")),
            requirement_text=requirement.get("text", ""),
            endpoint_id=endpoint_id or "(none)",
            endpoint_text=endpoint_text or "(endpoint contract text not supplied)",
            score=score,
            category=category or "Unknown",
        )
        return self._explain(prompt, f"compliance:{requirement.get('id')}")

    # ── Introspection ────────────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def provider_info(self) -> str:
        if self._available:
            return f"{self.provider} / {self.model}"
        return "no LLM configured (template fallback)"
