"""
src/review/intent.py — Acceptance criteria from an issue body
=============================================================
Turns the Markdown body of the issue a PR closes ("Closes #12") into intent
units for the review engine's intent check. Used by the CLI (--issue-body) and
the GitHub App (M3).

find_linked_issues(pr_body)        issues named with a closing keyword
                                   ("Closes #12", "fixes acme/api#7")
parse_acceptance_criteria(body)    acceptance criteria of one issue body

Acceptance-criteria order of preference:
  1. task-list items          - [ ] Users can save a product
  2. list items under an "Acceptance criteria" heading or label
  3. the whole body as one unit (if it has any text)
"""

from __future__ import annotations

import re
from typing import Dict, List, NamedTuple, Optional

_CHECKBOX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[[ xX]\]\s+(?P<t>.+?)\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<t>.+?)\s*$")
_AC_LABEL = re.compile(r"^\s*(?:#{1,6}\s*|\*\*|__)?\s*acceptance\s+criteri(?:a|on)\b", re.IGNORECASE)
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+\S|\*\*[^*]+\*\*\s*:?\s*$)")

# GitHub's closing keywords (close/closes/closed, fix/fixes/fixed, resolve/resolves/resolved),
# optionally followed by ":", then "#N", "owner/repo#N" or a full issue URL.
CLOSING_KEYWORD = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+"
    r"(?:https?://[^/\s]+/(?P<url_repo>[\w.-]+/[\w.-]+)/issues/(?P<url_num>\d+)"
    r"|(?P<repo>[\w.-]+/[\w.-]+)?#(?P<num>\d+))"
)


class IssueRef(NamedTuple):
    number: int
    repo: Optional[str] = None       # "owner/name" for a cross-repository reference, else None

    def label(self) -> str:
        return f"{self.repo}#{self.number}" if self.repo else f"#{self.number}"


def find_linked_issues(pr_body: str, limit: int = 10) -> List[IssueRef]:
    """Issues a PR body links with a closing keyword, in order, without duplicates.
    Code blocks and HTML comments are ignored (PR templates often contain examples)."""
    if not pr_body:
        return []
    text = re.sub(r"<!--.*?-->", " ", pr_body, flags=re.S)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    refs: List[IssueRef] = []
    for m in CLOSING_KEYWORD.finditer(text):
        repo = m.group("url_repo") or m.group("repo")
        ref = IssueRef(int(m.group("url_num") or m.group("num")), repo)
        if ref not in refs:
            refs.append(ref)
        if len(refs) >= limit:
            break
    return refs


def _clean(text: str) -> str:
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)                 # HTML comments (issue templates)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)                   # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)                # links -> label
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1", r"\2", text)
    return re.sub(r"\s+", " ", text).strip()


def _title(text: str) -> str:
    words = text.rstrip(".").split()
    return " ".join(words[:8])


def parse_acceptance_criteria(body: str, prefix: str = "ISSUE") -> List[Dict[str, str]]:
    """Intent units {id, title, text} from an issue body; [] when it has no text."""
    if not body or not body.strip():
        return []
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    lines = body.splitlines()

    items = [_clean(m.group("t")) for m in map(_CHECKBOX.match, lines) if m]

    if not items:
        in_ac = False
        for line in lines:
            if _AC_LABEL.match(line):
                in_ac = True
                rest = _AC_LABEL.sub("", line).strip(" :*_")
                if rest:
                    items.append(_clean(rest))
                continue
            if in_ac:
                m = _LIST_ITEM.match(line)
                if m:
                    items.append(_clean(m.group("t")))
                elif _HEADING.match(line) and items:
                    break

    if not items:
        whole = _clean(body)
        items = [whole] if whole else []

    items = [t for t in items if t]
    return [{"id": f"{prefix}-AC{i}", "title": _title(t), "text": t} for i, t in enumerate(items, start=1)]
