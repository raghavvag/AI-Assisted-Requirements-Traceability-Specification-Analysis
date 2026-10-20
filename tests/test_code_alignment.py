"""
M5 (docs/plan.md §6): code-aware review.

AST fingerprints, unified-diff parsing, the symbol diff, the code embedders,
the AlignmentScorer contract (T5.4), the engine on the demo FastAPI service
(scripts/demo_repo/app) against the IEEE .docx SRS, and GitDiffArtifactSource
on a real temporary git repository.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO_APP = ROOT / "scripts" / "demo_repo" / "app"
sys.path.insert(0, str(ROOT / "scripts" / "demo_repo"))

from code_regression import apply as apply_regression  # noqa: E402

from src.alignment import (  # noqa: E402
    ArtifactUnit, CompositeAlignmentScorer, ContextUnit, EmbeddingAlignmentScorer, GitDiffArtifactSource,
    PythonASTArtifactSource, SiameseBertAlignmentScorer,
)
from src.alignment import code_embedder as ce  # noqa: E402
from src.alignment.code_embedder import LexicalCodeEmbedder, calibrated_thresholds, code_tokens  # noqa: E402
from src.alignment.code_units import (  # noqa: E402
    enclosing_symbols, parse_hunks, parse_unified_diff, path_matches, symbols_from_sources,
)
from src.config import CodeConfig, ConfigError, Thresholds, config_from_dict  # noqa: E402
from src.review import diff_code, diff_endpoints, review_change  # noqa: E402
from src.srs import extract_requirements_with_report  # noqa: E402
from tests.fakes import FakeEmbedder  # noqa: E402


def demo_files():
    return {f"app/{p.name}": p.read_text(encoding="utf-8") for p in sorted(DEMO_APP.glob("*.py"))}


def symbols(files):
    syms, notes = symbols_from_sources(files)
    return {s.id: s for v in syms.values() for s in v}, notes


@pytest.fixture(scope="module")
def requirements():
    reqs, _ = extract_requirements_with_report(ROOT / "tests" / "fixtures" / "srs" / "ecommerce_srs_ieee.docx")
    return reqs


def lexical_thresholds():
    return CodeConfig(paths=["app/**/*.py"]).thresholds_for(Thresholds(), calibrated_thresholds(LexicalCodeEmbedder()))


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------

class TestFingerprints:
    def test_route_handler(self):
        by_id, notes = symbols(demo_files())
        sym = by_id["app/refunds.py::process_refund"]
        decorator_line = demo_files()["app/refunds.py"].splitlines().index(
            '@router.post("/refund", response_model=RefundReceipt)') + 1
        assert notes == [] and sym.kind == "function"
        assert sym.route == "POST /api/v1/refund" and sym.location == f"app/refunds.py:{decorator_line}"
        lines = sym.text.splitlines()
        assert lines[0] == "Function process_refund (process refund) in app/refunds.py"
        assert "Route: POST /api/v1/refund" in lines
        assert "Signature: process_refund(req: RefundRequest) -> RefundReceipt" in lines
        assert any(line.startswith("Docstring: Refund a completed order idempotently.") for line in lines)
        model = next(line for line in lines if line.startswith("Request model RefundRequest fields:"))
        assert "transaction_id: str (Unique client-provided id" in model        # resolved across files
        assert any(line.startswith("Response model RefundReceipt fields:") for line in lines)
        assert "req.transaction_id" in next(line for line in lines if line.startswith("Reads:"))
        assert "transaction_id" in next(line for line in lines if line.startswith("Returns keys:"))
        calls = next(line for line in lines if line.startswith("Calls:"))
        assert calls.index("ledger.find_refund_by_transaction") < calls.index("ledger.record_refund")  # source order

    def test_classes_methods_and_route_variants(self):
        src = '''
from fastapi import FastAPI
from pydantic import BaseModel
api = FastAPI()

class Item(BaseModel):
    """A thing."""
    name: str
    price: float = 0.0

    def total(self, qty: int) -> float:
        return self.price * qty

@api.api_route("/items", methods=["PUT"])
async def put_item(item: Item):
    return {"name": item.name}
'''
        by_id, _ = symbols({"svc.py": src})
        assert set(by_id) == {"svc.py::Item", "svc.py::Item.total", "svc.py::put_item"}
        assert "Fields: name: str, price: float" in by_id["svc.py::Item"].text
        assert "Methods: total" in by_id["svc.py::Item"].text
        assert by_id["svc.py::Item.total"].text.startswith("Method Item.total")
        put = by_id["svc.py::put_item"]
        assert put.route == "PUT /items" and put.text.startswith("Async function put_item")
        assert "Request model Item fields: name: str, price: float" in put.text
        assert "Returns keys: name" in put.text

    def test_moving_code_does_not_change_the_fingerprint(self):
        files = demo_files()
        moved = dict(files, **{"app/refunds.py": "# a comment\n\n\n" + files["app/refunds.py"]})
        a, _ = symbols(files)
        b, _ = symbols(moved)
        assert a["app/refunds.py::process_refund"].text == b["app/refunds.py::process_refund"].text
        assert a["app/refunds.py::process_refund"].lineno + 3 == b["app/refunds.py::process_refund"].lineno

    def test_invalid_python_is_a_note_not_a_crash(self):
        by_id, notes = symbols({"ok.py": "def f():\n    return 1\n", "bad.py": "def (:\n"})
        assert list(by_id) == ["ok.py::f"] and "bad.py" in notes[0]

    def test_ast_source_loads_units(self, tmp_path):
        target = tmp_path / "app"
        shutil.copytree(DEMO_APP, target)
        units = PythonASTArtifactSource(sorted(target.glob("*.py")), root=tmp_path).load()
        by_id = {u.id: u for u in units}
        u = by_id["app/refunds.py::process_refund"]
        assert u.kind == "function" and u.location.startswith("app/refunds.py:")
        assert u.metadata["route"] == "POST /api/v1/refund"
        assert {u.kind for u in units} == {"function", "class"}


# ---------------------------------------------------------------------------
# Diffs and globs
# ---------------------------------------------------------------------------

class TestDiffParsing:
    def test_hunks_with_context_lines(self):
        patch = "@@ -10,4 +10,5 @@ def f():\n     a = 1\n-    b = 2\n+    b = 3\n+    c = 4\n     d = 5\n     e = 6\n"
        assert parse_hunks(patch) == ({11}, {11, 12})

    def test_unified_zero_diff_with_add_delete_and_rename(self):
        diff = (
            "diff --git a/app/x.py b/app/x.py\n--- a/app/x.py\n+++ b/app/x.py\n@@ -3 +3,2 @@\n-old\n+new\n+more\n"
            "diff --git a/app/new.py b/app/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/app/new.py\n@@ -0,0 +1 @@\n+x = 1\n"
            "diff --git a/app/gone.py b/app/gone.py\ndeleted file mode 100644\n--- a/app/gone.py\n+++ /dev/null\n"
            "@@ -1 +0,0 @@\n-y = 2\n"
            "diff --git a/app/a.py b/app/b.py\nsimilarity index 100%\nrename from app/a.py\nrename to app/b.py\n"
        )
        patches = parse_unified_diff(diff)
        assert patches["app/x.py"].base_lines == {3} and patches["app/x.py"].head_lines == {3, 4}
        assert patches["app/new.py"].old_path is None and patches["app/new.py"].head_lines == {1}
        assert patches["app/gone.py"].new_path is None and patches["app/gone.py"].base_lines == {1}
        assert (patches["app/b.py"].old_path, patches["app/b.py"].new_path) == ("app/a.py", "app/b.py")

    def test_enclosing_symbol_prefers_the_method(self):
        src = "class A:\n    x: int\n\n    def m(self):\n        return 1\n"
        syms, _ = symbols_from_sources({"a.py": src})
        assert enclosing_symbols(syms["a.py"], [5]) == {"a.py::A.m"}
        assert enclosing_symbols(syms["a.py"], [2]) == {"a.py::A"}
        assert enclosing_symbols(syms["a.py"], [99]) == set()

    @pytest.mark.parametrize("path,include,exclude,expected", [
        ("app/refunds.py", ["app/**/*.py"], [], True),
        ("app/api/v1/refunds.py", ["app/**/*.py"], [], True),
        ("lib/refunds.py", ["app/**/*.py"], [], False),
        ("tests/test_x.py", ["**/*.py"], ["tests/**"], False),
        ("app/README.md", ["app/**"], [], False),          # only Python files
        ("src/x.py", ["./src/*.py"], [], True),
    ])
    def test_path_matches(self, path, include, exclude, expected):
        assert path_matches(path, include, exclude) is expected


class TestDiffCode:
    def test_field_drop_changes_handler_and_models(self):
        base = demo_files()
        diff, notes = diff_code(base, apply_regression(base))
        assert notes == [] and diff.kind == "code"
        assert {c.endpoint_id for c in diff.modified} == {
            "app/models.py::RefundRequest", "app/models.py::RefundReceipt", "app/refunds.py::process_refund"}
        assert not diff.added and not diff.removed
        handler = next(c for c in diff.modified if c.endpoint_id.endswith("process_refund"))
        assert handler.kind == "function" and handler.location.startswith("app/refunds.py:")
        assert "POST /api/v1/refund" in handler.change_description
        assert len(diff.base_endpoints) == len(symbols(base)[0])           # trace links rank over every symbol

    def test_model_only_edit_reaches_the_handler(self):
        base = demo_files()
        head = dict(base)
        line = '    transaction_id: str = Field(..., description="Unique client-provided id for idempotent processing and audit")\n'
        head["app/models.py"] = base["app/models.py"].replace(line, "")
        removed_at = base["app/models.py"].splitlines().index(line.rstrip("\n")) + 1
        diff, _ = diff_code(base, head, touched_lines={"app/models.py": ([removed_at], [])})
        handler = next(c for c in diff.modified if c.endpoint_id == "app/refunds.py::process_refund")
        assert "via a model it uses" in handler.change_description
        model = next(c for c in diff.modified if c.endpoint_id == "app/models.py::RefundRequest")
        assert "via a model" not in model.change_description

    def test_rename_is_a_modification_not_a_removal(self, requirements):
        base = demo_files()
        head = dict(base, **{"app/refunds.py": base["app/refunds.py"].replace("def process_refund(", "def handle_refund(")})
        diff, _ = diff_code(base, head)
        assert not diff.removed and not diff.added
        (change,) = diff.modified
        assert (change.base_id, change.head_id) == ("app/refunds.py::process_refund", "app/refunds.py::handle_refund")
        assert "renamed / moved from `process_refund`" in change.change_description
        result = review_change(requirements, spec_diff=None, code_diff=diff, thresholds=Thresholds(),
                               code_thresholds=lexical_thresholds(), embedder=None, code_embedder=LexicalCodeEmbedder())
        assert result.verdict.value == "PASS"

    def test_moved_to_another_file_is_paired(self):
        base = {"a.py": "def pay(order_id: str) -> str:\n    \"\"\"Charge the order.\"\"\"\n    return gateway.charge(order_id)\n",
                "b.py": "X = 1\n"}
        head = {"a.py": "Y = 2\n", "b.py": "X = 1\n\n\n" + base["a.py"]}
        diff, _ = diff_code(base, head)
        assert [(c.base_id, c.head_id) for c in diff.modified] == [("a.py::pay", "b.py::pay")]

    def test_two_fa_regression_removes_the_step_up_policy(self):
        base = demo_files()
        diff, _ = diff_code(base, apply_regression(base, "2fa"))
        assert [c.endpoint_id for c in diff.removed] == ["app/twofa.py::requires_step_up"]
        assert {c.endpoint_id for c in diff.modified} >= {"app/checkout.py::place_order",
                                                         "app/models.py::CheckoutRequest"}

    def test_added_and_removed_symbols(self):
        diff, _ = diff_code({"m.py": 'def a():\n    """Ship the order."""\n    return courier.ship()\n'},
                            {"m.py": 'def b(x: int) -> int:\n    """Square a number."""\n    return x * x\n'})
        assert [c.endpoint_id for c in diff.removed] == ["m.py::a"]
        assert [c.endpoint_id for c in diff.added] == ["m.py::b"]
        diff, _ = diff_code({"m.py": None}, {"m.py": "def a():\n    pass\n"})       # new file
        assert [c.status for c in diff.changes] == ["added"]


# ---------------------------------------------------------------------------
# Embedders and thresholds
# ---------------------------------------------------------------------------

class TestCodeEmbedders:
    def test_tokens_split_identifiers(self):
        toks = code_tokens("RefundRequest.transaction_id calls record_refund")
        assert {"refund", "transaction", "transaction_id", "record_refund"} <= set(toks)
        assert "calls" not in toks and "request" not in toks       # fingerprint labels are stop words

    def test_lexical_is_deterministic_and_normalised(self):
        emb = LexicalCodeEmbedder()
        a = emb.encode(["refund transaction_id", "cart subtotal"])
        b = emb.encode(["refund transaction_id", "cart subtotal"])
        assert np.array_equal(a, b)
        assert np.allclose(np.linalg.norm(a, axis=1), 1.0)
        with pytest.raises(ValueError):
            emb.encode([])

    def test_env_forces_lexical(self, monkeypatch):
        monkeypatch.setenv("TRACEABILITY_CODE_MODEL", "lexical")
        assert isinstance(ce.load_code_embedder(), LexicalCodeEmbedder)

    def test_falls_back_when_the_model_cannot_load(self, monkeypatch):
        monkeypatch.delenv("TRACEABILITY_CODE_MODEL", raising=False)
        monkeypatch.setattr(ce, "_CACHE", {})

        def boom(*a, **k):
            raise OSError("offline")
        monkeypatch.setattr(ce, "CodeEmbedder", boom)
        assert isinstance(ce.load_code_embedder("some/model"), LexicalCodeEmbedder)
        monkeypatch.setattr(ce, "_CACHE", {})
        with pytest.raises(OSError):
            ce.load_code_embedder("some/model", allow_fallback=False)

    def test_calibrated_thresholds_and_overrides(self):
        lex = lexical_thresholds()
        assert (lex.regression_strong, lex.regression_weak, lex.link_top_k) == (-0.10, -0.15, 5)
        assert lex.relevance_floor == Thresholds().relevance_floor          # the rest comes from the spec gate
        model = CodeConfig(paths=["**/*.py"]).thresholds_for(Thresholds(), calibrated_thresholds(object()))
        assert (model.regression_strong, model.regression_weak, model.link_top_k) == (-0.025, -0.03, 5)
        own = CodeConfig(paths=["**/*.py"], thresholds={"regression_weak": -0.2})
        assert own.thresholds_for(Thresholds(), ce.LEXICAL_THRESHOLDS).regression_weak == -0.2
        with pytest.raises(ConfigError):
            CodeConfig(paths=["**/*.py"], thresholds={"regression_weak": 0.0}).thresholds_for(Thresholds(), {})

    def test_code_block_in_traceability_yml(self):
        cfg = config_from_dict({"srs": "docs/SRS.docx", "openapi": "openapi.yaml",
                                "code": {"paths": "app/**/*.py", "model": "lexical"}})
        assert cfg.code.paths == ["app/**/*.py"] and cfg.code.model == "lexical"
        assert config_from_dict({"srs": "docs/SRS.docx", "openapi": "openapi.yaml"}).code is None
        with pytest.raises(ConfigError):
            config_from_dict({"srs": "a.md", "openapi": "o.yaml", "code": {"paths": ["x"], "thresholds": {"oops": 1}}})
        with pytest.raises(ConfigError):
            config_from_dict({"srs": "a.md", "openapi": "o.yaml", "code": {"paths": []}})


# ---------------------------------------------------------------------------
# AlignmentScorer contract (T5.4)
# ---------------------------------------------------------------------------

CONTEXTS = [ContextUnit(id="REQ-04", title="Refund integrity", text="Refunds carry a client transaction_id"),
            ContextUnit(id="REQ-03", title="Cart", text="The cart returns items and a subtotal")]
ARTIFACTS = [
    ArtifactUnit(id="POST /refund", text="POST /api/v1/refund refund with transaction_id", kind="endpoint"),
    ArtifactUnit(id="app/refunds.py::process_refund", kind="function",
                 text="Function process_refund reads req.transaction_id"),
    ArtifactUnit(id="app/models.py::Cart", kind="class", text="Class Cart Fields: items, subtotal"),
]


def _scorers():
    lex = LexicalCodeEmbedder()
    return {
        "embedding": EmbeddingAlignmentScorer(embedder=lex),
        "siamese": SiameseBertAlignmentScorer(embedder=lex),
        "composite": CompositeAlignmentScorer(text_scorer=EmbeddingAlignmentScorer(embedder=lex),
                                              code_scorer=SiameseBertAlignmentScorer(embedder=lex)),
    }


class TestScorerContract:
    @pytest.mark.parametrize("name", ["embedding", "siamese", "composite"])
    def test_contract(self, name):
        scorer = _scorers()[name]
        rows = scorer.score(CONTEXTS, ARTIFACTS)
        assert len(rows) == len(CONTEXTS) * len(ARTIFACTS)
        assert {(r.context_id, r.artifact_id) for r in rows} == {(c.id, a.id) for c in CONTEXTS for a in ARTIFACTS}
        assert all(0.0 <= r.score <= 1.0 for r in rows)
        assert rows == scorer.score(CONTEXTS, ARTIFACTS)                       # deterministic
        for ctx in CONTEXTS:
            mine = [r for r in rows if r.context_id == ctx.id]
            assert sorted(r.rank for r in mine) == list(range(1, len(ARTIFACTS) + 1))
            assert max(mine, key=lambda r: r.score).rank == 1
        assert scorer.score([], ARTIFACTS) == [] and scorer.score(CONTEXTS, []) == []

    def test_composite_routes_by_kind(self):
        rows = _scorers()["composite"].score(CONTEXTS, ARTIFACTS)
        method = {r.artifact_id: r.method for r in rows}
        assert method["POST /refund"] == "embedding-cosine"
        assert method["app/refunds.py::process_refund"] == "siamese-bert-code:lexical"
        assert method["app/models.py::Cart"] == "siamese-bert-code:lexical"


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class TestCodeReview:
    def test_field_drop_in_python_blocks_req04(self, requirements):
        base = demo_files()
        diff, notes = diff_code(base, apply_regression(base))
        result = review_change(requirements, spec_diff=None, code_diff=diff, code_notes=notes,
                               thresholds=Thresholds(), code_thresholds=lexical_thresholds(),
                               embedder=None, code_embedder=LexicalCodeEmbedder())
        assert result.verdict.value == "BLOCK" and result.regressed_requirements == ["REQ-04"]
        assert {v.endpoint_id for v in result.violations} >= {"app/refunds.py::process_refund"}
        v = next(v for v in result.violations if v.endpoint_id.endswith("process_refund"))
        assert v.kind == "function" and v.location.startswith("app/refunds.py:") and v.delta < -0.1
        assert v.to_dict()["artifact_kind"] == "function" and v.to_dict()["location"] == v.location
        assert result.diff.is_empty and len(result.code_diff.modified) == 3
        assert "code: 3 modified" in result.summary
        md = result.markdown
        assert "| Code Symbols Changed | 3 (3 modified, 0 removed, 0 added) |" in md
        assert "| **Code symbol** | `process_refund` (function, modified) |" in md
        assert f"| **Location** | `{v.location}` |" in md
        assert "Code symbols scored with `lexical`." in md
        assert result.to_dict()["code_changes"][0]["kind"] in ("function", "class")

    def test_benign_code_change_passes(self, requirements):
        base = demo_files()
        head = dict(base, **{"app/refunds.py": base["app/refunds.py"].replace(
            "    order = ledger.get_order(req.order_id)\n",
            "    logger.info('refund requested for %s', req.order_id)\n    order = ledger.get_order(req.order_id)\n")})
        diff, _ = diff_code(base, head)
        assert [c.endpoint_id for c in diff.modified] == ["app/refunds.py::process_refund"]
        result = review_change(requirements, spec_diff=None, code_diff=diff, thresholds=Thresholds(),
                               code_thresholds=lexical_thresholds(), embedder=None,
                               code_embedder=LexicalCodeEmbedder())
        assert result.verdict.value == "PASS" and result.violations == []
        assert "endpoints and code symbols maintain" in result.markdown

    def test_spec_and_code_are_merged(self, requirements):
        # spec: REQ-04 endpoint regresses (fake embedder); code: a benign change
        eb, eh = {"id": "POST /api/v1/refund", "text": "refund v1"}, {"id": "POST /api/v1/refund", "text": "refund v2"}
        fake = FakeEmbedder(len(requirements))
        fake.set("refund v1", r3=0.82)
        fake.set("refund v2", r3=0.70)
        base = demo_files()
        head = dict(base, **{"app/cart.py": base["app/cart.py"] + "\n\ndef helper() -> None:\n    pass\n"})
        code_diff, _ = diff_code(base, head)
        result = review_change(requirements, spec_diff=diff_endpoints([eb], [eh]), code_diff=code_diff,
                               thresholds=Thresholds(link_top_k=0), code_thresholds=lexical_thresholds(),
                               embedder=fake, code_embedder=LexicalCodeEmbedder())
        assert result.verdict.value == "BLOCK" and result.regressed_requirements == ["REQ-04"]
        assert [v.kind for v in result.violations] == ["endpoint"]
        assert len(result.diff.modified) == 1 and [c.status for c in result.code_diff.changes] == ["added"]
        assert "| Code Symbols Changed | 1 (0 modified, 0 removed, 1 added) |" in result.markdown

    def test_no_code_change_is_a_clean_pass(self, requirements):
        diff, _ = diff_code(demo_files(), demo_files())
        result = review_change(requirements, spec_diff=None, code_diff=diff, thresholds=Thresholds(),
                               embedder=None, code_embedder=None)
        assert result.verdict.value == "PASS"
        assert "No Python symbol changed in the configured code paths." in result.notes
        with pytest.raises(ValueError):
            review_change(requirements, spec_diff=None, code_diff=None, thresholds=Thresholds(), embedder=None)

    @pytest.mark.slow
    def test_jina_code_model_calibration(self, requirements):
        """The real code model with its calibrated thresholds (downloads ~300 MB once): both demo
        regressions block on the right requirement, and benign edits and renames pass."""
        try:
            emb = ce.CodeEmbedder()
        except OSError as exc:                      # no network / no cache: not a code failure
            pytest.skip(f"code model unavailable: {exc}")
        t = CodeConfig(paths=["app/**/*.py"]).thresholds_for(Thresholds(), calibrated_thresholds(emb))
        base = demo_files()

        def verdict(head):
            diff, _ = diff_code(base, head)
            r = review_change(requirements, spec_diff=None, code_diff=diff, thresholds=Thresholds(),
                              code_thresholds=t, embedder=None, code_embedder=emb)
            return r.verdict.value, r.regressed_requirements

        assert verdict(apply_regression(base, "refund")) == ("BLOCK", ["REQ-04"])
        assert verdict(apply_regression(base, "2fa")) == ("BLOCK", ["REQ-09"])
        benign = {
            "app/refunds.py": base["app/refunds.py"].replace("def process_refund(", "def handle_refund(").replace(
                "    order = ledger.get_order(req.order_id)\n",
                "    logger.info('refund %s', req.order_id)\n    order = ledger.get_order(req.order_id)\n"),
            "app/twofa.py": base["app/twofa.py"].replace("Verify the user's OTP", "Check the user's OTP"),
        }
        assert verdict({**base, **benign}) == ("PASS", [])


# ---------------------------------------------------------------------------
# GitDiffArtifactSource on a real repository
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
class TestGitDiffSource:
    @staticmethod
    def _git(repo, *args):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       check=True, capture_output=True)

    def test_changed_symbols_between_refs(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "checkout", "-q", "-b", "main")
        for path, src in demo_files().items():
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(src, encoding="utf-8", newline="\n")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_x.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "base")
        self._git(repo, "checkout", "-q", "-b", "feature")
        for path, src in apply_regression(demo_files()).items():
            (repo / path).write_text(src, encoding="utf-8", newline="\n")
        (repo / "tests" / "test_x.py").write_text("def test_b():\n    pass\n", encoding="utf-8")
        self._git(repo, "commit", "-q", "-am", "drop transaction_id")

        source = GitDiffArtifactSource(repo, "main", "feature", paths=["**/*.py"], exclude=["tests/**"])
        assert set(source.patches()) == {"app/models.py", "app/refunds.py"}
        units = {u.id: u for u in source.load()}
        assert set(units) == {"app/models.py::RefundRequest", "app/models.py::RefundReceipt",
                              "app/refunds.py::process_refund"}
        handler = units["app/refunds.py::process_refund"]
        assert handler.metadata == {"status": "modified", "touched": "yes",
                                    "change": handler.metadata["change"]}
        assert "transaction_id" not in handler.text.split("Reads:")[1]
        assert source.code_diff().kind == "code"
