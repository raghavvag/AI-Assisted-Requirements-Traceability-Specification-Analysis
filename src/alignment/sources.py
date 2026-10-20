"""
src/alignment/sources.py — Context & Artifact sources
====================================================
SRS and OpenAPI sources wrap the v1.0 parser. The code sources (M5) emit
Python symbols as units: PythonASTArtifactSource for whole files,
GitDiffArtifactSource for what changed between two git refs. Jira is still a
documented stub — see docs/architecture/semantic-alignment-roadmap.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

from src.alignment.base import ArtifactUnit, ContextUnit
from src.alignment.code_units import CodeSymbol, FilePatch, parse_unified_diff, path_matches, symbols_from_sources
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
# Code sources (M5) — fingerprints from src/alignment/code_units.py
# ===========================================================================

def _unit(sym: CodeSymbol, **extra: str) -> ArtifactUnit:
    return ArtifactUnit(id=sym.id, text=sym.text, kind=sym.kind, location=sym.location,
                        metadata={"qualname": sym.qualname, "path": sym.path, "route": sym.route, **extra})


class PythonASTArtifactSource:
    """One ArtifactUnit per top-level function, class and method of the given files.

    `location` is "path:line" (paths are reported relative to `root` when given).
    Pydantic-style models resolve across all the files, so a handler's text lists
    the fields of the model it accepts.
    """

    def __init__(self, file_paths: Sequence[Union[str, Path]], root: Union[str, Path, None] = None):
        self.file_paths = [Path(p) for p in file_paths]
        self.root = Path(root) if root is not None else None
        self.notes: List[str] = []

    def _display(self, path: Path) -> str:
        if self.root is not None:
            try:
                return path.resolve().relative_to(self.root.resolve()).as_posix()
            except ValueError:
                pass
        return path.as_posix()

    def load(self) -> List[ArtifactUnit]:
        files = {self._display(p): p.read_text(encoding="utf-8", errors="replace") for p in self.file_paths}
        symbols, self.notes = symbols_from_sources(files)
        return [_unit(s) for syms in symbols.values() for s in syms]


class GitDiffArtifactSource:
    """Changed Python symbols between two git refs.

    Runs `git diff --unified=0 base...head` (three dots: the PR's own commits,
    from the merge base), maps every hunk to its enclosing function / class
    through the AST of both versions, and compares fingerprints:

      load()       ArtifactUnits of the changed symbols (head version; a removed
                   symbol keeps its base text). metadata: status = added |
                   modified | removed, touched = "yes" if a hunk is inside it,
                   "via-model" if only a model it uses changed.
      code_diff()  the same change as an engine SpecDiff (src.review.code.diff_code)
    """

    def __init__(self, repo_path: Union[str, Path], base_ref: str, head_ref: str = "HEAD",
                 paths: Sequence[str] = ("**/*.py",), exclude: Sequence[str] = ()):
        self.repo_path, self.base_ref, self.head_ref = Path(repo_path), base_ref, head_ref
        self.paths, self.exclude = list(paths), list(exclude)
        self.notes: List[str] = []

    def _git(self, *args: str, check: bool = True) -> str:
        res = subprocess.run(["git", "-C", str(self.repo_path), *args], capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        if check and res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
        return res.stdout

    def _show(self, ref: str, path: str) -> Optional[str]:
        res = subprocess.run(["git", "-C", str(self.repo_path), "show", f"{ref}:{path}"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        return res.stdout if res.returncode == 0 else None

    def patches(self) -> Dict[str, FilePatch]:
        text = self._git("diff", "--unified=0", "--no-color", "--find-renames", f"{self.base_ref}...{self.head_ref}")
        return {path: p for path, p in parse_unified_diff(text).items()
                if path_matches(path, self.paths, self.exclude)
                or (p.old_path and path_matches(p.old_path, self.paths, self.exclude))}

    def _versions(self):
        merge_base = self._git("merge-base", self.base_ref, self.head_ref).strip()
        patches = self.patches()
        base_files: Dict[str, Optional[str]] = {}
        head_files: Dict[str, Optional[str]] = {}
        touched = {}
        for path, p in patches.items():                 # keyed by the head path; a rename keeps one id space
            base_files[path] = self._show(merge_base, p.old_path) if p.old_path else None
            head_files[path] = self._show(self.head_ref, p.new_path) if p.new_path else None
            touched[path] = (sorted(p.base_lines), sorted(p.head_lines))
        return base_files, head_files, touched

    def code_diff(self):
        from src.review.code import diff_code      # local import: src.review depends on this package

        base_files, head_files, touched = self._versions()
        diff, self.notes = diff_code(base_files, head_files, touched_lines=touched)
        return diff

    def load(self) -> List[ArtifactUnit]:
        diff = self.code_diff()
        units = []
        for change in diff.changes:
            via_model = change.status == "modified" and "via a model" in change.change_description
            units.append(ArtifactUnit(
                id=change.endpoint_id, text=change.head_text or change.base_text, kind=change.kind,
                location=change.location,
                metadata={"status": change.status, "touched": "via-model" if via_model else "yes",
                          "change": change.change_description},
            ))
        return units


# ===========================================================================
# STUB — roadmap Phase C. Interface is final; implementation is pending.
# ===========================================================================


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
