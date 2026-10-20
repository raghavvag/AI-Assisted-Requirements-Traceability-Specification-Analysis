"""
Shared pytest setup.

* Puts the repo root (and backend/) on sys.path so `import src...` / `import app...` work.
* Points the backend at a temporary SQLite DB and upload dir (see TEST_TMP).
* `semantic_embedder`: one SentenceTransformer per test session (loading
  all-mpnet-base-v2 takes seconds; tests marked `slow` should use this).
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.append(str(BACKEND))                    # `import app...` for the API tests

# The backend reads its settings once, at first import. Point the DB and the
# upload directory at a throw-away folder BEFORE any test module imports the
# app, so no test can ever touch backend/traceability.db or backend/uploads.
TEST_TMP = Path(tempfile.mkdtemp(prefix="trace_tests_"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(TEST_TMP / 'test.db').as_posix()}"
os.environ["UPLOAD_DIR"] = str(TEST_TMP / "uploads")


@pytest.fixture(scope="session")
def semantic_embedder():
    pytest.importorskip("sentence_transformers", exc_type=ImportError,
                        reason="sentence-transformers not importable in this environment")
    from src.embedder import SemanticEmbedder

    return SemanticEmbedder()
