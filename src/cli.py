"""
src/cli.py — Headless command line interface
============================================
    python -m src.cli extract-srs <file> [--json] [--config FILE]
    python -m src.cli analyze --srs FILE --spec FILE [--ground-truth CSV] [--json] [--config FILE]
    python -m src.cli review  --base-spec FILE|none [--head-spec FILE] [--srs FILE] [--config FILE]
                              [--issue-body FILE] [--format md|json] [--fail-on block|critical|never]
                              [--output FILE] [--no-explain]

Exit codes
    0  success (review: PASS or WARN, or a BLOCK that --fail-on does not fail)
    1  review failed under --fail-on (BLOCK, or Critical drift with --fail-on critical)
    2  tool, input or configuration error

Runs without external services: similarity is computed in NumPy (no Qdrant) and
explanations fall back to a deterministic template when no OPENAI_API_KEY /
GROQ_API_KEY is set. The first run downloads all-mpnet-base-v2 (~420 MB).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

TOOL_NAME = "traceability"
JSON_SCHEMA_VERSION = 1
EVAL_THRESHOLD = 0.40          # IR retrieval threshold, same as backend/app/services/pipeline.py

EXIT_OK, EXIT_FAIL, EXIT_ERROR = 0, 1, 2

logger = logging.getLogger("traceability.cli")


class CliError(Exception):
    """A user-facing error: printed as `error: <message>`, exit code 2."""


# ---------------------------------------------------------------------------
# JSON contract of `review --format json` (validated in tests; CI consumers can rely on it)
# ---------------------------------------------------------------------------

REVIEW_JSON_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["tool", "schema_version", "verdict", "approved", "fails", "fail_on", "exit_code",
                 "summary", "violations", "changes", "thresholds", "inputs", "srs", "markdown"],
    "properties": {
        "tool": {"const": TOOL_NAME},
        "schema_version": {"const": JSON_SCHEMA_VERSION},
        "verdict": {"enum": ["PASS", "WARN", "BLOCK"]},
        "approved": {"type": "boolean"},
        "fails": {"type": "boolean"},
        "fail_on": {"enum": ["block", "critical", "never"]},
        "exit_code": {"enum": [0, 1]},
        "summary": {"type": "string"},
        "violations": {"type": "array", "items": {
            "type": "object",
            "required": ["requirement_id", "endpoint_id", "change_status", "score_v1", "score_v2",
                         "delta", "severity", "explanation"],
            "properties": {
                "requirement_id": {"type": "string"},
                "endpoint_id": {"type": "string"},
                "change_status": {"enum": ["added", "removed", "modified"]},
                "score_v1": {"type": "number"}, "score_v2": {"type": "number"}, "delta": {"type": "number"},
                "severity": {"enum": ["Minor Drift", "Major Drift", "Critical Drift"]},
                "explanation": {"type": "string"},
            },
        }},
        "changes": {"type": "array", "items": {
            "type": "object", "required": ["endpoint_id", "status"],
            "properties": {"status": {"enum": ["added", "removed", "modified"]}},
        }},
        "intent": {"type": ["object", "null"]},
        "thresholds": {"type": "object", "required": ["compliant", "partial", "regression_strong",
                                                      "regression_weak", "link_top_k"]},
        "inputs": {"type": "object", "required": ["srs", "base_spec", "head_spec"]},
        "srs": {"type": "object", "required": ["n_requirements", "format"]},
        "markdown": {"type": "string"},
    },
}


# ---------------------------------------------------------------------------
# Factories (patched in tests so they need no model)
# ---------------------------------------------------------------------------

def make_embedder():
    from src.embedder import SemanticEmbedder
    return SemanticEmbedder()


def make_explainer():
    from src.explainer import ComplianceExplainer
    return ComplianceExplainer()          # never raises; template text when no API key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utf8_streams() -> None:
    # Windows consoles default to cp1252, which cannot print the report's emoji.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _setup_logging(verbose: int) -> None:
    level = logging.WARNING if verbose == 0 else (logging.INFO if verbose == 1 else logging.DEBUG)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr, force=True)
    if verbose == 0:
        # prance's "$ref resolution failed, falling back" and model-loading chatter
        for noisy in ("src.parser", "sentence_transformers", "transformers", "httpx", "src.drift_detector"):
            logging.getLogger(noisy).setLevel(logging.ERROR)


def _existing(path: Optional[str], what: str) -> Path:
    if not path:
        raise CliError(f"{what} is required")
    p = Path(path)
    if not p.is_file():
        raise CliError(f"{what} not found: {p}")
    return p


def _load_config(path: Optional[str], required: bool = False):
    from src.config import find_config, load_config
    if path:
        return load_config(path)
    found = find_config(Path.cwd())
    if found is None and required:
        raise CliError("no --config given and no .traceability.yml found")
    return load_config(found) if found else None


def _load_spec(path: Optional[str], what: str) -> Optional[Dict[str, Any]]:
    """Parsed OpenAPI dict; None for "none" / "-" (spec absent on that side)."""
    from src.parser import load_openapi_spec
    if path is not None and path.strip().lower() in ("none", "-", "/dev/null"):
        return None
    p = _existing(path, what)
    import yaml
    try:
        spec = load_openapi_spec(str(p))
    except (json.JSONDecodeError, ValueError, yaml.YAMLError) as exc:
        raise CliError(f"{what} {p} is not valid JSON/YAML: {exc}") from exc
    if not isinstance(spec, dict):
        raise CliError(f"{what} {p} is not an OpenAPI document")
    return spec


def _extract(srs: Path, options=None):
    from src.srs import extract_requirements_with_report
    return extract_requirements_with_report(srs, options)


def _emit(text: str, output: Optional[str]) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    if output:
        Path(output).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# extract-srs
# ---------------------------------------------------------------------------

def cmd_extract_srs(args: argparse.Namespace) -> int:
    cfg = _load_config(args.config) if args.config else None
    srs = _existing(args.file, "SRS file")
    reqs, report = _extract(srs, cfg.extraction_options() if cfg else None)

    if args.json:
        _emit(json.dumps({"tool": TOOL_NAME, "schema_version": JSON_SCHEMA_VERSION,
                          "requirements": reqs, "report": report.to_dict()}, indent=2, ensure_ascii=False),
              args.output)
        return EXIT_OK

    lines = [report.summary(), ""]
    lines.append(f"{'ID':<14} {'TYPE':<6} {'PRIO':<8} {'FOUND BY':<15} {'LOCATION':<24} TITLE")
    for r in reqs:
        kind = "NFR" if r.get("type") == "non_functional" else "FR"
        lines.append(f"{str(r['id']):<14} {kind:<6} {str(r.get('priority') or '-'):<8} "
                     f"{str(r.get('strategy', '')):<15} {str((r.get('source') or {}).get('locator', '')):<24} "
                     f"{r['title']}")
    if report.skipped:
        lines += ["", "Skipped as noise / boilerplate:"]
        lines += [f"  {reason}: {n}" for reason, n in sorted(report.skipped.items())]
    if report.warnings:
        lines += ["", "Warnings:"] + [f"  - {w}" for w in report.warnings]
    _emit("\n".join(lines), args.output)
    return EXIT_OK


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------

def cmd_analyze(args: argparse.Namespace) -> int:
    from src.config import Thresholds
    from src.evaluator import compute_map, compute_precision_recall_f1, compute_tfidf_similarity_matrix, load_ground_truth
    from src.parser import extract_endpoints_from_spec
    from src.similarity import build_traceability_matrix, compute_similarity_matrix

    cfg = _load_config(args.config) if args.config else None
    t = cfg.thresholds if cfg else Thresholds()
    srs = _existing(args.srs or (str(cfg.srs_path) if cfg else None), "--srs")
    spec_path = args.spec or (str(cfg.openapi_paths[0]) if cfg else None)
    spec = _load_spec(spec_path, "--spec")
    if spec is None:
        raise CliError("--spec is required")
    gt_path = _existing(args.ground_truth, "--ground-truth") if args.ground_truth else None

    reqs, report = _extract(srs, cfg.extraction_options() if cfg else None)
    endpoints = extract_endpoints_from_spec(spec, spec_path)
    emb = make_embedder()
    sim = compute_similarity_matrix(emb.encode_requirements(reqs), emb.encode_endpoints(endpoints))
    matrix = build_traceability_matrix(reqs, endpoints, sim, t.compliant)

    rows = []
    for i, r in enumerate(reqs):
        order = sim[i].argsort()[::-1]
        best = float(sim[i, order[0]])
        band = "compliant" if best >= t.compliant else ("partial" if best >= t.partial else "not_covered")
        rows.append({
            "id": r["id"], "title": r["title"], "best_endpoint": endpoints[order[0]]["id"],
            "best_score": round(best, 4), "band": band,
            "top": [{"endpoint_id": endpoints[j]["id"], "score": round(float(sim[i, j]), 4)} for j in order[:3]],
        })
    counts = {b: sum(1 for x in rows if x["band"] == b) for b in ("compliant", "partial", "not_covered")}
    compliance = counts["compliant"] / max(1, len(rows))

    metrics = None
    if gt_path:
        gt = load_ground_truth(str(gt_path))

        def block(m) -> Dict[str, float]:
            prf = compute_precision_recall_f1(m, gt, reqs, endpoints, threshold=EVAL_THRESHOLD)
            out = {k.lower().replace("-", "_").replace(" ", "_"): round(float(v), 4)
                   for k, v in prf.items() if k.lower() != "n_requirements"}
            out["map"] = round(float(compute_map(m, gt, reqs)), 4)
            return out
        metrics = {"eval_threshold": EVAL_THRESHOLD, "transformer": block(matrix),
                   "tfidf": block(compute_tfidf_similarity_matrix(reqs, endpoints))}

    if args.json:
        _emit(json.dumps({
            "tool": TOOL_NAME, "schema_version": JSON_SCHEMA_VERSION,
            "srs": {"file": str(srs), "n_requirements": len(reqs), "format": report.format},
            "spec": {"file": spec_path, "n_endpoints": len(endpoints)},
            "thresholds": {"compliant": t.compliant, "partial": t.partial},
            "compliance": round(compliance, 4), "bands": counts, "requirements": rows,
            "matrix": {r["id"]: {e["id"]: round(float(sim[i, j]), 4) for j, e in enumerate(endpoints)}
                       for i, r in enumerate(reqs)},
            "metrics": metrics,
        }, indent=2, ensure_ascii=False), args.output)
        return EXIT_OK

    mark = {"compliant": "OK  ", "partial": "PART", "not_covered": "MISS"}
    lines = [f"{len(reqs)} requirements x {len(endpoints)} endpoints  (compliant >= {t.compliant}, "
             f"partial >= {t.partial})", ""]
    for x in rows:
        lines.append(f"[{mark[x['band']]}] {x['id']:<12} {x['best_score']:.3f}  {x['best_endpoint']:<40} {x['title']}")
    lines += ["", f"Compliance: {compliance:.1%}  ({counts['compliant']} compliant, {counts['partial']} partial, "
                  f"{counts['not_covered']} not covered)"]
    if metrics:
        tr, tf = metrics["transformer"], metrics["tfidf"]
        lines += ["", f"Evaluation at tau = {EVAL_THRESHOLD}:",
                  f"  {'':<12} {'P':>7} {'R':>7} {'F1':>7} {'MAP':>7}",
                  f"  {'Transformer':<12} {tr['precision']:>7.4f} {tr['recall']:>7.4f} {tr['f1']:>7.4f} {tr['map']:>7.4f}",
                  f"  {'TF-IDF':<12} {tf['precision']:>7.4f} {tf['recall']:>7.4f} {tf['f1']:>7.4f} {tf['map']:>7.4f}"]
    _emit("\n".join(lines), args.output)
    return EXIT_OK


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

def cmd_review(args: argparse.Namespace) -> int:
    from src.config import FailOn, Thresholds
    from src.review import review_spec_change
    from src.review.intent import parse_acceptance_criteria

    cfg = _load_config(args.config, required=not (args.srs and args.head_spec))
    srs = _existing(args.srs or (str(cfg.srs_path) if cfg else None), "--srs")
    head_path = args.head_spec or (str(cfg.openapi_paths[0]) if cfg else None)
    if not head_path:
        raise CliError("--head-spec is required (or `openapi:` in .traceability.yml)")
    spec_base = _load_spec(args.base_spec, "--base-spec")
    spec_head = _load_spec(head_path, "--head-spec")
    if spec_base is None and spec_head is None:
        raise CliError("--base-spec and --head-spec cannot both be 'none'")

    fail_on = FailOn(args.fail_on) if args.fail_on else (cfg.fail_on if cfg else FailOn.BLOCK)
    thresholds = cfg.thresholds if cfg else Thresholds()
    intent = None
    if args.issue_body:
        body = _existing(args.issue_body, "--issue-body").read_text(encoding="utf-8-sig")
        intent = parse_acceptance_criteria(body) or None

    reqs, report = _extract(srs, cfg.extraction_options() if cfg else None)
    result = review_spec_change(
        reqs, spec_base, spec_head,
        thresholds=thresholds, intent=intent,
        embedder=make_embedder(),
        explainer=None if args.no_explain else make_explainer(),
        source_base=str(args.base_spec), source_head=str(head_path),
    )
    fails = result.fails(fail_on)
    code = EXIT_FAIL if fails else EXIT_OK

    if args.format == "json":
        payload = {
            "tool": TOOL_NAME, "schema_version": JSON_SCHEMA_VERSION,
            **result.to_dict(),
            "fails": fails, "fail_on": fail_on.value, "exit_code": code,
            "inputs": {"srs": str(srs), "base_spec": str(args.base_spec), "head_spec": str(head_path),
                       "issue_body": args.issue_body, "config": cfg.source if cfg else None},
            "srs": {"n_requirements": len(reqs), "format": report.format,
                    "by_strategy": dict(report.by_strategy)},
        }
        _emit(json.dumps(payload, indent=2, ensure_ascii=False), args.output)
    else:
        _emit(result.markdown, args.output)
    print(f"{result.summary} -> exit {code} (fail_on={fail_on.value})", file=sys.stderr)
    return code


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="Semantic requirements traceability: extract SRS requirements, analyse coverage, "
                    "and gate API changes against the requirement baseline.",
        epilog="Exit codes: 0 ok, 1 review failed under --fail-on, 2 error.",
    )
    p.add_argument("-v", "--verbose", action="count", default=0, help="more logging on stderr (-vv for debug)")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    e = sub.add_parser("extract-srs", help="list the requirements found in an SRS document")
    e.add_argument("file", help="SRS document (.docx, .pdf, .md, .txt)")
    e.add_argument("--json", action="store_true", help="machine-readable output")
    e.add_argument("--config", help=".traceability.yml (uses its srs_extraction options)")
    e.add_argument("--output", help="also write the output to this file")
    e.set_defaults(func=cmd_extract_srs)

    a = sub.add_parser("analyze", help="traceability matrix + compliance (+ metrics with ground truth)")
    a.add_argument("--srs", help="SRS document (default: srs from --config)")
    a.add_argument("--spec", help="OpenAPI spec (default: first openapi from --config)")
    a.add_argument("--ground-truth", help="labels CSV (requirement_id,endpoint_id,is_relevant)")
    a.add_argument("--config", help=".traceability.yml")
    a.add_argument("--json", action="store_true", help="machine-readable output incl. the full matrix")
    a.add_argument("--output", help="also write the output to this file")
    a.set_defaults(func=cmd_analyze)

    r = sub.add_parser("review", help="gate an OpenAPI change: PASS / WARN / BLOCK")
    r.add_argument("--base-spec", required=True, help="spec before the change, or 'none' if it is new")
    r.add_argument("--head-spec", help="spec after the change (default: first openapi from the config)")
    r.add_argument("--srs", help="SRS document (default: srs from the config)")
    r.add_argument("--config", help=".traceability.yml (default: nearest one from the current directory)")
    r.add_argument("--issue-body", help="Markdown body of the linked issue (acceptance criteria)")
    r.add_argument("--format", choices=("md", "json"), default="md", help="output format (default md)")
    r.add_argument("--fail-on", choices=("block", "critical", "never"),
                   help="when to exit 1 (default: fail_on from the config, else block)")
    r.add_argument("--output", help="also write the report to this file")
    r.add_argument("--no-explain", action="store_true", help="skip LLM / template explanations")
    r.set_defaults(func=cmd_review)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    _utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)          # usage errors exit 2 (argparse)
    _setup_logging(args.verbose)
    from src.config import ConfigError
    from src.srs import SRSExtractionError
    try:
        return args.func(args)
    except (CliError, ConfigError, SRSExtractionError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        logger.debug("details", exc_info=True)
    except Exception as exc:  # noqa: BLE001 — a CI tool must not crash with a traceback by default
        print(f"error: unexpected {type(exc).__name__}: {exc} (run with -v for details)", file=sys.stderr)
        logger.info("traceback", exc_info=True)
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
