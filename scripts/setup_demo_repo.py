"""
scripts/setup_demo_repo.py — Populate traceability-demo-store (T3.10)
======================================================================
Builds the demo repository the live GitHub App reviews (docs/plan.md §4.7):

  main                   docs/SRS.docx (ecommerce_srs_ieee fixture), openapi.yaml (store_v1),
                         .traceability.yml, README.md
  add_endpoint           + POST /api/v1/cart/discount                 -> expected PASS
  remove_idempotency     refund drops the client transaction_id      -> expected BLOCK on REQ-04
  drop_2fa_requirement   checkout drops the 2FA step-up gate          -> expected BLOCK on REQ-09

Each branch is one commit on top of main, ready to push and open as a PR. The
issue and PR texts the PRs link to live in scripts/demo_repo/.

Run:
  python scripts/setup_demo_repo.py [--path ../traceability-demo-store] [--verify] [--force]

  --path    the demo repo checkout (default: next to this project). If it does
            not exist it is cloned from --remote.
  --verify  before committing, review every branch with the real engine
            (python -m src.cli review, no LLM) and stop if a verdict differs
            from the expected one. Loads the embedding model, about a minute.
  --force   recreate scenario branches that already exist locally.
Nothing is pushed.

M5, code-aware review (two phases, each a branch off origin/main):
  python scripts/setup_demo_repo.py --code baseline   [--verify]
      add_service_code: the FastAPI service in scripts/demo_repo/app + `code:` in
      .traceability.yml -> expected PASS. Merge its PR before phase 2.
  python scripts/setup_demo_repo.py --code regression [--verify]
      code_drop_transaction_id: the refund handler and models stop taking the
      client transaction_id (scripts/demo_repo/code_regression.py); openapi.yaml
      is untouched -> expected BLOCK on REQ-04, caught from the code alone.
  python scripts/setup_demo_repo.py --code add-2fa        [--verify]
      add_2fa_module: adds app/twofa.py (REQ-09's challenge / verify / step-up
      policy) to a demo repo created before it existed -> expected PASS. Merge it.
  python scripts/setup_demo_repo.py --code regression-2fa [--verify]
      code_drop_2fa_step_up: deletes the step-up policy, the checkout 2FA check
      and two_fa_token -> expected BLOCK on REQ-09.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRS_FIXTURE = ROOT / "tests" / "fixtures" / "srs" / "ecommerce_srs_ieee.docx"
SPEC_V1 = ROOT / "data" / "api_specs" / "store_v1.json"
TEXTS = ROOT / "scripts" / "demo_repo"
DEFAULT_REMOTE = "https://github.com/raghavvag/traceability-demo-store.git"
TRAILER = "\n\nCo-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"

# Same thresholds as this repository's .traceability.yml; only the paths differ.
CONFIG = """\
# Semantic traceability gate for this repository.
# Read by the Traceability Bot GitHub App on every pull request.
version: 1
srs: docs/SRS.docx
openapi: openapi.yaml
thresholds:
  compliant: 0.55
  partial: 0.40
  drift: -0.10
  regression_strong: -0.05
  regression_weak: -0.08
  strong_relevance: 0.60
  relevance_floor: 0.35
  link_top_k: 3
fail_on: block
srs_extraction:
  id_pattern: null
  include_nfr: true
  extra_blocklist: []
"""

README = """\
# traceability-demo-store

Demo repository for the **Requirements Traceability** GitHub App.

| File | What it is |
|---|---|
| `docs/SRS.docx` | IEEE 830 software requirements specification (REQ-01 … REQ-10) |
| `openapi.yaml` | OpenAPI 3 contract of the Nexus storefront API |
| `.traceability.yml` | Tells the bot where the SRS and the spec are, and the score thresholds |

