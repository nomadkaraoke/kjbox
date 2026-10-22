"""Contract harness for the app-facing singer API (``/sing/api/v1``).

``assert_matches_contract(resp, operation_id)`` validates a Flask test response
(status + JSON body) against ``docs/api/singer-v1.openapi.json``: the status
must be declared on the operation (or covered by ``default``) and the body must
match that response's schema. Every v1 integration test should call it, so the
hand-maintained contract can't drift from the code.

Request helpers (``V1``, ``bearer``) live here too; the ``gen_stub`` /
``app_token`` fixtures are in ``tests/conftest.py``.
"""

import functools
import json
import os

import jsonschema

V1 = "/sing/api/v1"

CONTRACT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "docs", "api", "singer-v1.openapi.json"
)


@functools.lru_cache(maxsize=1)
def load_contract():
    with open(CONTRACT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def find_operation(operation_id):
    """Return ``(method, path, operation)`` for an ``operationId``."""
    for path, item in load_contract()["paths"].items():
        for method, op in item.items():
            if isinstance(op, dict) and op.get("operationId") == operation_id:
                return method, path, op
    raise AssertionError(f"operationId {operation_id!r} is not in the contract")


def _resolve(obj):
    """Follow a local ``$ref`` (``#/components/...``) one level."""
    while isinstance(obj, dict) and "$ref" in obj:
        node = load_contract()
        for part in obj["$ref"].lstrip("#/").split("/"):
            node = node[part]
        obj = node
    return obj


def response_schema(operation_id, status):
    _method, _path, op = find_operation(operation_id)
    responses = op.get("responses") or {}
    spec = responses.get(str(status)) or responses.get("default")
    assert spec is not None, (
        f"{operation_id}: status {status} is not declared in the contract "
        f"(declared: {sorted(responses)})"
    )
    spec = _resolve(spec)
    content = (spec.get("content") or {}).get("application/json")
    return content["schema"] if content else None


def validate_body(body, schema):
    """Validate ``body`` against a contract schema (local ``$ref``s resolve
    against the contract's ``components``)."""
    root = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "components": load_contract()["components"],
        **schema,
    }
    jsonschema.Draft202012Validator(root).validate(body)


def assert_matches_contract(resp, operation_id):
    """Assert a test-client response is one the contract allows for this operation."""
    schema = response_schema(operation_id, resp.status_code)
    if schema is None:
        assert not resp.data, f"{operation_id}: {resp.status_code} must have no body"
        return None
    assert resp.mimetype == "application/json", (
        f"{operation_id}: expected JSON, got {resp.mimetype}"
    )
    body = resp.get_json()
    validate_body(body, schema)
    return body
