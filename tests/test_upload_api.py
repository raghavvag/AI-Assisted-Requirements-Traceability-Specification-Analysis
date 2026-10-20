"""
test_upload_api.py — SRS / spec upload validation and the SRS preview endpoint
==============================================================================
Runs the real FastAPI app against a throw-away SQLite DB and upload directory.
Authentication is replaced by a dependency override; nothing here loads the
embedding model or needs Qdrant.
"""

import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "srs"
# DATABASE_URL / UPLOAD_DIR already point at a temp dir (tests/conftest.py).

from docx import Document as new_docx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import get_current_user  # noqa: E402
from app.database import Base, Project, User  # noqa: E402
from app.main import app  # noqa: E402

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture(scope="module")
def ctx():
    assert "trace_tests_" in settings.DATABASE_URL, "tests must never use the real database"
    engine = create_engine(settings.DATABASE_URL.replace("+aiosqlite", ""))
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as s:
        user = User(google_id="g-1", email="qa@example.com", name="QA")
        s.add(user)
        s.flush()
        p1 = Project(user_id=user.id, name="docx project")
        p2 = Project(user_id=user.id, name="empty project")
        s.add_all([p1, p2])
        s.commit()
        ids = {"user": user, "p1": p1.id, "p2": p2.id}
    app.dependency_overrides[get_current_user] = lambda: ids["user"]
    client = TestClient(app)
    yield client, ids
    app.dependency_overrides.clear()
    engine.dispose()


def _upload(client, pid, doc_type, name, data, mime="application/octet-stream"):
    return client.post(f"/api/projects/{pid}/upload", data={"doc_type": doc_type},
                       files={"file": (name, data, mime)})


def _docx(paragraphs) -> bytes:
    doc = new_docx()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class TestSrsUpload:
    def test_docx_happy_path(self, ctx):
        client, ids = ctx
        r = _upload(client, ids["p1"], "SRS", "Nexus SRS.docx",
                    (FIXTURES / "ecommerce_srs_ieee.docx").read_bytes(), DOCX_MIME)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["doc_type"] == "SRS" and body["original_filename"] == "Nexus SRS.docx"
        ex = body["extraction"]
        assert ex["n_requirements"] == 10 and ex["format"] == "docx"
        assert ex["by_strategy"] == {"table": 6, "tagged": 4}
        assert ex["requirement_ids"][:2] == ["REQ-01", "REQ-02"]
        assert (Path(settings.UPLOAD_DIR) / str(ids["p1"]) / "SRS.docx").exists()

    def test_preview_returns_stored_extraction(self, ctx):
        client, ids = ctx
        r = client.get(f"/api/projects/{ids['p1']}/srs/requirements")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["is_default"] is False and body["filename"] == "Nexus SRS.docx"
        assert len(body["requirements"]) == 10
        first = body["requirements"][0]
        assert first["source"]["locator"] == "table 3 / row 2" and first["priority"] == "High"
        assert body["report"]["skipped"]["caption"] == 2

    def test_reupload_other_format_removes_stale_file(self, ctx):
        client, ids = ctx
        md = (ROOT / "data" / "requirements" / "ecommerce_srs.md").read_bytes()
        r = _upload(client, ids["p1"], "SRS", "ecommerce_srs.md", md, "text/markdown")
        assert r.status_code == 201, r.text
        proj = Path(settings.UPLOAD_DIR) / str(ids["p1"])
        assert (proj / "SRS.md").exists() and not (proj / "SRS.docx").exists()
        assert not list(proj.glob(".SRS.upload*"))

    def test_pdf_upload(self, ctx):
        pdf = FIXTURES / "ecommerce_srs_ieee.pdf"
        if not pdf.exists():
            pytest.skip("PDF fixture not generated")
        client, ids = ctx
        r = _upload(client, ids["p1"], "SRS", "srs.pdf", pdf.read_bytes(), "application/pdf")
        assert r.status_code == 201, r.text
        assert r.json()["extraction"]["n_requirements"] == 10

    def test_default_srs_preview_when_nothing_uploaded(self, ctx):
        client, ids = ctx
        body = client.get(f"/api/projects/{ids['p2']}/srs/requirements").json()
        assert body["is_default"] is True and len(body["requirements"]) == 10


