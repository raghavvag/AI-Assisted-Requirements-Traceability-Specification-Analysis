"""
src/srs — Industrial SRS ingestion
==================================
Reads formal Software Requirements Specification documents (.docx, .pdf) as
well as the original Markdown/text format, removes boilerplate, and returns
clean requirement records for the embedding pipeline.

    from src.srs import extract_requirements_with_report
    requirements, report = extract_requirements_with_report("docs/SRS.docx")

See docs/plan.md §2 for the design.
"""

from src.srs.errors import (
    InvalidDocumentError,
    NoRequirementsFoundError,
    ScannedPdfError,
    SRSExtractionError,
    UnsupportedFormatError,
)
from src.srs.extractor import (
    SUPPORTED_EXTENSIONS,
    detect_format,
    extract_from_blocks,
    extract_requirements,
    extract_requirements_with_report,
)
from src.srs.options import DEFAULT_ID_PATTERN, ExtractionOptions
from src.srs.report import ExtractionReport

__all__ = [
    "DEFAULT_ID_PATTERN",
    "ExtractionOptions",
    "ExtractionReport",
    "InvalidDocumentError",
    "NoRequirementsFoundError",
    "SRSExtractionError",
    "SUPPORTED_EXTENSIONS",
    "ScannedPdfError",
    "UnsupportedFormatError",
    "detect_format",
    "extract_from_blocks",
    "extract_requirements",
    "extract_requirements_with_report",
]
