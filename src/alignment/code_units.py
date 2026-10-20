"""
src/alignment/code_units.py — Python source as alignment units (M5, plan §6)
===========================================================================
Turns Python files into one unit per top-level function, class and method. A
unit's text is a semantic fingerprint, not raw source, so it embeds well next to
requirement prose and does not change when code only moves:

    Function process_refund (process refund) in app/refunds.py
    Route: POST /api/v1/refund
    Signature: process_refund(req: RefundRequest) -> RefundReceipt
    Docstring: Refund a completed order idempotently.
    Request model RefundRequest fields: order_id: str, transaction_id: str (Unique client id ...)
    Reads: req.order_id, req.transaction_id
    Returns keys: refund_id, transaction_id
    Calls: ledger.find_by_transaction, issue_refund

Pydantic-style models (classes with annotated fields) are resolved across all
files parsed together, so a handler's fingerprint changes when the model it
accepts loses a field: that is how a field-level regression in code is caught.

Also here: unified-diff parsing (`git diff --unified=0` and GitHub's `patch`
field) and mapping changed lines to their enclosing symbol, and `diff_code`,
which pairs symbols across two versions into the engine's SpecDiff.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_ROUTER_FACTORIES = {"APIRouter", "FastAPI", "Blueprint", "Flask"}
_MAX_CALLS = 20
_MAX_DOC = 400


@dataclass(frozen=True)
class CodeSymbol:
    """One function, method or class with its semantic fingerprint."""

    id: str                     # "app/refunds.py::process_refund"
    qualname: str               # "process_refund" | "RefundService.refund"
    kind: str                   # "function" | "class"
    path: str
    lineno: int                 # first line, decorators included
    end_lineno: int
    text: str                   # the fingerprint (what gets embedded)
    route: str = ""             # "POST /api/v1/refund" for a route handler

    @property
    def location(self) -> str:
        return f"{self.path}:{self.lineno}"


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------

def _humanize(name: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ")
    return " ".join(words.lower().split())


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _const_str(node: Optional[ast.AST]) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _names_in(node: Optional[ast.AST]) -> List[str]:
    """Every bare name inside an annotation ("Optional[List[Item]]" -> Optional, List, Item)."""
    if node is None:
        return []
    return [n.id for n in ast.walk(node) if isinstance(n, ast.Name)] + \
           [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _walk_body(nodes: Iterable[ast.AST]):
    """ast.walk over a body without descending into nested functions / classes."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                stack.append(child)


def _field_list(cls: ast.ClassDef) -> List[str]:
    """`name: type` (+ Field description) for each annotated class attribute."""
    out = []
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            item = f"{stmt.target.id}: {ast.unparse(stmt.annotation)}"
            desc = None
            if isinstance(stmt.value, ast.Call):
                for kw in stmt.value.keywords:
                    if kw.arg in ("description", "title"):
                        desc = _const_str(kw.value)
            if desc:
                item += f" ({desc.strip()})"
            out.append(item)
    return out


def collect_models(trees: Mapping[str, ast.Module]) -> Dict[str, List[str]]:
    """Model name -> field list, for every class with annotated fields in any of the trees."""
    models: Dict[str, List[str]] = {}
    for tree in trees.values():
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                fields = _field_list(node)
                if fields:
                    models[node.name] = fields
    return models


def _router_prefixes(tree: ast.Module) -> Dict[str, str]:
    """`router = APIRouter(prefix="/api/v1")` -> {"router": "/api/v1"}."""
    out: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                and _dotted(node.value.func).split(".")[-1] in _ROUTER_FACTORIES:
            prefix = next((_const_str(kw.value) for kw in node.value.keywords if kw.arg in ("prefix", "url_prefix")),
                          None) or ""
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = prefix.rstrip("/")
    return out


def _route_of(fn: ast.AST, prefixes: Dict[str, str]) -> Tuple[str, Optional[str], List[str]]:
    """(route, response_model, other decorator names) from a function's decorators."""
    route, response_model, others = "", None, []
    for dec in getattr(fn, "decorator_list", []):
        call = dec if isinstance(dec, ast.Call) else None
        func = call.func if call else dec
        name = _dotted(func)
        verb = name.split(".")[-1].lower()
        if call and isinstance(func, ast.Attribute) and verb in HTTP_METHODS | {"route", "api_route"}:
            path = _const_str(call.args[0]) if call.args else _const_str(
                next((kw.value for kw in call.keywords if kw.arg == "path"), None))
            if path is not None:
                if verb in HTTP_METHODS:
                    method = verb.upper()
                else:
                    methods = next((kw.value for kw in call.keywords if kw.arg == "methods"), None)
                    method = "/".join(_const_str(e) or "" for e in getattr(methods, "elts", [])).upper() or "GET"
                prefix = prefixes.get(_dotted(func.value), "")
                route = f"{method} {prefix}{path}"
                response_model = next((ast.unparse(kw.value) for kw in call.keywords if kw.arg == "response_model"),
                                      None)
                continue
        others.append(name or ast.unparse(dec))
    return route, response_model, others


