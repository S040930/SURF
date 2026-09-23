"""Strict JSON Schema for the Codex CLI output contract.

``codex exec --output-schema`` forwards the document to the responses API as a
structured-output schema, and that API accepts only a narrow subset of JSON
Schema.  It validates **every** object node in the document, nested ones
included, and rejects the whole request when one of them violates the subset:

1. every object node must carry ``"additionalProperties": false``;
2. an object node that declares ``properties`` must also declare ``required``
   as an array listing *every* one of those keys.

Measured against the pinned CLI, the server answers a violation with::

    code: invalid_json_schema
    In context=('properties', 'memory', 'items'),
    'additionalProperties' is required to be supplied and to be false.

Those two rules used to hold *by accident* rather than by construction: every
caller built its schema from a Pydantic model, and Pydantic emits
``additionalProperties: false`` for a model that forbids extra fields (see
``ScoreOutput``, which is why the scoring half of the memory study ran clean
while every memory write failed).  A schema that arrives from a third-party
framework carries no such guarantee, so the requirement is enforced here
instead of being assumed.  Applying it to an already-compliant document is a
no-op, which is what keeps the healthy path untouched.

Expressibility is bounded on purpose.  The subset cannot describe an *open*
object: ``additionalProperties: false`` with no ``properties`` accepts only
``{}``, so a caller that wanted "some object" would silently receive an empty
one.  A node that omits ``properties`` altogether therefore raises
:class:`SchemaNotExpressibleError` -- the document does not say what the object
contains, and inventing "empty" for it would lose the response.  An *explicitly*
empty ``"properties": {}`` is honoured instead, because that caller has declared
the empty object rather than left it to be inferred.

The error is a ``ValueError`` and therefore a *permanent* failure for the
worker: a malformed schema can never succeed on retry, so it should fail loudly
on the first attempt rather than burn a retry budget to produce the same 400.
"""

from __future__ import annotations

from typing import Any, Mapping

OBJECT_TYPE = "object"

# Keywords that hold literal values rather than subschemas.  ``default`` in
# particular may legitimately contain a JSON object, and recursing into it
# would mistake that value for a schema.
_OPAQUE_KEYWORDS = frozenset({"default", "examples", "enum", "const"})

# Keywords re-derived by this module for object nodes; anything a caller put
# there is intentionally replaced.
_DERIVED_KEYWORDS = frozenset({"additionalProperties", "properties", "required"})


class SchemaNotExpressibleError(ValueError):
    """A schema cannot be written in the API's strict JSON Schema subset."""


def _is_object_node(node: Mapping[str, Any]) -> bool:
    declared = node.get("type")
    if declared == OBJECT_TYPE:
        return True
    if isinstance(declared, list) and OBJECT_TYPE in declared:
        return True
    return "properties" in node


def _strict(schema: Any, path: str) -> Any:
    if isinstance(schema, list):
        return [_strict(item, f"{path}[{index}]") for index, item in enumerate(schema)]
    if not isinstance(schema, dict):
        return schema

    strict: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _DERIVED_KEYWORDS:
            continue
        if key in _OPAQUE_KEYWORDS:
            strict[key] = value
        elif isinstance(value, (dict, list)):
            strict[key] = _strict(value, f"{path}.{key}")
        else:
            strict[key] = value

    if not _is_object_node(schema):
        # ``additionalProperties`` only means something on an object, and an
        # array or scalar node never gets one added here.
        return strict

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise SchemaNotExpressibleError(
            f"{path}: object node declares no 'properties' key, and the strict "
            "subset cannot express an open object -- 'additionalProperties: "
            "false' accepts only '{}', so this node would make the model return "
            "an empty object and the caller's parser would silently find "
            "nothing. Declare the keys the caller reads; pass an explicit "
            "'properties': {} only when an empty object really is the contract."
        )
    strict["properties"] = {
        name: _strict(value, f"{path}.properties.{name}")
        for name, value in properties.items()
    }
    declared = schema.get("required")
    if (
        isinstance(declared, list)
        and len(declared) == len(properties)
        and set(declared) == set(properties)
    ):
        # Already complete: keep it verbatim, order included.  Rebuilding would
        # reorder the array in documents that the API already accepted, and
        # "normalisation is a no-op on compliant schemas" is worth more than a
        # canonical spelling that nothing observes.
        strict["required"] = list(declared)
    else:
        strict["required"] = list(properties.keys())
    strict["additionalProperties"] = False
    return strict


def strictify_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return ``schema`` rewritten into the strict subset the API accepts.

    A document that already complies passes through unchanged, down to the order
    of an existing ``required`` array, so this is safe to apply to the schemas
    that already work.  See the module docstring for the two rules enforced.
    """
    if not isinstance(schema, Mapping):  # pragma: no cover - defensive
        raise SchemaNotExpressibleError(
            f"output schema must be a JSON object, got {type(schema).__name__}"
        )
    strict = _strict(dict(schema), "<root>")
    if not isinstance(strict, dict):  # pragma: no cover - defensive
        raise SchemaNotExpressibleError("output schema must be a JSON object")
    return strict


def _iter_nodes(schema: Any, path: str = "<root>"):
    """Yield every subschema in the document together with its JSON path."""
    stack: list[tuple[Any, str]] = [(schema, path)]
    while stack:
        current, where = stack.pop()
        if isinstance(current, dict):
            yield current, where
            for key, value in current.items():
                if key in _OPAQUE_KEYWORDS:
                    continue
                if isinstance(value, (dict, list)):
                    stack.append((value, f"{where}.{key}"))
        elif isinstance(current, list):
            for index, item in enumerate(current):
                if isinstance(item, (dict, list)):
                    stack.append((item, f"{where}[{index}]"))


def assert_strict_output_schema(schema: Mapping[str, Any]) -> None:
    """Raise ``AssertionError`` unless every object node obeys the two rules.

    This is the predicate the API applies, re-derived from the document rather
    than compared against :func:`strictify_output_schema`'s output: the API does
    not care about the *order* of ``required``, and the runner serialises with
    ``sort_keys=True``, so a fixpoint comparison would reject documents that are
    perfectly legal -- ``ScoreOutput``'s own schema among them.

    Node order is checked here rather than produced by strictification for the
    same reason: normalisation must be a no-op on the schemas that already work,
    and rewriting them would change what the healthy scoring path sends.
    """
    checked = 0
    for node, where in _iter_nodes(schema):
        if not _is_object_node(node):
            continue
        checked += 1
        if node.get("additionalProperties") is not False:
            raise AssertionError(
                f"{where}: object node must set 'additionalProperties': false, "
                f"found {node.get('additionalProperties')!r}"
            )
        properties = node.get("properties")
        if not isinstance(properties, dict):
            raise AssertionError(f"{where}: object node does not declare 'properties'")
        required = node.get("required")
        if (
            not isinstance(required, list)
            or len(required) != len(properties)
            or set(required) != set(properties)
        ):
            raise AssertionError(
                f"{where}: 'required' must list every property, "
                f"got {required!r} for {list(properties)!r}"
            )
    if not checked:
        raise AssertionError("output schema declares no object node to validate")


__all__ = [
    "OBJECT_TYPE",
    "SchemaNotExpressibleError",
    "assert_strict_output_schema",
    "strictify_output_schema",
]
