"""
src/alignment/gate.py — AlignmentGate implementation
===================================================
`ThresholdAlignmentGate` turns scored (context, artefact) pairs into an
ALLOW / WARN / BLOCK decision plus a GitHub-style Markdown comment. This is the
policy layer a future GitHub Action calls to set a PR check conclusion.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from src.alignment.base import (
    AlignmentBand,
    AlignmentResult,
    ContextUnit,
    GateDecision,
    GatePolicy,
    GateVerdict,
)

_BAND_ICON = {
    AlignmentBand.COMPLIANT: "🟢",
    AlignmentBand.PARTIAL: "🟠",
    AlignmentBand.NON_COMPLIANT: "🔴",
}


class ThresholdAlignmentGate:
    """Coverage- and band-threshold policy gate."""

    def __init__(self, policy: Optional[GatePolicy] = None):
        self.policy = policy or GatePolicy()

    def evaluate(
        self,
        contexts: List[ContextUnit],
        results: List[AlignmentResult],
        policy: Optional[GatePolicy] = None,
    ) -> GateDecision:
        policy = policy or self.policy

        # Best artefact per context
        best: Dict[str, AlignmentResult] = {}
        for r in results:
            cur = best.get(r.context_id)
            if cur is None or r.score > cur.score:
                best[r.context_id] = r

        ctx_by_id = {c.id: c for c in contexts}
        rows = []
        n_compliant = n_partial = n_noncompliant = 0
        unmet: List[str] = []

        for cid in [c.id for c in contexts]:
            r = best.get(cid)
            # Re-derive the band from the *effective* policy so a caller can pass
            # a stricter/looser policy at evaluate() time than the scorer used.
            band = policy.band(r.score) if r else AlignmentBand.NON_COMPLIANT
            if band is AlignmentBand.COMPLIANT:
                n_compliant += 1
            elif band is AlignmentBand.PARTIAL:
                n_partial += 1
            else:
                n_noncompliant += 1
                unmet.append(cid)
            rows.append((cid, r, band))

        total = max(1, len(contexts))
        covered = n_compliant + n_partial
        coverage = covered / total

        # Verdict
        if coverage < policy.min_coverage:
            verdict = GateVerdict.BLOCK if policy.block_below_coverage else GateVerdict.WARN
        elif n_partial and policy.warn_on_partial:
            verdict = GateVerdict.WARN
        else:
            verdict = GateVerdict.ALLOW

        summary = (
            f"{coverage:.0%} coverage — "
            f"{n_compliant} compliant / {n_partial} partial / {n_noncompliant} unmet "
            f"of {len(contexts)} requirements"
        )
        md = self._render_markdown(verdict, summary, rows, ctx_by_id, policy)

        return GateDecision(
            verdict=verdict,
            coverage=round(coverage, 4),
            summary=summary,
            markdown_comment=md,
            unmet_context_ids=unmet,
            results=results,
        )

    # -- rendering ----------------------------------------------------------

    @staticmethod
    def _render_markdown(verdict, summary, rows, ctx_by_id, policy) -> str:
        head = {
            GateVerdict.ALLOW: "✅ **Alignment check passed**",
            GateVerdict.WARN: "⚠️ **Alignment check passed with warnings**",
            GateVerdict.BLOCK: "🚫 **Alignment check failed — merge blocked**",
        }[verdict]

        lines = [
            "## 🤖 AI Compliance Bot — Semantic Alignment Review",
            f"> {summary}",
            "",
            head,
            "",
            "| Requirement | Best match | Score | Band |",
            "|---|---|---|---|",
        ]
        for cid, r, band in rows:
            ctx = ctx_by_id.get(cid)
            title = (ctx.title or ctx.text[:48]) if ctx else cid
            if r:
                lines.append(
                    f"| `{cid}` — {title} | `{r.artifact_id}` | {r.score:.3f} | {_BAND_ICON[band]} {band.value} |"
                )
            else:
                lines.append(f"| `{cid}` — {title} | _none_ | — | 🔴 Non-Compliant |")

        unmet = [cid for cid, _, band in rows if band is AlignmentBand.NON_COMPLIANT]
        if unmet:
            lines += [
                "",
                "### 🔴 Unmet requirements",
                "",
                *[f"- `{cid}` has no implementation artefact scoring ≥ "
                  f"{policy.partial_at:.2f}. Add or extend an endpoint/symbol that "
                  f"delivers this behaviour, and link it in the PR description." for cid in unmet],
            ]

        if verdict is GateVerdict.BLOCK:
            lines += ["", "> Merge is blocked until coverage reaches "
                      f"{policy.min_coverage:.0%}."]
        return "\n".join(lines)
