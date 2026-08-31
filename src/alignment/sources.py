"""
src/alignment/sources.py — Context & Artifact sources
====================================================
Working sources wrap the existing v1.0 parser. Future sources (Jira, git diff,
Python AST) are declared as documented stubs so the interface and roadmap are
concrete — see docs/architecture/semantic-alignment-roadmap.md.
"""

from __future__ import annotations

from typing import List

from src.alignment.base import ArtifactUnit, ContextUnit
from src.parser import extract_endpoints, extract_requirements


# ===========================================================================
# WORKING — v1.0 pipeline re-expressed against the abstraction
# ===========================================================================

class SRSContextSource:
    """ContextSource backed by a structured SRS Markdown file (REQ-N: blocks)."""

    def __init__(self, srs_path: str):
        self.srs_path = srs_path

    def load(self) -> List[ContextUnit]:
        return [
            ContextUnit(
                id=r["id"],
                title=r.get("title", ""),
                text=r["text"],
                kind="requirement",
                metadata={"source": self.srs_path},
            )
            for r in extract_requirements(self.srs_path)
        ]


class OpenAPIArtifactSource:
    """ArtifactSource backed by an OpenAPI 3.0 / Swagger 2.0 spec file."""

    def __init__(self, spec_path: str):
        self.spec_path = spec_path

    def load(self) -> List[ArtifactUnit]:
        return [
            ArtifactUnit(
                id=e["id"],
                text=e["text"],
                kind="endpoint",
                location=f"{self.spec_path}#{e['method']} {e['path']}",
                metadata={
                    "method": e.get("method", ""),
                    "path": e.get("path", ""),
                    "operation_id": e.get("operation_id", ""),
                },
            )
            for e in extract_endpoints(self.spec_path)
        ]


# ===========================================================================
# STUBS — roadmap Phases B–D. Interface is final; implementation is pending.
# ===========================================================================

class GitDiffArtifactSource:
    """
    ArtifactSource that emits changed files / hunks between two git refs.

    Roadmap Phase B. Planned implementation:
      - `git diff --unified=0 {base_ref}...{head_ref}` (or GitPython)
      - one ArtifactUnit per changed hunk: text = added lines + surrounding
        function signature; location = "path:start_line-end_line"
      - filter to source files (respect .gitattributes / language allow-list)
    """

    def __init__(self, repo_path: str, base_ref: str, head_ref: str):
        self.repo_path, self.base_ref, self.head_ref = repo_path, base_ref, head_ref

    def load(self) -> List[ArtifactUnit]:  # pragma: no cover - stub
        raise NotImplementedError(
            "GitDiffArtifactSource is roadmap Phase B — see "
            "docs/architecture/semantic-alignment-roadmap.md"
        )


class PythonASTArtifactSource:
    """
    ArtifactSource that emits per-symbol units from changed Python files.

    Roadmap Phase D. Planned implementation:
      - walk the AST of each changed file (`ast` module)
      - one ArtifactUnit per FunctionDef / AsyncFunctionDef / ClassDef
      - text = qualified name + signature + docstring + one-line body summary +
        names of called functions (1-hop call graph) — a semantic fingerprint,
        not raw source
      - pair with a code-aware scorer (see SiameseBertAlignmentScorer)
    """

    def __init__(self, file_paths: List[str]):
        self.file_paths = file_paths

    def load(self) -> List[ArtifactUnit]:  # pragma: no cover - stub
        raise NotImplementedError(
            "PythonASTArtifactSource is roadmap Phase D — see "
            "docs/architecture/semantic-alignment-roadmap.md"
        )


class JiraContextSource:
    """
    ContextSource backed by the Jira REST API v3.

    Roadmap Phase C. Planned implementation:
      - fetch issues by JQL or issue key(s)
      - one ContextUnit per issue for `summary` + `description`
        (Atlassian Document Format -> plain text)
      - additionally decompose the **Acceptance Criteria** custom field into one
        ContextUnit per bullet (kind="acceptance_criterion") so partial coverage
        is visible
      - metadata carries {issue_key, issue_type, epic, status, url}
    """

    def __init__(self, base_url: str, *, jql: str = "", issue_keys: List[str] | None = None,
                 email: str = "", api_token: str = ""):
        self.base_url = base_url
        self.jql = jql
        self.issue_keys = issue_keys or []
        self._auth = (email, api_token)

    def load(self) -> List[ContextUnit]:  # pragma: no cover - stub
        raise NotImplementedError(
            "JiraContextSource is roadmap Phase C — see "
            "docs/architecture/semantic-alignment-roadmap.md"
        )
