"""
test_cli.py — python -m src.cli (extract-srs / analyze / review)
================================================================
Fast tests run `main(argv)` in-process with a fake embedder (no model) and
check exit codes and output contracts. Tests marked `slow` use the real model,
including the store_v1 -> store_v2 integration run and one real subprocess.
"""

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from src import cli
from src.parser import extract_endpoints_from_spec
from src.review.intent import parse_acceptance_criteria
from tests.fakes import FakeEmbedder

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "srs"
STORE_V1 = ROOT / "data" / "api_specs" / "store_v1.json"
STORE_V2 = ROOT / "data" / "api_specs" / "store_v2.json"
ECOMMERCE_SRS = ROOT / "data" / "requirements" / "ecommerce_srs.md"
GROUND_TRUTH = ROOT / "data" / "ground_truth" / "traceability_labels.csv"

SRS_MD = (
    "REQ-01: Refund Integrity\nRefunds must carry a client transaction_id.\n\n"
    "REQ-02: Checkout Security\nHigh-value checkout must require 2FA.\n\n"
    "REQ-03: Catalogue\nProducts must be listable.\n"
)


def _op(summary):
    return {"summary": summary, "responses": {"200": {"description": "ok"}}}


BASE = {"openapi": "3.0.3", "paths": {
    "/api/v1/refund": {"post": _op("refund with client transaction id")},
    "/api/v1/checkout": {"post": _op("checkout with 2fa")},
}}
HEAD_MINOR = {"openapi": "3.0.3", "paths": {             # refund drop of -0.10 (Minor)
    "/api/v2/refund": {"post": _op("refund with server reference")},
    "/api/v2/checkout": {"post": _op("checkout with 2fa")},
}}
HEAD_REMOVED = {"openapi": "3.0.3", "paths": {           # refund removed: Critical
    "/api/v2/checkout": {"post": _op("checkout with 2fa")},
}}
HEAD_CLEAN = {"openapi": "3.0.3", "paths": {             # refund drop of -0.02 (noise)
    "/api/v2/refund": {"post": _op("refund with client transaction id v2")},
    "/api/v2/checkout": {"post": _op("checkout with 2fa")},
}}


def _text(spec, eid):
    return next(e["text"] for e in extract_endpoints_from_spec(spec) if e["id"] == eid)


