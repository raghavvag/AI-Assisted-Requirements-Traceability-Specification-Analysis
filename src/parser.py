"""
parser.py — SRS & OpenAPI Ingestion Module
============================================
Responsibility: Parse and normalise both input artefact types into clean,
                semantically rich text chunks ready for embedding.

Inputs:
  - SRS document: .docx / .pdf / .md / .txt (data/requirements/ecommerce_srs.md)
  - OpenAPI 3.0 JSON/YAML spec file    (data/api_specs/store_v*.json)

Outputs:
  - List[Dict] of requirement chunks: {id, title, text}
  - List[Dict] of endpoint chunks:   {id, method, path, text}
"""

import json
import re
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

import yaml

if TYPE_CHECKING:  # pragma: no cover
    from src.srs.options import ExtractionOptions

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SRS Parser
# ---------------------------------------------------------------------------

def extract_requirements(filepath: str, options: Optional["ExtractionOptions"] = None) -> List[Dict]:
    """
    Parse an SRS document into a list of requirement dicts.

    Supported formats (dispatched on the file extension, see src/srs/):
      - .docx  formal Word SRS: requirement tables, tagged headings/paragraphs
               (FR-1, REQ-04, [NFR-2], BNK-REQ-01), boilerplate removed
      - .pdf   text-layer PDF exported from an SRS
      - .md / .txt  the original "REQ-N: Title" + body format (unchanged
               behaviour), falling back to the structured strategies

    Args:
        filepath: Absolute or relative path to the SRS file.
        options:  Optional src.srs.ExtractionOptions (custom ID pattern, ...).

    Returns:
        List of dicts. Always has {"id", "title", "text"}; also carries
        type / priority / section / source metadata.

    Raises:
        FileNotFoundError: If the SRS file does not exist.
        ValueError: If no requirements are found or the file cannot be read
                    (src.srs.SRSExtractionError, which subclasses ValueError).
    """
    from src.srs import extract_requirements as _extract

    requirements = _extract(filepath, options)
    logger.info("Extracted %d requirements from %s", len(requirements), filepath)
    return requirements


# ---------------------------------------------------------------------------
# OpenAPI Parser
# ---------------------------------------------------------------------------

