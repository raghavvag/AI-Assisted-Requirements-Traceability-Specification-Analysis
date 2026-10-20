"""
backend/app/services/srs_ingest.py
==================================
Upload validation + SRS extraction for the API layer. Wraps src/srs so routers
never touch file formats directly.

Validation (per doc type):
  SRS      .docx .pdf .md .txt
  API_V*   .json .yaml .yml
  all      size <= MAX_UPLOAD_MB, non-empty, content matches the extension
           (.docx = zip "PK\\x03\\x04", .pdf = "%PDF-", text = decodable, no NUL)
  .docx    zip-bomb guard + macro check (done by the reader as well)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from fastapi import HTTPException, status

from app.core.config import settings
from app.database import DocType

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.srs import (  # noqa: E402
    ExtractionOptions,
    SRSExtractionError,
    extract_requirements_with_report,
)
from src.srs.readers.docx_reader import guard_docx_archive  # noqa: E402

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS: Dict[DocType, Tuple[str, ...]] = {
    DocType.SRS: (".docx", ".pdf", ".md", ".txt"),
    DocType.API_V1: (".json", ".yaml", ".yml"),
    DocType.API_V2: (".json", ".yaml", ".yml"),
}
DEFAULT_SRS = ROOT / "data" / "requirements" / "ecommerce_srs.md"


def max_upload_bytes() -> int:
    return settings.MAX_UPLOAD_MB * 1024 * 1024


def _bad_request(message: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def validate_upload(doc_type: DocType, filename: str, content: bytes) -> str:
    """Return the lower-case extension if `content` is acceptable for `doc_type`;
    raise HTTPException(400/413/422) otherwise."""
    ext = Path(filename or "").suffix.lower()
    allowed = ALLOWED_EXTENSIONS[doc_type]
    if ext not in allowed:
        hint = " Legacy .doc files must be re-saved as .docx." if ext == ".doc" else ""
        raise _bad_request(
            f"File type '{ext or '(none)'}' is not allowed for {doc_type.value}. "
            f"Use {', '.join(allowed)}.{hint}"
        )
    if len(content) > max_upload_bytes():
        raise _bad_request(
            f"File is {len(content) / 1e6:.1f} MB; the limit is {settings.MAX_UPLOAD_MB} MB.",
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )
    if not content:
        raise _bad_request("The uploaded file is empty.")

    if ext == ".docx" and not content.startswith(b"PK\x03\x04"):
        raise _bad_request(
            "This is not a valid .docx file. If it is password-protected or a renamed .doc, "
            "open it in Word and save it as an unprotected .docx."
        )
    if ext == ".pdf" and not content.lstrip()[:5] == b"%PDF-":
        raise _bad_request("This is not a valid PDF file.")
    if ext in (".md", ".txt", ".json", ".yaml", ".yml"):
        if b"\x00" in content[:4096]:
            raise _bad_request(f"'{filename}' looks like a binary file, not {ext} text.")
        text = _decode(content)
        if ext in (".json", ".yaml", ".yml"):
            _validate_spec(text, ext)
    return ext


def _decode(content: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise _bad_request("The text file is not UTF-8 or Windows-1252 encoded.")


def _validate_spec(text: str, ext: str) -> None:
    try:
        spec = json.loads(text) if ext == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise _bad_request(f"Invalid {ext.lstrip('.').upper()}: {exc}", status.HTTP_422_UNPROCESSABLE_ENTITY)
    if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict) or not spec["paths"]:
        raise _bad_request(
            "This does not look like an OpenAPI / Swagger spec: no 'paths' section.",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )


def guard_docx(path: Path) -> None:
    guard_docx_archive(path, settings.MAX_DOCX_UNCOMPRESSED_MB * 1024 * 1024)


def parse_srs(path: Path, display_name: str, options: Optional[ExtractionOptions] = None
              ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Extract requirements. Raises HTTPException(422) with the extraction
    report when the document yields nothing usable."""
    try:
        if path.suffix.lower() == ".docx":
            guard_docx(path)
        reqs, report = extract_requirements_with_report(path, options, display_name=display_name)
    except SRSExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": exc.message,
                "report": exc.report.to_dict() if exc.report else None,
            },
        ) from exc
    return reqs, report.to_dict()


def summary_of(reqs: List[Dict[str, Any]], report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "n_requirements": len(reqs),
        "format": report.get("format", ""),
        "by_strategy": report.get("by_strategy", {}),
        "n_warnings": len(report.get("warnings", [])),
        "warnings": report.get("warnings", [])[:5],
        "requirement_ids": [str(r["id"]) for r in reqs],
    }


def dump_extraction(reqs: List[Dict[str, Any]], report: Dict[str, Any]) -> str:
    """Serialised form stored in Document.raw_text for SRS uploads."""
    return json.dumps({"kind": "srs_extraction", "version": 1, "requirements": reqs, "report": report})


def load_extraction(raw_text: Optional[str]) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Any]]]:
    if not raw_text or not raw_text.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("kind") != "srs_extraction":
        return None
    return data.get("requirements", []), data.get("report", {})
