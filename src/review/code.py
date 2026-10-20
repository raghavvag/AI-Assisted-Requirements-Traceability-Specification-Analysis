"""
src/review/code.py — Code-aware review (M5, plan §6)
====================================================
Python symbols go through the same rules as OpenAPI endpoints
(`review_endpoint_changes`: tiered regression rule, trace-link scoping,
severity), with a code embedder and code-calibrated thresholds:

    diff_code(base_files, head_files)   symbols paired by "path::qualname";
                                        modified = fingerprint changed
    review_code_change(...)             ReviewResult over the code diff
    merge_reviews(spec, code)           one result: worst verdict, all violations
    review_change(...)                  spec and/or code in one call

A symbol's fingerprint (src/alignment/code_units.py) includes the fields of the
models it accepts or returns, so a PR that only edits `RefundRequest` still
re-scores the handler that takes it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.alignment.code_units import CodeSymbol, enclosing_symbols, symbols_from_sources
from src.review.engine import (
    ADDED, MODIFIED, REMOVED, EndpointChange, PullRequestInfo, ReviewResult, SpecDiff, Verdict,
    review_endpoint_changes,
)

CODE = "code"
_VERDICT_RANK = {Verdict.PASS: 0, Verdict.WARN: 1, Verdict.BLOCK: 2}


def _change(sym_b: Optional[CodeSymbol], sym_h: Optional[CodeSymbol], status: str,
            touched: bool, renamed: bool = False) -> EndpointChange:
    sym = sym_h or sym_b
    what = {MODIFIED: "changed", ADDED: "added", REMOVED: "removed"}[status]
    if renamed:
        what = f"renamed / moved from `{sym_b.qualname}` ({sym_b.location})"
    route = f" ({sym.route})" if sym.route else ""
    note = "" if touched or status != MODIFIED else " via a model it uses"
    return EndpointChange(
        endpoint_id=sym.id, status=status,
        base_id=sym_b.id if sym_b else None, head_id=sym_h.id if sym_h else None,
        base_text=sym_b.text if sym_b else "", head_text=sym_h.text if sym_h else "",
        change_description=f"{sym.kind.capitalize()} `{sym.qualname}`{route} {what}{note} at {sym.location}",
        kind=sym.kind, location=sym.location,
    )


def diff_code(
    base_files: Mapping[str, Optional[str]],
    head_files: Mapping[str, Optional[str]],
    *,
    touched_lines: Optional[Mapping[str, Tuple[Sequence[int], Sequence[int]]]] = None,
) -> Tuple[SpecDiff, List[str]]:
    """Pair the symbols of two versions of the same files ({path: source or None}).

    Paths must already agree on both sides (map a renamed file's base content
    to its head path). `touched_lines` ({path: (base_lines, head_lines)}) only
    labels which changes the diff touched directly; the fingerprint comparison
    decides what changed, so a model edit also flags the handlers that use it.
    """
    base_syms, notes_b = symbols_from_sources(base_files)
    head_syms, notes_h = symbols_from_sources(head_files)
    base = {s.id: s for syms in base_syms.values() for s in syms}
    head = {s.id: s for syms in head_syms.values() for s in syms}

    touched: set = set()
    for path, (b_lines, h_lines) in (touched_lines or {}).items():
        touched |= enclosing_symbols(base_syms.get(path, []), b_lines)
        touched |= enclosing_symbols(head_syms.get(path, []), h_lines)
    direct = (lambda sid: sid in touched) if touched_lines is not None else (lambda sid: True)

    added, removed, modified, unchanged = [], [], [], []
    gone = [base[s] for s in sorted(set(base) - set(head))]
    new = [head[s] for s in sorted(set(head) - set(base))]
    renames = _pair_renames(gone, new)
    for b, h in renames:
        modified.append(_change(b, h, MODIFIED, True, renamed=True))
    renamed_ids = {b.id for b, _ in renames} | {h.id for _, h in renames}

    for sid in sorted(set(base) | set(head)):
        if sid in renamed_ids:
            continue
        b, h = base.get(sid), head.get(sid)
        if b and h:
            if b.text == h.text:
                unchanged.append(sid)
            else:
                modified.append(_change(b, h, MODIFIED, direct(sid)))
        elif b:
            removed.append(_change(b, None, REMOVED, True))
        else:
            added.append(_change(None, h, ADDED, True))
    diff = SpecDiff(tuple(added), tuple(removed), tuple(modified), tuple(unchanged),
                    tuple((s.id, s.text) for s in base.values()), kind=CODE)
    return diff, [*notes_b, *notes_h]


RENAME_SIMILARITY = 0.8


def _body(sym: CodeSymbol) -> str:
    """Fingerprint without the header line (which carries the name and path)."""
    lines = sym.text.splitlines()[1:]
    return "\n".join(line for line in lines if not line.startswith("Signature: "))


def _pair_renames(gone: Sequence[CodeSymbol], new: Sequence[CodeSymbol]) -> List[Tuple[CodeSymbol, CodeSymbol]]:
    """Removed + added symbols of the same kind with near-identical bodies are a rename or a move,
    not a removal: without this, renaming a handler would score its requirement against nothing."""
    from difflib import SequenceMatcher

    pairs, used = [], set()
    for b in gone:
        best, best_ratio = None, RENAME_SIMILARITY
        for h in new:
            if h.id in used or h.kind != b.kind:
                continue
            ratio = SequenceMatcher(None, _body(b), _body(h)).ratio()
            if ratio >= best_ratio:
                best, best_ratio = h, ratio
        if best is not None:
            used.add(best.id)
            pairs.append((b, best))
    return pairs


def review_code_change(
    requirements: Sequence[Dict[str, Any]],
    diff: SpecDiff,
    *,
    thresholds: Any,
    embedder: Any,
    explainer: Any = None,
    intent: Optional[Sequence[Any]] = None,
    pr: Optional[PullRequestInfo] = None,
    notes: Sequence[str] = (),
) -> ReviewResult:
    """`review_endpoint_changes` over Python symbols, reported as a code review."""
    result = review_endpoint_changes(requirements, diff, thresholds=thresholds, embedder=embedder,
                                     explainer=explainer, intent=intent, pr=pr)
    model = getattr(embedder, "model_name", type(embedder).__name__)
    result.notes = [*notes, *result.notes, f"Code symbols scored with `{model}`."]
    empty = SpecDiff((), (), (), ())
    result.code_diff, result.diff = result.diff, empty
    return _rerender(result)


def merge_reviews(spec: Optional[ReviewResult], code: Optional[ReviewResult]) -> ReviewResult:
    """One result for a PR that was reviewed on both its spec and its code."""
    if code is None:
        if spec is None:
            raise ValueError("Nothing to merge.")
        return spec
    if spec is None:
        return code
    merged = replace(
        spec,
        verdict=max(spec.verdict, code.verdict, key=_VERDICT_RANK.__getitem__),
        violations=sorted([*spec.violations, *code.violations], key=lambda v: (v.delta, v.requirement_id)),
        clean_changes=[*spec.clean_changes, *code.clean_changes],
        intent=spec.intent if spec.intent is not None else code.intent,
        notes=list(dict.fromkeys(n for n in [*spec.notes, *code.notes]
                                 if not (n.startswith("No endpoint changed") and spec.diff.is_empty))),
        code_diff=code.code_diff,
        provider_info=spec.provider_info or code.provider_info,
    )
    return _rerender(merged)


def review_change(
    requirements: Sequence[Dict[str, Any]],
    *,
    spec_diff: Optional[SpecDiff],
    code_diff: Optional[SpecDiff] = None,
    code_notes: Sequence[str] = (),
    thresholds: Any,
    code_thresholds: Any = None,
    embedder: Any,
    code_embedder: Any = None,
    explainer: Any = None,
    intent: Optional[Sequence[Any]] = None,
    pr: Optional[PullRequestInfo] = None,
) -> ReviewResult:
    """Review a PR's spec diff and/or code diff.

    The linked-issue intent check runs once: on the spec when the PR adds or
    changes endpoints, otherwise on the code.
    """
    has_code = code_diff is not None and not code_diff.is_empty
    spec_delivers = spec_diff is not None and any(c.status in (ADDED, MODIFIED) for c in spec_diff.changes)
    spec = None
    if spec_diff is not None and (not spec_diff.is_empty or not has_code):
        spec = review_endpoint_changes(requirements, spec_diff, thresholds=thresholds, embedder=embedder,
                                       explainer=explainer, intent=intent if (spec_delivers or not has_code) else None,
                                       pr=pr)
    if spec_diff is None and code_diff is None:
        raise ValueError("review_change needs a spec diff, a code diff, or both.")
    code = None
    if has_code or spec is None:                     # spec is None here only when code_diff is not None
        if code_embedder is None and has_code:
            raise ValueError("A code diff needs a code embedder.")
        code = review_code_change(requirements, code_diff, thresholds=code_thresholds or thresholds,
                                  embedder=code_embedder, explainer=explainer,
                                  intent=None if spec_delivers else intent, pr=pr, notes=code_notes)
    elif code_diff is not None and spec is not None:
        spec.notes.extend(code_notes)
    return merge_reviews(spec, code)


def _rerender(result: ReviewResult) -> ReviewResult:
    from src.review.report import render_markdown
    result.markdown = render_markdown(result)
    return result