def _function_text(fn, qualname: str, path: str, models: Dict[str, List[str]],
                   prefixes: Dict[str, str]) -> Tuple[str, str]:
    route, response_model, decorators = _route_of(fn, prefixes)
    kind_word = "Async function" if isinstance(fn, ast.AsyncFunctionDef) else (
        "Method" if "." in qualname else "Function")
    returns = f" -> {ast.unparse(fn.returns)}" if fn.returns is not None else ""
    lines = [f"{kind_word} {qualname} ({_humanize(fn.name)}) in {path}"]
    if route:
        lines.append(f"Route: {route}")
    lines.append(f"Signature: {fn.name}({ast.unparse(fn.args)}){returns}")
    if decorators:
        lines.append("Decorators: " + ", ".join(decorators))
    doc = ast.get_docstring(fn)
    if doc:
        lines.append("Docstring: " + " ".join(doc.split())[:_MAX_DOC])

    params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs] if a.arg not in ("self", "cls")]
    seen_models = set()
    annotated = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    for arg in annotated:
        for name in _names_in(arg.annotation):
            if name in models and name not in seen_models:
                seen_models.add(name)
                lines.append(f"Request model {name} fields: " + ", ".join(models[name]))
    for name in [*_names_in(fn.returns), *([response_model] if response_model else [])]:
        if name in models and name not in seen_models:
            seen_models.add(name)
            lines.append(f"Response model {name} fields: " + ", ".join(models[name]))

    reads: List[str] = []
    returned: List[str] = []
    calls: List[Tuple[int, int, str]] = []
    for node in _walk_body(fn.body):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in params:
            reads.append(f"{node.value.id}.{node.attr}")
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in params:
            key = _const_str(node.slice)
            if key:
                reads.append(f"{node.value.id}[{key!r}]")
        elif isinstance(node, ast.Return) and node.value is not None:
            value = node.value
            if isinstance(value, ast.Dict):
                returned += [k for k in (_const_str(k) for k in value.keys) if k]
            elif isinstance(value, ast.Call):
                returned += [kw.arg for kw in value.keywords if kw.arg]
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name:
                calls.append((node.lineno, node.col_offset, name))
    if reads:
        lines.append("Reads: " + ", ".join(sorted(set(reads))))
    if returned:
        lines.append("Returns keys: " + ", ".join(sorted(set(returned))))
    if calls:
        ordered = [name for _, _, name in sorted(calls)]           # source order
        lines.append("Calls: " + ", ".join(list(dict.fromkeys(ordered))[:_MAX_CALLS]))
    return "\n".join(lines), route


def _class_text(cls: ast.ClassDef, path: str) -> str:
    lines = [f"Class {cls.name} ({_humanize(cls.name)}) in {path}"]
    if cls.bases:
        lines.append("Bases: " + ", ".join(ast.unparse(b) for b in cls.bases))
    doc = ast.get_docstring(cls)
    if doc:
        lines.append("Docstring: " + " ".join(doc.split())[:_MAX_DOC])
    fields = _field_list(cls)
    if fields:
        lines.append("Fields: " + ", ".join(fields))
    methods = [n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if methods:
        lines.append("Methods: " + ", ".join(methods))
    return "\n".join(lines)


def _first_line(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno, *(d.lineno for d in decorators)])


def parse_python(source: str, path: str) -> Optional[ast.Module]:
    try:
        return ast.parse(source, filename=path)
    except SyntaxError as exc:
        logger.warning("Skipping %s: not valid Python (%s)", path, exc)
        return None


def extract_symbols(tree: ast.Module, path: str, models: Optional[Dict[str, List[str]]] = None) -> List[CodeSymbol]:
    """Top-level functions and classes, and the methods of top-level classes."""
    models = collect_models({path: tree}) if models is None else models
    prefixes = _router_prefixes(tree)
    out: List[CodeSymbol] = []

    def add_function(fn, qualname: str) -> None:
        text, route = _function_text(fn, qualname, path, models, prefixes)
        out.append(CodeSymbol(f"{path}::{qualname}", qualname, "function", path, _first_line(fn),
                              fn.end_lineno or fn.lineno, text, route))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add_function(node, node.name)
        elif isinstance(node, ast.ClassDef):
            out.append(CodeSymbol(f"{path}::{node.name}", node.name, "class", path, _first_line(node),
                                  node.end_lineno or node.lineno, _class_text(node, path)))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add_function(child, f"{node.name}.{child.name}")
    return out