def load_openapi_spec(filepath: str) -> Dict:
    """
    Load an OpenAPI 3.0 / Swagger 2.0 spec from JSON or YAML.
    Attempts prance-based $ref resolution first; falls back to raw load.

    Args:
        filepath: Path to the .json or .yaml spec file.

    Returns:
        Parsed spec as a Python dict.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"OpenAPI spec not found: {filepath}")

    # --- Attempt prance resolution (resolves $ref pointers) ---
    try:
        import prance  # type: ignore
        parser = prance.ResolvingParser(str(path.resolve()), lazy=False)
        spec = parser.specification
        logger.info("Loaded OpenAPI spec via prance (with $ref resolution): %s", filepath)
        return spec
    except Exception as prance_err:
        logger.warning(
            "prance resolution failed (%s). Falling back to raw JSON/YAML load.", prance_err
        )

    # --- Fallback: raw load ---
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        spec = yaml.safe_load(raw)
    else:
        spec = json.loads(raw)

    logger.info("Loaded OpenAPI spec via raw parser: %s", filepath)
    return spec


def _build_endpoint_text(method: str, path_str: str, operation: Dict) -> str:
    """
    Build a single, semantically rich text string representing one API endpoint.

    Combines: HTTP method + path + summary + description + parameter names/descriptions
              + request body schema field names/descriptions + response description.

    This concatenated representation is fed to the embedding model to produce
    a dense vector capturing the full business intent of the endpoint.
    """
    parts = [f"{method.upper()} {path_str}"]

    # Operation-level fields
    for field in ("summary", "description"):
        value = operation.get(field, "")
        if value:
            parts.append(str(value).strip())

    # Parameters (query, path, header)
    for param in operation.get("parameters", []):
        param_name = param.get("name", "")
        param_desc = param.get("description", "")
        if param_name:
            parts.append(f"parameter {param_name}: {param_desc}".strip())

    # Request body schema properties
    req_body = operation.get("requestBody", {})
    content = req_body.get("content", {})
    for media_type, media_obj in content.items():
        schema = media_obj.get("schema", {})
        _extract_schema_text(schema, parts)

    # Response descriptions (focus on 200/201)
    responses = operation.get("responses", {})
    for status_code in ("200", "201"):
        resp = responses.get(status_code, {})
        resp_desc = resp.get("description", "")
        if resp_desc:
            parts.append(f"response: {resp_desc}".strip())
        # Also extract response schema properties if present
        resp_content = resp.get("content", {})
        for _, resp_media in resp_content.items():
            resp_schema = resp_media.get("schema", {})
            _extract_schema_text(resp_schema, parts)

    full_text = " | ".join(p for p in parts if p)
    # Normalise whitespace
    full_text = re.sub(r"\s+", " ", full_text).strip()
    return full_text


def _extract_schema_text(schema: Dict, parts: List[str], depth: int = 0):
    """
    Recursively extract property names and descriptions from a JSON Schema object
    and append them as text fragments to `parts`.

    Handles: object properties, array items, nested objects (depth-limited to 2).
    """
    if depth > 2 or not isinstance(schema, dict):
        return

    schema_desc = schema.get("description", "")
    if schema_desc:
        parts.append(schema_desc.strip())

    # Object properties
    for prop_name, prop_schema in schema.get("properties", {}).items():
        prop_desc = prop_schema.get("description", "") if isinstance(prop_schema, dict) else ""
        parts.append(
            f"field {prop_name}: {prop_desc}".strip() if prop_desc else f"field {prop_name}"
        )
        if isinstance(prop_schema, dict):
            _extract_schema_text(prop_schema, parts, depth + 1)

    # Array items
    items_schema = schema.get("items", {})
    if items_schema and isinstance(items_schema, dict):
        _extract_schema_text(items_schema, parts, depth + 1)


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _resolve_local_refs(node, root: Dict, _seen: Optional[frozenset] = None):
    """
    Inline local JSON references ("$ref": "#/components/schemas/Refund") so a
    spec dict fetched from somewhere other than a file (GitHub API, git show)
    gives the same endpoint text as a prance-resolved file. Remote refs are left
    as they are; reference cycles are cut at the first repetition.
    """
    seen = _seen or frozenset()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            if ref in seen:
                return {}
            target = root
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or part not in target:
                    return node                                  # dangling ref: keep as-is
                target = target[part]
            merged = {**_resolve_local_refs(target, root, seen | {ref}),
                      **{k: v for k, v in node.items() if k != "$ref"}}
            return merged
        return {k: _resolve_local_refs(v, root, seen) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_local_refs(v, root, seen) for v in node]
    return node


def extract_endpoints_from_spec(spec: Dict, source: str = "") -> List[Dict]:
    """
    Extract all endpoints from an already-loaded OpenAPI 3.x / Swagger 2.0 spec.

    This is the format-independent core: the CLI, the review engine and the
    GitHub App pass specs they read from git or the GitHub API straight in,
    without writing temp files. Local `$ref`s are resolved here.

    Each endpoint produces a dict with:
        - id:     Canonical identifier, e.g. "POST /api/v1/refund"
        - method: HTTP verb in uppercase, e.g. "POST"
        - path:   URL path string, e.g. "/api/v1/refund"
        - text:   Full semantic text representation for embedding
        - operation_id, summary

    Args:
        spec:   Parsed spec as a dict.
        source: Label for log and error messages (file path, "git:HEAD~1:openapi.yaml", ...).

    Returns:
        List of endpoint dicts sorted by (path, method).

    Raises:
        ValueError: If `spec` is not a mapping or has no 'paths' section.
    """
    label = source or "<spec>"
    if not isinstance(spec, dict):
        raise ValueError(f"OpenAPI spec must be a mapping, got {type(spec).__name__}: {label}")

    paths = spec.get("paths", {})
    if not paths or not isinstance(paths, dict):
        raise ValueError(f"OpenAPI spec has no 'paths' section: {label}")

    if '"$ref": "#/' in json.dumps(paths, default=str):
        paths = _resolve_local_refs(paths, spec)

    endpoints = []
    for path_str, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            # path-level keys such as "parameters" / "summary" are not operations
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue

            endpoint_id = f"{method.upper()} {path_str}"
            endpoints.append({
                "id": endpoint_id,
                "method": method.upper(),
                "path": path_str,
                "text": _build_endpoint_text(method, path_str, operation),
                "operation_id": operation.get("operationId", ""),
                "summary": operation.get("summary", ""),
            })
            logger.debug("Parsed endpoint: %s", endpoint_id)

    # Stable sort: alphabetical by path, then method
    endpoints.sort(key=lambda e: (e["path"], e["method"]))
    logger.info("Extracted %d endpoints from %s", len(endpoints), label)
    return endpoints


def extract_endpoints(filepath: str) -> List[Dict]:
    """
    Parse an OpenAPI spec file (.json / .yaml) and extract all endpoints.

    Backward-compatible wrapper: loads the file (prance $ref resolution, raw
    JSON/YAML fallback) and delegates to `extract_endpoints_from_spec`.

    Raises:
        FileNotFoundError: If the spec file does not exist.
        ValueError: If the spec has no 'paths' key.
    """
    return extract_endpoints_from_spec(load_openapi_spec(filepath), source=str(filepath))


# ---------------------------------------------------------------------------
# Convenience: load both artefacts together
# ---------------------------------------------------------------------------

def load_artefacts(
    srs_path: str,
    spec_path: str,
) -> tuple[List[Dict], List[Dict]]:
    """
    Convenience wrapper: parse both SRS and OpenAPI spec in one call.

    Returns:
        (requirements, endpoints) — both as List[Dict]
    """
    requirements = extract_requirements(srs_path)
    endpoints = extract_endpoints(spec_path)
    return requirements, endpoints