On every pull request that changes `openapi.yaml` or `docs/SRS.docx`, the bot re-scores the changed
endpoints against the requirements. It posts a **Requirements Traceability** check run and one PR comment.
A requirement that loses alignment makes the check fail; with branch protection, that blocks the merge.
"""


# ---------------------------------------------------------------------------
# Spec edits
# ---------------------------------------------------------------------------

def _fix_mojibake(value):
    """store_v1.json has UTF-8 dashes that were decoded as cp1252 ("â€”"). Repair them."""
    if isinstance(value, str) and "â€" in value:
        try:
            return value.encode("cp1252").decode("utf-8")
        except UnicodeError:
            return value
    if isinstance(value, dict):
        return {k: _fix_mojibake(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_fix_mojibake(v) for v in value]
    return value


def baseline_spec() -> dict:
    return _fix_mojibake(json.loads(SPEC_V1.read_text(encoding="utf-8")))


def _insert_path_after(paths: dict, after: str, new_path: str, item: dict) -> dict:
    out = {}
    for key, value in paths.items():
        out[key] = value
        if key == after:
            out[new_path] = item
    if new_path not in out:
        out[new_path] = item
    return out


def add_endpoint(spec: dict) -> dict:
    """Additive: a new endpoint that applies a discount code to the cart."""
    cart_item = {"type": "object", "properties": {
        "item_id": {"type": "string"}, "product_id": {"type": "string"},
        "quantity": {"type": "integer"}, "unit_price": {"type": "number"},
    }}
    operation = {
        "operationId": "applyDiscountCode",
        "summary": "Apply a promotional discount code to the shopping cart",
        "description": (
            "Applies a promotional discount_code to the authenticated user's persistent shopping cart. "
            "Returns the updated cart state with item_id, product_id, quantity, unit_price, the "
            "discount_amount applied and the recalculated subtotal. Invalid or expired codes are "
            "rejected with a structured validation error."
        ),
        "tags": ["Cart"],
        "security": [{"BearerAuth": []}],
        "requestBody": {"required": True, "content": {"application/json": {"schema": {
            "type": "object",
            "required": ["discount_code"],
            "properties": {"discount_code": {
                "type": "string", "description": "Promotional discount code entered by the shopper"}},
        }}}},
        "responses": {
            "200": {
                "description": "Discount applied. Returns the cart with the discount and recalculated subtotal.",
                "content": {"application/json": {"schema": {"type": "object", "properties": {
                    "cart_id": {"type": "string"},
                    "items": {"type": "array", "items": cart_item},
                    "discount_code": {"type": "string"},
                    "discount_amount": {"type": "number"},
                    "subtotal": {"type": "number", "description": "Server-computed subtotal after the discount"},
                }}}},
            },
            "422": {"description": "Invalid or expired discount code."},
        },
    }
    spec["paths"] = _insert_path_after(spec["paths"], "/api/v1/cart/items/{item_id}",
                                       "/api/v1/cart/discount", {"post": operation})
    return spec


def remove_idempotency(spec: dict) -> dict:
    """Refund: replace the client transaction_id with a server-generated refund_reference."""
    op = spec["paths"]["/api/v1/refund"]["post"]
    op["summary"] = "Process a refund and obtain a server-generated refund confirmation"
    op["description"] = (
        "Initiates a refund for a completed order. Submits order_id, refund_amount and reason. The server "
        "auto-generates a unique refund_reference for internal tracking and returns it in the response. "
        "No caller-supplied idempotency key is accepted or required."
    )
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    schema["required"] = [f for f in schema["required"] if f != "transaction_id"]
    schema["properties"].pop("transaction_id")

    ok = op["responses"]["200"]
    ok["description"] = "Refund processed successfully. Returns a server-generated refund_reference code."
    props = ok["content"]["application/json"]["schema"]["properties"]
    ok["content"]["application/json"]["schema"]["properties"] = {
        ("refund_reference" if k == "transaction_id" else k):
            ({"type": "string", "description": "Auto-generated server-side reference code for this refund"}
             if k == "transaction_id" else v)
        for k, v in props.items()
    }
    return spec


def drop_2fa_requirement(spec: dict) -> dict:
    """Checkout: no 2FA token, no step-up gate for high-value orders."""
    op = spec["paths"]["/api/v1/checkout"]["post"]
    op.pop("x-requires-2fa", None)
    op["description"] = (
        "Accepts a complete checkout payload including cart contents, shipping_address, delivery_method, and "
        "payment_method_token. Validates all fields and places the order, returning a unique order_id and "
        "estimated_delivery_date. Incomplete or invalid payloads are rejected with a structured validation "
        "error. All orders are processed directly without any step-up authentication or secondary "
        "verification challenge, regardless of order value."
    )
    op["requestBody"]["content"]["application/json"]["schema"]["properties"].pop("two_fa_token")
    return spec


SCENARIOS = [
    # branch, edit, expected verdict, requirement that must be blocked, issue text, commit message
    ("add_endpoint", add_endpoint, "PASS", None, "issue_1_discount_codes.md",
     "feat(cart): apply promotional discount codes to the cart"),
    ("remove_idempotency", remove_idempotency, "BLOCK", "REQ-04", "issue_2_simplify_refunds.md",
     "refactor(refund): replace client transaction_id with server refund_reference"),
    ("drop_2fa_requirement", drop_2fa_requirement, "BLOCK", "REQ-09", "issue_3_checkout_friction.md",
     "refactor(checkout): remove the 2FA step-up gate"),
]


def write_spec(spec: dict, path: Path) -> None:
    path.write_text(
        yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=110, default_flow_style=False),
        encoding="utf-8", newline="\n",
    )


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

def git(repo: Path, *args: str, check: bool = True) -> str:
    res = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{res.stderr.strip() or res.stdout.strip()}")
    return res.stdout.strip()


def has_commits(repo: Path) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=repo,
                          capture_output=True).returncode == 0


def branch_exists(repo: Path, name: str) -> bool:
    return bool(git(repo, "branch", "--list", name))


def commit_all(repo: Path, message: str) -> bool:
    git(repo, "add", "-A")
    if not git(repo, "status", "--porcelain"):
        return False
    git(repo, "commit", "-q", "-m", message + TRAILER)
    return True


def ensure_repo(path: Path, remote: str) -> None:
    if not path.exists():
        print(f"• {path} not found; cloning {remote}")
        res = subprocess.run(["git", "clone", remote, str(path)], capture_output=True, text=True)
        if res.returncode != 0:
            raise SystemExit(f"git clone failed:\n{res.stderr.strip()}")
    if not (path / ".git").exists():
        raise SystemExit(f"{path} exists but is not a git repository.")
    if git(path, "status", "--porcelain"):
        raise SystemExit(f"{path} has uncommitted changes; commit or stash them first.")


# ---------------------------------------------------------------------------
# Verification with the real engine
# ---------------------------------------------------------------------------

def verify(base: dict, heads: dict) -> None:
    """Run python -m src.cli review for every scenario; stop on an unexpected verdict."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / ".traceability.yml").write_text(CONFIG, encoding="utf-8")
        (tmp / "docs").mkdir()
        shutil.copy2(SRS_FIXTURE, tmp / "docs" / "SRS.docx")
        write_spec(base, tmp / "base.yaml")
        failures = []
        for branch, _, expected, req, issue, _ in SCENARIOS:
            write_spec(heads[branch], tmp / f"{branch}.yaml")
            res = subprocess.run(
                [sys.executable, "-m", "src.cli", "review",
                 "--base-spec", str(tmp / "base.yaml"), "--head-spec", str(tmp / f"{branch}.yaml"),
                 "--srs", str(tmp / "docs" / "SRS.docx"), "--config", str(tmp / ".traceability.yml"),
                 "--issue-body", str(TEXTS / issue), "--format", "json", "--no-explain"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            try:
                report = json.loads(res.stdout)
            except json.JSONDecodeError:
                raise SystemExit(f"review of {branch} produced no JSON:\n{res.stderr[-2000:]}")
            blocked = sorted({v.get("requirement_id") for v in report.get("violations", [])})
            ok = report["verdict"] == expected and (req is None or req in blocked)
            print(f"  {'ok  ' if ok else 'FAIL'} {branch:22s} {report['verdict']:5s} "
                  f"violations={blocked or '-'}  (expected {expected}{f' on {req}' if req else ''})")
            if not ok:
                failures.append(branch)
        if failures:
            raise SystemExit(f"Unexpected verdicts for: {', '.join(failures)}. Nothing was committed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--path", type=Path, default=ROOT.parent / "traceability-demo-store")
    ap.add_argument("--remote", default=DEFAULT_REMOTE)
    ap.add_argument("--verify", action="store_true", help="check every verdict with the engine first")
    ap.add_argument("--force", action="store_true", help="recreate scenario branches that already exist")
    args = ap.parse_args()
    repo = args.path.resolve()

    base = baseline_spec()
    heads = {branch: edit(copy.deepcopy(base)) for branch, edit, *_ in SCENARIOS}
    if args.verify:
        print("• Verifying the three scenarios with the review engine (no LLM)…")
        verify(base, heads)

    ensure_repo(repo, args.remote)

    # main: the baseline.
    if has_commits(repo):
        git(repo, "checkout", "-q", "main")
    else:
        git(repo, "symbolic-ref", "HEAD", "refs/heads/main")      # empty clone: start main
    (repo / "docs").mkdir(exist_ok=True)
    shutil.copy2(SRS_FIXTURE, repo / "docs" / "SRS.docx")
    write_spec(base, repo / "openapi.yaml")
    (repo / ".traceability.yml").write_text(CONFIG, encoding="utf-8", newline="\n")
    if not (repo / "README.md").exists():
        (repo / "README.md").write_text(README, encoding="utf-8", newline="\n")
    print("• main: baseline committed" if commit_all(repo, "chore: add SRS, OpenAPI spec and traceability config")
          else "• main: baseline already up to date")

    # One branch per scenario, each a single commit on main.
    for branch, _, expected, req, _, message in SCENARIOS:
        if branch_exists(repo, branch):
            if not args.force:
                print(f"• {branch}: already exists, kept (use --force to recreate)")
                continue
            git(repo, "branch", "-D", branch)
        git(repo, "checkout", "-q", "-b", branch, "main")
        write_spec(heads[branch], repo / "openapi.yaml")
        commit_all(repo, message)
        print(f"• {branch}: committed (expected {expected}{f' on {req}' if req else ''})")
    git(repo, "checkout", "-q", "main")

    print(f"\nDone. Repository: {repo}")
    print(git(repo, "log", "--oneline", "--graph", "--all", "-n", "10"))


# ---------------------------------------------------------------------------
# M5: code-aware scenario (two phases, each a branch off origin/main)
# ---------------------------------------------------------------------------

CODE_BLOCK = """\
code:                            # M5: also review changed Python code
  paths: ["app/**/*.py"]
  exclude: ["tests/**"]
  model: jinaai/jina-embeddings-v2-base-code
"""
DEMO_APP = TEXTS / "app"
CODE_PHASES = {
    #  phase:      (branch, commit message, expected verdict, requirement that must be blocked)
    "baseline": ("add_service_code", "feat(app): add the FastAPI service and enable code scanning", "PASS", None),
    "regression": ("code_drop_transaction_id",
                   "refactor(refund): server-generated refund reference instead of the client transaction_id",
                   "BLOCK", "REQ-04"),
    # for a demo repo whose service predates app/twofa.py
    "add-2fa": ("add_2fa_module", "feat(auth): two-factor step-up challenge and verification (REQ-09)", "PASS", None),
    "regression-2fa": ("code_drop_2fa_step_up",
                       "refactor(checkout): drop the 2FA step-up gate for frictionless checkout",
                       "BLOCK", "REQ-09"),
}
REGRESSION_SCENARIO = {"regression": "refund", "regression-2fa": "2fa"}


def _app_files(root: Path) -> dict:
    return {f"app/{p.name}": p.read_text(encoding="utf-8") for p in sorted((root / "app").glob("*.py"))}


def verify_code(phase: str, base_files: dict, head_files: dict) -> None:
    """Review the code change in-process with the configured code model."""
    sys.path.insert(0, str(ROOT))
    from src.alignment.code_embedder import calibrated_thresholds, load_code_embedder
    from src.config import CodeConfig, Thresholds
    from src.review import diff_code, review_change
    from src.srs import extract_requirements_with_report

    _, _, expected, req = CODE_PHASES[phase]
    reqs, _ = extract_requirements_with_report(SRS_FIXTURE)
    cfg = CodeConfig(paths=["app/**/*.py"], exclude=["tests/**"])
    emb = load_code_embedder(cfg.model)
    diff, notes = diff_code(base_files, head_files)
    result = review_change(reqs, spec_diff=None, code_diff=diff, code_notes=notes, thresholds=Thresholds(),
                           code_thresholds=cfg.thresholds_for(Thresholds(), calibrated_thresholds(emb)),
                           embedder=None, code_embedder=emb)
    blocked = result.regressed_requirements
    ok = result.verdict.value == expected and (req is None or blocked == [req])
    print(f"  {'ok  ' if ok else 'FAIL'} {phase:10s} {result.verdict.value:5s} violations={blocked or '-'} "
          f"(expected {expected}{f' on {req}' if req else ''}; scored with {getattr(emb, 'model_name', emb)})")
    if not ok:
        raise SystemExit(f"Unexpected verdict for the code {phase}. Nothing was committed.")


def code_branch(repo: Path, phase: str, *, force: bool, verify: bool) -> None:
    branch, message, expected, req = CODE_PHASES[phase]
    git(repo, "fetch", "-q", "origin")
    if branch_exists(repo, branch):
        if not force:
            print(f"• {branch}: already exists, kept (use --force to recreate)")
            return
        git(repo, "checkout", "-q", "main")
        git(repo, "branch", "-D", branch)
    git(repo, "checkout", "-q", "-b", branch, "origin/main")
    try:
        if phase == "baseline":
            base_files = {}
            (repo / "app").mkdir(exist_ok=True)
            for src in sorted(DEMO_APP.glob("*.py")):
                shutil.copy2(src, repo / "app" / src.name)
            (repo / "app" / "__init__.py").write_text('"""Nexus storefront service (demo for code scanning)."""\n',
                                                      encoding="utf-8", newline="\n")
            config = (repo / ".traceability.yml").read_text(encoding="utf-8")
            if "\ncode:" not in config:
                (repo / ".traceability.yml").write_text(config.rstrip("\n") + "\n" + CODE_BLOCK,
                                                        encoding="utf-8", newline="\n")
            head_files = _app_files(repo)
        elif phase == "add-2fa":
            base_files = _app_files(repo)
            if "app/twofa.py" in base_files:
                raise SystemExit("origin/main already has app/twofa.py: run `--code regression-2fa`.")
            shutil.copy2(DEMO_APP / "twofa.py", repo / "app" / "twofa.py")
            head_files = _app_files(repo)
        else:
            base_files = _app_files(repo)
            needs = "app/twofa.py" if phase == "regression-2fa" else "app/refunds.py"
            if needs not in base_files:
                raise SystemExit(f"origin/main has no {needs} yet: merge the "
                                 f"{'add_2fa_module' if phase == 'regression-2fa' else 'add_service_code'} PR first.")
            sys.path.insert(0, str(TEXTS))
            from code_regression import apply

            head_files = apply(base_files, REGRESSION_SCENARIO[phase])
            for path, src in head_files.items():
                (repo / path).write_text(src, encoding="utf-8", newline="\n")
        if verify:
            print(f"• Verifying the code {phase} with the review engine…")
            def strip(files):
                return {k: v for k, v in files.items() if k != "app/__init__.py"}
            verify_code(phase, strip(base_files), strip(head_files))
        commit_all(repo, message)
    except BaseException:
        git(repo, "checkout", "-q", "-f", "main")
        git(repo, "branch", "-D", branch, check=False)
        raise
    git(repo, "checkout", "-q", "main")
    print(f"• {branch}: committed on origin/main (expected {expected}{f' on {req}' if req else ''})")


def main_code(argv=None) -> None:
    ap = argparse.ArgumentParser(description="M5 code scenario for traceability-demo-store")
    ap.add_argument("phase", choices=sorted(CODE_PHASES))
    ap.add_argument("--path", type=Path, default=ROOT.parent / "traceability-demo-store")
    ap.add_argument("--remote", default=DEFAULT_REMOTE)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    repo = args.path.resolve()
    ensure_repo(repo, args.remote)
    code_branch(repo, args.phase, force=args.force, verify=args.verify)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--code":
        main_code(sys.argv[2:])
    else:
        main()