def symbols_from_sources(files: Mapping[str, Optional[str]]) -> Tuple[Dict[str, List[CodeSymbol]], List[str]]:
    """{path: source} -> ({path: symbols}, notes). Models resolve across all given files."""
    trees: Dict[str, ast.Module] = {}
    notes: List[str] = []
    for path, source in files.items():
        if source is None:
            continue
        tree = parse_python(source, path)
        if tree is None:
            notes.append(f"`{path}` is not valid Python; its symbols were skipped.")
        else:
            trees[path] = tree
    models = collect_models(trees)
    return {path: extract_symbols(tree, path, models) for path, tree in trees.items()}, notes


# ---------------------------------------------------------------------------
# Unified diffs
# ---------------------------------------------------------------------------

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FilePatch:
    old_path: Optional[str]          # None = file added
    new_path: Optional[str]          # None = file deleted
    base_lines: Set[int]             # changed (removed) line numbers in the base version
    head_lines: Set[int]             # changed (added) line numbers in the head version

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


def parse_hunks(patch: str) -> Tuple[Set[int], Set[int]]:
    """Changed base / head line numbers of a patch's hunks (works with or without context lines)."""
    base, head = set(), set()
    b = h = 0
    in_hunk = False
    for line in patch.splitlines():
        m = _HUNK.match(line)
        if m:
            b, h, in_hunk = int(m.group(1)), int(m.group(3)), True
            continue
        if not in_hunk or line.startswith("\\"):
            continue
        if line.startswith("-") and not line.startswith("---"):
            base.add(b)
            b += 1
        elif line.startswith("+") and not line.startswith("+++"):
            head.add(h)
            h += 1
        elif line.startswith(" ") or line == "":
            b += 1
            h += 1
        else:                                  # next file header
            in_hunk = False
    return base, head


def parse_unified_diff(text: str) -> Dict[str, FilePatch]:
    """`git diff` output -> {path: FilePatch} (keyed by the head path, or the base path if deleted)."""
    out: Dict[str, FilePatch] = {}
    chunks = re.split(r"(?m)^diff --git ", text)
    for chunk in chunks:
        if not chunk.strip():
            continue
        old = new = None
        for line in chunk.splitlines():
            if line.startswith("--- "):
                old = None if line[4:].strip() == "/dev/null" else _strip_prefix(line[4:].strip())
            elif line.startswith("+++ "):
                new = None if line[4:].strip() == "/dev/null" else _strip_prefix(line[4:].strip())
                break
        if old is None and new is None:          # rename / mode change without content
            m = re.match(r"a/(\S+) b/(\S+)", chunk)
            if not m:
                continue
            old, new = m.group(1), m.group(2)
        base_lines, head_lines = parse_hunks(chunk)
        patch = FilePatch(old, new, base_lines, head_lines)
        out[patch.path] = patch
    return out


def _strip_prefix(p: str) -> str:
    return p[2:] if p[:2] in ("a/", "b/") else p


def enclosing_symbols(symbols: Sequence[CodeSymbol], lines: Iterable[int]) -> Set[str]:
    """Ids of the innermost symbol around each line (a method wins over its class)."""
    hit: Set[str] = set()
    for line in lines:
        around = [s for s in symbols if s.lineno <= line <= s.end_lineno]
        if around:
            hit.add(min(around, key=lambda s: s.end_lineno - s.lineno).id)
    return hit


# ---------------------------------------------------------------------------
# Glob matching for `code.paths`
# ---------------------------------------------------------------------------

def _glob_regex(pattern: str) -> re.Pattern:
    out, i = "", 0
    pattern = pattern.replace("\\", "/")
    if pattern.startswith("./"):
        pattern = pattern[2:]
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += r"(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += r".*"
            i += 2
        elif pattern[i] == "*":
            out += r"[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += r"[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def path_matches(path: str, include: Sequence[str], exclude: Sequence[str] = ()) -> bool:
    """`app/api/refunds.py` against globs like `app/**/*.py` (`**` spans directories)."""
    p = PurePosixPath(str(path).replace("\\", "/")).as_posix()
    if not p.endswith(".py"):
        return False
    if any(_glob_regex(x).match(p) for x in exclude):
        return False
    return any(_glob_regex(x).match(p) for x in include)
