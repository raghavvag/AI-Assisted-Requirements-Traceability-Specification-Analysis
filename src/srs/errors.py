"""
src/srs/errors.py — Extraction errors
=====================================
All subclass ValueError so existing callers that catch ValueError from
`src.parser.extract_requirements` keep working.
"""

from __future__ import annotations

from typing import Optional

from src.srs.report import ExtractionReport


class SRSExtractionError(ValueError):
    """The document could not be turned into requirements. Carries the report
    (when one exists) so the API can tell the user what was seen."""

    def __init__(self, message: str, report: Optional[ExtractionReport] = None):
        super().__init__(message)
        self.message = message
        self.report = report


class UnsupportedFormatError(SRSExtractionError):
    """Extension we do not read (.doc, .docm, .rtf, ...)."""


class InvalidDocumentError(SRSExtractionError):
    """File claims a format but is corrupt, encrypted or unsafe (zip bomb)."""


class ScannedPdfError(SRSExtractionError):
    """PDF has no text layer; OCR is out of scope."""


class NoRequirementsFoundError(SRSExtractionError):
    """Document parsed fine but no strategy found a requirement."""