class TestSrsRejections:
    @pytest.mark.parametrize("name", ["srs.exe", "srs.json", "srs.doc", "srs"])
    def test_wrong_extension(self, ctx, name):
        client, ids = ctx
        r = _upload(client, ids["p2"], "SRS", name, b"REQ-01: x\nThe system must work.")
        assert r.status_code == 400
        assert "not allowed" in r.json()["detail"]

    def test_docx_bad_magic(self, ctx):
        client, ids = ctx
        r = _upload(client, ids["p2"], "SRS", "srs.docx", b"REQ-01: plain text renamed to docx")
        assert r.status_code == 400 and "not a valid .docx" in r.json()["detail"]

    def test_pdf_bad_magic(self, ctx):
        client, ids = ctx
        r = _upload(client, ids["p2"], "SRS", "srs.pdf", b"hello")
        assert r.status_code == 400

    def test_empty_file(self, ctx):
        client, ids = ctx
        assert _upload(client, ids["p2"], "SRS", "srs.md", b"").status_code == 400

    def test_oversize(self, ctx, monkeypatch):
        client, ids = ctx
        monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 0)
        r = _upload(client, ids["p2"], "SRS", "srs.md", b"REQ-01: Too big\nbody")
        assert r.status_code == 413

    def test_docx_without_requirements_is_422_with_report(self, ctx):
        client, ids = ctx
        r = _upload(client, ids["p2"], "SRS", "notes.docx", _docx(["Meeting notes", "Nothing formal here."]),
                    DOCX_MIME)
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert "No requirements were found" in detail["message"]
        assert detail["report"]["format"] == "docx"
        proj = Path(settings.UPLOAD_DIR) / str(ids["p2"])
        assert not list(proj.glob("SRS.*")) and not list(proj.glob(".SRS.upload*"))

    def test_scanned_pdf_is_422(self, ctx):
        client, ids = ctx
        r = _upload(client, ids["p2"], "SRS", "scan.pdf", (FIXTURES / "scanned.pdf").read_bytes(), "application/pdf")
        assert r.status_code == 422 and "no text layer" in r.json()["detail"]["message"]

    def test_binary_text_file(self, ctx):
        client, ids = ctx
        r = _upload(client, ids["p2"], "SRS", "srs.txt", b"\x00\x01\x02binary")
        assert r.status_code == 400


class TestSpecUpload:
    def test_openapi_json(self, ctx):
        client, ids = ctx
        spec = (ROOT / "data" / "api_specs" / "store_v1.json").read_bytes()
        r = _upload(client, ids["p1"], "API_V1", "store_v1.json", spec, "application/json")
        assert r.status_code == 201 and r.json()["extraction"] is None

    def test_spec_rejects_docx(self, ctx):
        client, ids = ctx
        r = _upload(client, ids["p1"], "API_V1", "spec.docx", b"PK\x03\x04")
        assert r.status_code == 400

    def test_invalid_json(self, ctx):
        client, ids = ctx
        r = _upload(client, ids["p1"], "API_V2", "v2.json", b"{not json")
        assert r.status_code == 422

    def test_json_without_paths(self, ctx):
        client, ids = ctx
        r = _upload(client, ids["p1"], "API_V2", "v2.json", json.dumps({"openapi": "3.0.0"}).encode())
        assert r.status_code == 422 and "paths" in r.json()["detail"]

    def test_invalid_doc_type(self, ctx):
        client, ids = ctx
        assert _upload(client, ids["p1"], "README", "a.md", b"x").status_code == 400
