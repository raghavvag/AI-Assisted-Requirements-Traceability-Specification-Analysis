"""
test_parser_spec.py — extract_endpoints_from_spec (dict input, T2.3)
"""

import json
from pathlib import Path

import pytest
import yaml

from src.parser import extract_endpoints, extract_endpoints_from_spec

ROOT = Path(__file__).parent.parent
SPECS = [ROOT / "data" / "api_specs" / "store_v1.json", ROOT / "data" / "api_specs" / "store_v2.json",
         ROOT / "data" / "samples" / "banking" / "aurora_v1.json"]


@pytest.mark.parametrize("path", SPECS, ids=lambda p: p.name)
def test_dict_and_file_give_identical_endpoints(path):
    spec = json.loads(path.read_text(encoding="utf-8"))
    assert extract_endpoints_from_spec(spec, str(path)) == extract_endpoints(str(path))


def test_yaml_text_roundtrip():
    spec = json.loads(SPECS[0].read_text(encoding="utf-8"))
    from_yaml = yaml.safe_load(yaml.safe_dump(spec, sort_keys=False))   # key order = field order in the text
    assert extract_endpoints_from_spec(from_yaml) == extract_endpoints_from_spec(spec)


def _spec_with_refs():
    return {
        "openapi": "3.0.0",
        "paths": {
            "/refund": {
                "parameters": [{"name": "ignored-path-level"}],
                "post": {
                    "summary": "Refund a payment",
                    "requestBody": {"content": {"application/json": {
                        "schema": {"$ref": "#/components/schemas/Refund"}}}},
                    "responses": {"200": {"$ref": "#/components/responses/Ok"}},
                },
            },
        },
        "components": {
            "schemas": {
                "Refund": {"type": "object", "properties": {
                    "transaction_id": {"type": "string", "description": "client idempotency key"},
                    "parent": {"$ref": "#/components/schemas/Refund"},          # cycle
                }},
            },
            "responses": {"Ok": {"description": "Refund receipt"}},
        },
    }


def test_local_refs_are_resolved():
    eps = extract_endpoints_from_spec(_spec_with_refs(), "inline")
    assert [e["id"] for e in eps] == ["POST /refund"]
    text = eps[0]["text"]
    assert "field transaction_id: client idempotency key" in text
    assert "response: Refund receipt" in text


def test_dangling_ref_is_kept_without_crash():
    spec = _spec_with_refs()
    del spec["components"]["responses"]
    eps = extract_endpoints_from_spec(spec)
    assert eps[0]["id"] == "POST /refund"


@pytest.mark.parametrize("spec", [{}, {"paths": {}}, {"paths": []}, ["not", "a", "dict"], None])
def test_invalid_specs(spec):
    with pytest.raises(ValueError):
        extract_endpoints_from_spec(spec, "bad")


def test_source_label_in_error():
    with pytest.raises(ValueError, match="git:HEAD~1:openapi.yaml"):
        extract_endpoints_from_spec({"openapi": "3.0.0"}, "git:HEAD~1:openapi.yaml")
