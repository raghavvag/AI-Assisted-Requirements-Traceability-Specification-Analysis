"""
src/srs/normalise.py — Candidates -> requirement dicts
=====================================================
Turns raw strategy output into the requirement contract the rest of the
pipeline consumes, and resolves duplicates.

Output contract (backward compatible with the v1 parser):

    required   id, title, text
    optional   type, priority, section, raw_id, id_source, acceptance_criteria,
               rationale, source{file, format, locator}

`text` is what gets embedded. It is composed exactly like the v1 Markdown
parser ("{title}. {description}") so scores stay comparable across formats.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from src.srs.noise import clean_text
from src.srs.options import ExtractionOptions
from src.srs.report import ExtractionReport
from src.srs.strategies import (
    META_ACCEPTANCE,
    META_ID_SOURCE,
    META_PRIORITY,
    META_RATIONALE,
    META_SOURCE,
    META_TYPE,
    STRATEGY_PRIORITY,
    Candidate,
)

FUNCTIONAL = "functional"
NON_FUNCTIONAL = "non_functional"

_PRIORITY_INLINE = re.compile(
    r"[\[(]?\s*\bpriority\s*[:=\-]\s*(?P<p>high|medium|low|critical|essential|desirable|optional|"
    r"must(?: have)?|should(?: have)?|could(?: have)?|won'?t(?: have)?|p[0-4])\b\s*[\])]?\.?",
    re.IGNORECASE,
)
_RATIONALE_INLINE = re.compile(r"\s*\brationale\s*:\s*(?P<r>.+)$", re.IGNORECASE | re.S)
_NFR_ID = re.compile(r"^(?:[A-Z0-9]+[-_.])?(?:NFR|NF|QR|QAR)(?=[-_. ]?\d)")
_NFR_WORDS = re.compile(
    r"non[- ]?functional|quality attribute|performance|security|usability|reliability|availability|"
    r"scalability|maintainability|portability|safety|privacy|supportability|interoperab|capacity|"
    r"latency|response time|throughput|accessibility|compliance requirement",
    re.IGNORECASE,
)
_FUNCTIONAL_WORDS = re.compile(r"(?<!non[- ])(?<!non)\bfunctional\b|\bfeature|\buse case", re.IGNORECASE)
_WS = re.compile(r"\s+")


def canonical_id(raw: str) -> str:
    """`FR 3` -> `FR-3`, `[req-04]:` -> `REQ-04`, `7` -> `REQ-7`."""
    t, prev = clean_text(raw), None
    while t != prev:                       # "[req-04]:" -> "req-04"
        prev, t = t, t.strip().strip("[](){}").rstrip(":.;,")
    t = re.sub(r"\s+", "-", t).upper()
    if re.fullmatch(r"\d+(?:\.\d+)*", t):
        t = f"REQ-{t}"
    return t


def derive_title(description: str) -> str:
    """Short label for a requirement that has no explicit title."""
    first = re.split(r"(?<=[.!?])\s+", description.strip(), maxsplit=1)[0].strip()
    head, sep, _ = first.partition(":")
    if sep and 1 <= len(head.split()) <= 8:
        return head.strip()
    words = first.rstrip(".").split()
    return " ".join(words) if len(words) <= 12 else " ".join(words[:8])


def classify_type(cid: str, section: Sequence[str], explicit: str = "") -> str:
    e = explicit.lower()
    if e:
        if "non" in e or _NFR_WORDS.search(e) or e in {"nfr", "nf", "quality"}:
            return NON_FUNCTIONAL
        if "func" in e or e in {"fr", "feature"}:
            return FUNCTIONAL
    if _NFR_ID.match(cid):
        return NON_FUNCTIONAL
    for heading in reversed(section):              # closest heading decides
        if re.search(r"non[- ]?functional", heading, re.IGNORECASE):
            return NON_FUNCTIONAL
        if _FUNCTIONAL_WORDS.search(heading):
            return FUNCTIONAL
        if _NFR_WORDS.search(heading):
            return NON_FUNCTIONAL
    return FUNCTIONAL


def _collapse(text: str) -> str:
    return _WS.sub(" ", text).strip()


def build_requirement(
    cand: Candidate,
    *,
    source_file: str,
    source_format: str,
    options: ExtractionOptions,
    report: ExtractionReport,
) -> Optional[Dict[str, object]]:
    cid = canonical_id(cand.raw_id)
    metadata = dict(cand.metadata)
    description = cand.description

    m = _PRIORITY_INLINE.search(description)
    if m:
        metadata.setdefault(META_PRIORITY, m.group("p"))
        description = _PRIORITY_INLINE.sub(" ", description)
    m = _RATIONALE_INLINE.search(description)
    if m:
        metadata.setdefault(META_RATIONALE, _collapse(m.group("r")))
        description = description[: m.start()]

    description = _collapse(clean_text(description, collapse=False))
    title = _collapse(clean_text(cand.title)).rstrip(".").strip()
    explicit_title = bool(title)
    if not title:
        if not description:
            report.skip("requirement_without_text")
            return None
        title = derive_title(description)

    # v1 composition: "{title}. {body}". A derived title is a prefix of the
    # description, so it is not repeated.
    if explicit_title and description:
        text = f"{title}. {description}"
    else:
        text = description or title
    acceptance = _collapse(clean_text(metadata.get(META_ACCEPTANCE, "")))
    if acceptance:
        text = f"{text} Acceptance criteria: {acceptance}"
    text = _collapse(text)

    section = " > ".join(cand.section)
    req_type = classify_type(cid, cand.section, metadata.get(META_TYPE, ""))

    n_words = len(text.split())
    if n_words > options.max_words:
        report.warn(f"{cid}: {n_words} words; the embedding model reads about the first "
                    f"{options.max_words}, the rest is ignored. Consider splitting it.")
    elif n_words < options.min_words:
        report.warn(f"{cid}: only {n_words} word(s); too short to match reliably.")

    priority = metadata.get(META_PRIORITY)
    return {
        "id": cid,
        "title": title,
        "text": text,
        "type": req_type,
        "priority": _collapse(priority) if priority else None,
        "section": section,
        "raw_id": cand.raw_id,
        "id_source": metadata.get(META_ID_SOURCE, "document"),
        "acceptance_criteria": acceptance or None,
        "rationale": _collapse(metadata[META_RATIONALE]) if metadata.get(META_RATIONALE) else None,
        "origin": _collapse(metadata[META_SOURCE]) if metadata.get(META_SOURCE) else None,
        "strategy": cand.strategy,
        "source": {"file": source_file, "format": source_format, "locator": cand.locator},
    }


def finalize(
    candidates: Sequence[Candidate],
    *,
    source_file: str,
    source_format: str,
    options: ExtractionOptions,
    report: ExtractionReport,
) -> List[Dict[str, object]]:
    """Resolve duplicates (strategy priority, then document order), build the
    output dicts, apply `include_nfr`, return them in document order."""
    winners: Dict[str, Candidate] = {}
    for cand in sorted(candidates, key=lambda c: (STRATEGY_PRIORITY.get(c.strategy, 9), c.position)):
        cid = canonical_id(cand.raw_id)
        if not cid:
            continue
        if cid in winners:
            kept = winners[cid]
            report.duplicates.append(cid)
            report.warn(f"Duplicate ID {cid} at {cand.locator} ignored (kept the one at {kept.locator}).")
            continue
        winners[cid] = cand

    ordered: List[Tuple[Tuple[int, int], Candidate]] = sorted(
        ((c.position, c) for c in winners.values()), key=lambda pc: pc[0]
    )
    out: List[Dict[str, object]] = []
    dropped_nfr = 0
    for _, cand in ordered:
        req = build_requirement(cand, source_file=source_file, source_format=source_format,
                                options=options, report=report)
        if req is None:
            continue
        if not options.include_nfr and req["type"] == NON_FUNCTIONAL:
            dropped_nfr += 1
            continue
        out.append(req)
    report.skip("non_functional_excluded", dropped_nfr)
    return out
