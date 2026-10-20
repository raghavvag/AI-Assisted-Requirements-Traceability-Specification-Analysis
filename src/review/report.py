"""
src/review/report.py — GitHub-style Markdown for a ReviewResult
==============================================================
Same layout the v1 simulated PR bot posted (summary table, one card per
violation with the root-cause analysis, clean changes, merge banner), extended
with WARN, removed/added endpoints and the optional intent check.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from src.review.engine import ReviewResult

_STATUS = {"PASS": "PASS ✅", "WARN": "WARN ⚠️", "BLOCK": "FAIL ❌"}


def render_markdown(result: "ReviewResult", now: Optional[datetime] = None) -> str:
    from src.review.engine import Verdict

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    d, pr = result.diff, result.pr
    status = _STATUS[result.verdict.value]

    lines: List[str] = [
        "## \U0001f916 AI Compliance Bot — Requirements Traceability Review",
        f"> Automated semantic compliance analysis | {stamp}",
        f"> LLM Provider: {result.provider_info or 'n/a'}",
        "",
        "---",
        "",
        "### Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    if pr is not None:
        if pr.number is not None:
            lines.append(f"| PR Number | #{pr.number} |")
        if pr.title:
            lines.append(f"| PR Title | {pr.title} |")
        if pr.author:
            lines.append(f"| Author | {pr.author} |")
    lines += [
        f"| Endpoints Changed | {len(d.changes)} ({len(d.modified)} modified, {len(d.removed)} removed, "
        f"{len(d.added)} added) |",
    ]
    c = result.code_diff
    if c is not None:
        lines.append(f"| Code Symbols Changed | {len(c.changes)} ({len(c.modified)} modified, "
                     f"{len(c.removed)} removed, {len(c.added)} added) |")
    lines += [
        f"| Compliance Violations | {len(result.violations)} |",
        f"| Clean Changes (No Violations) | {len(result.clean_changes)} |",
    ]
    if result.intent is not None:
        lines.append(f"| Linked-issue Coverage | {result.intent.coverage:.0%} |")
    lines += [f"| Overall Status | **{status}** |", "", "---", ""]

    if result.violations:
        lines += ["### ⚠️ Violation Details", ""]
        for i, v in enumerate(result.violations, start=1):
            icon = "\U0001f534" if ("Critical" in v.severity or "Major" in v.severity) else "\U0001f7e0"
            lines += [
                f"#### VIOLATION #{i} — {icon} {v.severity}",
                "",
                "| Field | Value |",
                "|---|---|",
                f"| **Requirement** | `{v.requirement_id}` |",
            ]
            if v.is_code:
                lines += [
                    f"| **Code symbol** | `{v.endpoint_id.split('::')[-1]}` ({v.kind}, {v.change_status}) |",
                    f"| **Location** | `{v.location}` |",
                    f"| **Base Score** | `{v.score_v1:.4f}` |",
                    f"| **Head Score** | `{v.score_v2:.4f}` |",
                ]
            else:
                lines += [
                    f"| **Endpoint** | `{v.endpoint_id}` ({v.change_status}) |",
                    f"| **v1.0 Score** | `{v.score_v1:.4f}` |",
                    f"| **v2.0 Score** | `{v.score_v2:.4f}` |",
                ]
            lines += [
                f"| **Score Delta** | `{v.delta:.4f}` |",
                f"| **Change** | {v.change_description or 'N/A'} |",
                "",
                "**\U0001f9e0 AI Root Cause Analysis:**",
                "",
                v.explanation or "_No explanation requested._",
                "",
                "---",
                "",
            ]
    else:
        lines += [
            "### ✅ No Compliance Violations Detected",
            "",
            ("All changed endpoints and code symbols maintain or improve alignment with the requirement baseline."
             if c is not None and not c.is_empty else
             "All changed endpoints maintain or improve alignment with the requirement baseline.")
            if not (d.is_empty and (c is None or c.is_empty))
            else "No endpoint changed between the two specifications.",
            "",
            "---",
            "",
        ]

    if result.clean_changes:
        lines += ["### ✅ Clean Changes (Non-Regressing)", ""]
        lines += [f"- `{c.endpoint_id}` ({c.status}): {c.change_description or 'changed'}"
                  for c in result.clean_changes]
        lines += ["", "---", ""]

    if result.intent is not None:
        lines += ["### \U0001f3af Linked-issue intent", "", f"> {result.intent.summary}", ""]
        if result.intent.unmet_context_ids:
            lines += [f"- `{cid}` is not delivered by any added or modified endpoint"
                      for cid in result.intent.unmet_context_ids]
            lines.append("")
        lines += ["---", ""]

    for note in result.notes:
        lines.append(f"> ℹ️ {note}")
    if result.notes:
        lines.append("")

    if result.verdict is Verdict.BLOCK:
        lines += [
            "> \U0001f6ab **Merge is BLOCKED** until all compliance violations are resolved.",
            "> Address the remediation actions above and push a new commit.",
        ]
    elif result.verdict is Verdict.WARN:
        lines += [
            "> ⚠️ **Merge is allowed with warnings**: no requirement regressed, but the change only",
            "> partly delivers the linked issue. Check the intent section above.",
        ]
    else:
        lines += [
            "> ✅ **Merge is APPROVED** from a requirements-compliance perspective.",
            "> All API changes are consistent with the requirement baseline.",
        ]
    return "\n".join(lines)
