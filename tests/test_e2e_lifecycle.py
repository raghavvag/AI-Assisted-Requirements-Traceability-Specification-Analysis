"""
test_e2e_lifecycle.py — the whole product lifecycle under pytest
================================================================
Runs scripts/e2e_system_test.py offline: an IEEE-830 .docx whose REQ-09 exists
only as a UML activity diagram is uploaded with the store specs, analysed, and
four pull requests are sent through the signed GitHub webhook (PASS, BLOCK on
the diagram requirement, BLOCK on REQ-04, WARN). The diagram is read through
the real vision code path with the model's answer replayed from
tests/fixtures/e2e/vision_recordings.json. Loads all-mpnet-base-v2, so slow.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location("e2e_system_test", ROOT / "scripts" / "e2e_system_test.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module          # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


@pytest.mark.slow
def test_full_lifecycle(tmp_path, monkeypatch, semantic_embedder):
    from app.services import pipeline

    monkeypatch.setattr(pipeline, "_embedder", semantic_embedder)      # reuse the session's model
    e2e = _load_script()
    checks, artefacts = e2e.run_lifecycle(tmp_path, quiet=True)

    failed = [f"{c.stage}: {c.name} ({c.detail})" for c in checks.failed]
    assert not failed, "\n".join(failed)
    assert len(checks.rows) >= 20
    assert {r["id"]: r["strategy"] for r in artefacts["requirements"]}["REQ-09"] == "diagram"
