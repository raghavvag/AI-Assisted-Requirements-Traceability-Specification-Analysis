"""
parser.py — SRS & OpenAPI Ingestion Module
============================================
Responsibility: Parse and normalise both input artefact types into clean,
                semantically rich text chunks ready for embedding.

Inputs:
  - Natural language SRS Markdown file (data/requirements/ecommerce_srs.md)
  - OpenAPI 3.0 JSON/YAML spec file    (data/api_specs/store_v*.json)

Outputs:
  - List[Dict] of requirement chunks: {id, title, text}
  - List[Dict] of endpoint chunks:   {id, method, path, text}
"""

import json
import re
import logging
from pathlib import Path
from typing import List, Dict, Optional

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SRS Parser
# ---------------------------------------------------------------------------

def extract_requirements(filepath: str) -> List[Dict]:
    """
    Parse a structured SRS Markdown file into a list of requirement dicts.

    The file must contain blocks prefixed with 'REQ-{N}:' as separators.
    Each block is extracted into {id, title, text}.

    Args:
        filepath: Absolute or relative path to the SRS .md file.

    Returns:
        List of dicts: [{"id": "REQ-01", "title": "...", "text": "..."}]

    Raises:
        FileNotFoundError: If the SRS file does not exist.
        ValueError: If no requirement blocks are found in the file.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"SRS file not found: {filepath}")

    raw_text = path.read_text(encoding="utf-8")

    # Split on the requirement-ID pattern. Accepts a bare "REQ-4:" as well as a
    # domain-prefixed id such as "TF-REQ-01:" or "AUTH-REQ-12:" (common in real
    # SRS documents). Leading Markdown heading markers ("## ") are tolerated.
    pattern = re.compile(
        r"^\s{0,3}#{0,4}\s*((?:[A-Za-z][A-Za-z0-9]{0,7}-)?REQ-\d+):\s*(.+)$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(raw_text))

    if not matches:
        raise ValueError(
            f"No requirement blocks ((PREFIX-)REQ-N: ...) found in: {filepath}"
        )

    requirements = []
    for i, match in enumerate(matches):
        req_id = match.group(1).strip()         # e.g. "REQ-04"
        req_title = match.group(2).strip()       # e.g. "Refund Transaction Integrity"

        # Extract body text: from end of this match to start of next match (or EOF)
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        body_text = raw_text[body_start:body_end].strip()

        # Compose full semantic text: title + body sentences
        full_text = f"{req_title}. {body_text}"
        # Collapse multiple whitespace / newlines for clean embedding input
        full_text = re.sub(r"\s+", " ", full_text).strip()

        requirements.append({
            "id": req_id,
            "title": req_title,
            "text": full_text,
        })
        logger.debug("Parsed %s: %s", req_id, req_title)

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

    schema_type = schema.get("type", "")
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


def extract_endpoints(filepath: str) -> List[Dict]:
    """
    Parse an OpenAPI spec file and extract all endpoints as semantic text chunks.

    Each endpoint produces a dict with:
        - id:     Canonical identifier, e.g. "POST /api/v1/refund"
        - method: HTTP verb in uppercase, e.g. "POST"
        - path:   URL path string, e.g. "/api/v1/refund"
        - text:   Full semantic text representation for embedding

    Args:
        filepath: Path to the OpenAPI .json or .yaml spec file.

    Returns:
        List of endpoint dicts sorted by (path, method).

    Raises:
        FileNotFoundError: If the spec file does not exist.
        ValueError: If the spec has no 'paths' key.
    """
    spec = load_openapi_spec(filepath)

    paths = spec.get("paths", {})
    if not paths:
        raise ValueError(f"OpenAPI spec has no 'paths' section: {filepath}")

    # HTTP methods to extract (excludes 'parameters' key at path level)
    http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}

    endpoints = []
    for path_str, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in http_methods:
                continue
            if not isinstance(operation, dict):
                continue

            endpoint_id = f"{method.upper()} {path_str}"
            text = _build_endpoint_text(method, path_str, operation)

            endpoints.append({
                "id": endpoint_id,
                "method": method.upper(),
                "path": path_str,
                "text": text,
                "operation_id": operation.get("operationId", ""),
                "summary": operation.get("summary", ""),
            })
            logger.debug("Parsed endpoint: %s", endpoint_id)

    # Stable sort: alphabetical by path, then method
    endpoints.sort(key=lambda e: (e["path"], e["method"]))
    logger.info(
        "Extracted %d endpoints from %s", len(endpoints), filepath
    )
    return endpoints


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