def _fake_embedder():
    emb = FakeEmbedder(3)
    emb.set(_text(BASE, "POST /api/v1/refund"), r0=0.80)
    emb.set(_text(BASE, "POST /api/v1/checkout"), r1=0.70)
    emb.set(_text(HEAD_MINOR, "POST /api/v2/refund"), r0=0.70)
    emb.set(_text(HEAD_CLEAN, "POST /api/v2/refund"), r0=0.78)
    for head in (HEAD_MINOR, HEAD_REMOVED, HEAD_CLEAN):
        emb.set(_text(head, "POST /api/v2/checkout"), r1=0.70)
    return emb


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Isolated working directory with an SRS, specs and a fake model."""
    monkeypatch.chdir(tmp_path)                     # no repo .traceability.yml discovery
    (tmp_path / "srs.md").write_text(SRS_MD, encoding="utf-8")
    for name, spec in (("base", BASE), ("minor", HEAD_MINOR), ("removed", HEAD_REMOVED), ("clean", HEAD_CLEAN)):
        (tmp_path / f"{name}.json").write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(cli, "make_embedder", _fake_embedder)
    monkeypatch.setattr(cli, "make_explainer", lambda: None)
    return tmp_path


def _review(ws, head, *extra):
    return cli.main(["review", "--srs", str(ws / "srs.md"), "--base-spec", str(ws / "base.json"),
                     "--head-spec", str(ws / f"{head}.json"), "--no-explain", *extra])


def _json_out(capsys):
    return json.loads(capsys.readouterr().out)


# ===========================================================================
# review: exit codes and fail_on
# ===========================================================================

class TestReviewExitCodes:
    def test_regression_blocks_with_exit_1(self, ws, capsys):
        assert _review(ws, "minor") == 1
        out = capsys.readouterr()
        assert "Merge is BLOCKED" in out.out and "exit 1" in out.err

    def test_clean_change_passes_with_exit_0(self, ws, capsys):
        assert _review(ws, "clean") == 0
        assert "Merge is APPROVED" in capsys.readouterr().out

    @pytest.mark.parametrize("head,fail_on,code", [
        ("minor", "block", 1),
        ("minor", "critical", 0),      # Minor Drift only
        ("minor", "never", 0),
        ("removed", "block", 1),
        ("removed", "critical", 1),    # removing a linked endpoint is Critical Drift
        ("removed", "never", 0),
        ("clean", "block", 0),
    ])
    def test_fail_on_matrix(self, ws, capsys, head, fail_on, code):
        assert _review(ws, head, "--fail-on", fail_on) == code

    def test_fail_on_from_config(self, ws, capsys):
        (ws / ".traceability.yml").write_text(
            "srs: srs.md\nopenapi: minor.json\nfail_on: never\n", encoding="utf-8")
        assert cli.main(["review", "--base-spec", str(ws / "base.json"), "--no-explain"]) == 0
        assert cli.main(["review", "--base-spec", str(ws / "base.json"), "--no-explain",
                         "--fail-on", "block"]) == 1           # CLI flag beats the file

    def test_config_thresholds_are_used(self, ws, capsys):
        (ws / ".traceability.yml").write_text(
            "srs: srs.md\nopenapi: minor.json\n"
            "thresholds: {regression_strong: -0.15, regression_weak: -0.2}\n", encoding="utf-8")
        assert cli.main(["review", "--base-spec", "base.json", "--no-explain"]) == 0

    def test_new_spec_base_none(self, ws, capsys):
        assert cli.main(["review", "--srs", "srs.md", "--base-spec", "none", "--head-spec", "minor.json",
                         "--no-explain", "--format", "json"]) == 0
        data = _json_out(capsys)
        assert data["verdict"] == "PASS" and {c["status"] for c in data["changes"]} == {"added"}


class TestReviewErrors:
    @pytest.mark.parametrize("argv", [
        ["review", "--srs", "srs.md", "--base-spec", "missing.json", "--head-spec", "minor.json"],
        ["review", "--srs", "srs.md", "--base-spec", "base.json", "--head-spec", "missing.json"],
        ["review", "--srs", "missing.md", "--base-spec", "base.json", "--head-spec", "minor.json"],
        ["review", "--srs", "srs.md", "--base-spec", "none", "--head-spec", "none"],
        ["review", "--base-spec", "base.json"],                                      # no config, no --srs
        ["review", "--srs", "srs.md", "--base-spec", "base.json", "--head-spec", "minor.json",
         "--config", "nope.yml"],
    ])
    def test_tool_errors_exit_2(self, ws, capsys, argv):
        assert cli.main(argv + ["--no-explain"]) == 2
        assert "error:" in capsys.readouterr().err

    def test_invalid_config_exit_2(self, ws, capsys):
        (ws / ".traceability.yml").write_text("srs: srs.md\nopenapi: minor.json\nfail_on: maybe\n", encoding="utf-8")
        assert cli.main(["review", "--base-spec", "base.json", "--no-explain"]) == 2
        assert "fail_on" in capsys.readouterr().err

    def test_invalid_spec_exit_2(self, ws, capsys):
        (ws / "bad.json").write_text("{not json", encoding="utf-8")
        assert cli.main(["review", "--srs", "srs.md", "--base-spec", "bad.json", "--head-spec", "minor.json"]) == 2

    def test_spec_without_paths_exit_2(self, ws, capsys):
        (ws / "nopaths.json").write_text('{"openapi": "3.0.0"}', encoding="utf-8")
        assert cli.main(["review", "--srs", "srs.md", "--base-spec", "nopaths.json", "--head-spec", "minor.json"]) == 2

    def test_srs_without_requirements_exit_2(self, ws, capsys):
        (ws / "empty.md").write_text("# Notes\n\nNothing formal.\n", encoding="utf-8")
        assert cli.main(["review", "--srs", "empty.md", "--base-spec", "base.json", "--head-spec", "minor.json"]) == 2
        assert "No requirements" in capsys.readouterr().err

    def test_usage_error_exit_2(self, ws):
        with pytest.raises(SystemExit) as exc:
            cli.main(["review", "--format", "xml", "--base-spec", "base.json"])
        assert exc.value.code == 2

    def test_unexpected_exception_exit_2(self, ws, capsys, monkeypatch):
        def boom():
            raise RuntimeError("model exploded")
        monkeypatch.setattr(cli, "make_embedder", boom)
        assert _review(ws, "minor") == 2
        assert "unexpected RuntimeError" in capsys.readouterr().err


# ===========================================================================
# review: JSON contract
# ===========================================================================

class TestReviewJson:
    def test_schema_valid_block(self, ws, capsys):
        assert _review(ws, "minor", "--format", "json") == 1
        data = _json_out(capsys)
        jsonschema.validate(data, cli.REVIEW_JSON_SCHEMA)
        assert data["verdict"] == "BLOCK" and data["fails"] is True and data["exit_code"] == 1
        v = data["violations"][0]
        assert (v["requirement_id"], v["change_status"], v["severity"]) == ("REQ-01", "modified", "Minor Drift")
        assert data["srs"]["n_requirements"] == 3 and data["inputs"]["head_spec"].endswith("minor.json")

    def test_schema_valid_pass(self, ws, capsys):
        assert _review(ws, "clean", "--format", "json") == 0
        data = _json_out(capsys)
        jsonschema.validate(data, cli.REVIEW_JSON_SCHEMA)
        assert data["verdict"] == "PASS" and data["violations"] == [] and data["exit_code"] == 0

    def test_schema_rejects_broken_payload(self):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"tool": "traceability", "verdict": "MAYBE"}, cli.REVIEW_JSON_SCHEMA)

    def test_output_file_matches_stdout(self, ws, capsys):
        _review(ws, "minor", "--format", "json", "--output", "report.json")
        assert json.loads((ws / "report.json").read_text(encoding="utf-8")) == _json_out(capsys)

    def test_stdout_is_pure_json(self, ws, capsys):
        _review(ws, "minor", "--format", "json")
        out = capsys.readouterr()
        json.loads(out.out)                       # summary line goes to stderr, not stdout
        assert "BLOCK" in out.err

    def test_issue_body_runs_intent_check(self, ws, capsys, monkeypatch):
        (ws / "issue.md").write_text("Wishlist\n\n- [ ] Users can save a product to a wishlist\n", encoding="utf-8")
        assert _review(ws, "clean", "--format", "json", "--issue-body", "issue.md") == 0
        data = _json_out(capsys)
        jsonschema.validate(data, cli.REVIEW_JSON_SCHEMA)
        assert data["intent"] is not None and data["intent"]["verdict"] in ("ALLOW", "WARN", "BLOCK")
        assert data["verdict"] in ("PASS", "WARN") and data["inputs"]["issue_body"] == "issue.md"


# ===========================================================================
# extract-srs
# ===========================================================================

class TestExtractSrs:
    def test_json(self, capsys):
        assert cli.main(["extract-srs", str(FIXTURES / "ecommerce_srs_ieee.docx"), "--json"]) == 0
        data = _json_out(capsys)
        assert len(data["requirements"]) == 10 and data["report"]["by_strategy"] == {"table": 6, "tagged": 4}
        assert data["requirements"][0]["source"]["locator"] == "table 3 / row 2"

    def test_human(self, capsys):
        assert cli.main(["extract-srs", str(FIXTURES / "messy_srs.docx")]) == 0
        out = capsys.readouterr().out
        assert "12 requirement(s) from docx" in out and "NFR-2" in out and "Duplicate ID FR-2" in out

    def test_config_options_apply(self, tmp_path, capsys):
        cfg = tmp_path / ".traceability.yml"
        cfg.write_text("srs: s.md\nopenapi: a.json\nsrs_extraction: {include_nfr: false}\n", encoding="utf-8")
        assert cli.main(["extract-srs", str(FIXTURES / "messy_srs.docx"), "--json", "--config", str(cfg)]) == 0
        assert all(not r["id"].startswith("NFR") for r in _json_out(capsys)["requirements"])

    @pytest.mark.parametrize("name", ["scanned.pdf", "does-not-exist.docx"])
    def test_errors(self, capsys, name):
        assert cli.main(["extract-srs", str(FIXTURES / name)]) == 2
        assert "error:" in capsys.readouterr().err


# ===========================================================================
# intent parsing
# ===========================================================================

class TestAcceptanceCriteria:
    def test_checkboxes(self):
        acs = parse_acceptance_criteria("Intro\n- [ ] Users can add\n- [x] Users can **remove**\n* [ ] [Docs](http://x) updated")
        assert [a["text"] for a in acs] == ["Users can add", "Users can remove", "Docs updated"]
        assert [a["id"] for a in acs] == ["ISSUE-AC1", "ISSUE-AC2", "ISSUE-AC3"]

    def test_heading_section(self):
        body = "## Context\nWhy.\n\n## Acceptance Criteria\n1. Refunds keep the id\n2. Receipt echoes it\n\n## Notes\n- not an AC"
        assert [a["text"] for a in parse_acceptance_criteria(body)] == ["Refunds keep the id", "Receipt echoes it"]

    def test_bold_label_and_template_comments(self):
        body = "<!-- template: describe -->\n**Acceptance criteria:**\n- A works\n- B works\n"
        assert [a["text"] for a in parse_acceptance_criteria(body, prefix="GH-12")] == ["A works", "B works"]
        assert parse_acceptance_criteria(body, prefix="GH-12")[0]["id"] == "GH-12-AC1"

    def test_whole_body_fallback_and_empty(self):
        assert parse_acceptance_criteria("Support discount codes in the cart.")[0]["text"] == \
            "Support discount codes in the cart."
        assert parse_acceptance_criteria("  \n<!-- only a comment -->\n") == []


# ===========================================================================
# Real model (slow)
# ===========================================================================

@pytest.mark.slow
class TestIntegration:
    def test_store_v1_to_v2_blocks_on_req04_and_req09(self, capsys, monkeypatch, semantic_embedder):
        monkeypatch.chdir(ROOT)
        monkeypatch.setattr(cli, "make_embedder", lambda: semantic_embedder)
        code = cli.main(["review", "--config", str(ROOT / ".traceability.yml"),
                         "--base-spec", str(STORE_V1), "--head-spec", str(STORE_V2),
                         "--format", "json", "--no-explain"])
        data = _json_out(capsys)
        jsonschema.validate(data, cli.REVIEW_JSON_SCHEMA)
        assert code == 1 and data["verdict"] == "BLOCK"
        regressed = {v["requirement_id"] for v in data["violations"]}
        # the two intentional drifts of the dataset ...
        assert {"REQ-04", "REQ-09"} <= regressed
        # ... and no loosely related pair: REQ-05/06/07 vs the 2FA endpoints are not
        # relevant in the ground truth and are excluded by trace-link scoping (link_top_k=3)
        assert not regressed & {"REQ-05", "REQ-06", "REQ-07"}
        # REQ-02 is a real weakening: v2 GET /products/{id} lost its parameter and response detail
        assert regressed == {"REQ-02", "REQ-04", "REQ-09"}
        assert len(data["changes"]) == 20 and data["unchanged_endpoints"] == 0

    def test_fail_on_critical_lets_minor_drift_through(self, capsys, monkeypatch, semantic_embedder):
        monkeypatch.chdir(ROOT)
        monkeypatch.setattr(cli, "make_embedder", lambda: semantic_embedder)
        assert cli.main(["review", "--base-spec", str(STORE_V1), "--head-spec", str(STORE_V2),
                         "--fail-on", "critical", "--no-explain"]) == 0

    def test_analyze_reproduces_baseline_metrics(self, capsys, monkeypatch, semantic_embedder):
        monkeypatch.setattr(cli, "make_embedder", lambda: semantic_embedder)
        assert cli.main(["analyze", "--srs", str(ECOMMERCE_SRS), "--spec", str(STORE_V1),
                         "--ground-truth", str(GROUND_TRUTH), "--json"]) == 0
        data = _json_out(capsys)
        base = json.loads((ROOT / "tests" / "fixtures" / "baseline_metrics.json").read_text(encoding="utf-8"))
        for key in ("precision", "recall", "f1", "map"):
            assert data["metrics"]["transformer"][key] == pytest.approx(base["transformer"][key], abs=base["tolerance"])
            assert data["metrics"]["tfidf"][key] == pytest.approx(base["tfidf"][key], abs=base["tolerance"])
        assert data["compliance"] == 1.0 and len(data["matrix"]) == 10 and len(data["matrix"]["REQ-01"]) == 20

    def test_real_subprocess_exit_code(self):
        """`python -m src.cli` as CI runs it: separate process, real model, template explanations."""
        proc = subprocess.run(
            [sys.executable, "-m", "src.cli", "review", "--base-spec", str(STORE_V1),
             "--head-spec", str(STORE_V2), "--format", "json"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=900,
            env={**__import__("os").environ, "OPENAI_API_KEY": "", "GROQ_API_KEY": ""},
        )
        assert proc.returncode == 1, proc.stderr[-2000:]
        data = json.loads(proc.stdout)
        assert data["verdict"] == "BLOCK" and all(v["explanation"] for v in data["violations"])
