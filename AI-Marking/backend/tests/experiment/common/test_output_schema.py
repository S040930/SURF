"""The strict JSON Schema subset that ``codex exec --output-schema`` requires.

The assertions here are deliberately independent of the implementation: the
walker re-derives the two server-side rules from the document, so a bug in
``strictify_output_schema`` cannot make its own tests pass.
"""

from typing import Any

import pytest

from app.experiment.common.output_schema import (
    SchemaNotExpressibleError,
    assert_strict_output_schema,
    strictify_output_schema,
)
from app.experiment.memory_study.protocol import ScoreOutput

# Verbatim from the pinned A-MEM controller
# (``agentic_memory/memory_system.py``, the ``analyze_content`` call).  It has
# properties but neither ``required`` nor ``additionalProperties``, which is the
# shape the API rejected for every memory write in the pilot run.
AMEM_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "keywords": {"type": "array", "items": {"type": "string"}},
        "context": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}

# mem0's extraction contract: the failing node lives one level down, inside
# ``items``, which is why the API error named context ('properties','memory','items').
NESTED_ARRAY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "memory": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
        }
    },
}

_OPAQUE_KEYWORDS = {"default", "examples", "enum", "const"}


def _walk(node: Any, path: str = "<root>"):
    """Yield every node of a JSON Schema document with its location."""
    stack: list[tuple[Any, str]] = [(node, path)]
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


def _assert_every_object_node_is_strict(schema: Any) -> int:
    """Check both API rules on every object node; return how many were checked."""
    checked = 0
    for node, where in _walk(schema):
        if node.get("type") != "object":
            continue
        checked += 1
        assert node.get("additionalProperties") is False, f"{where}: not closed"
        properties = node.get("properties")
        assert isinstance(properties, dict), f"{where}: object node without properties"
        assert list(node.get("required", [])) == list(properties), (
            f"{where}: required must list every property, "
            f"got {node.get('required')!r} for {list(properties)!r}"
        )
    assert checked >= 1, "the walker found no object node to check"
    return checked


def test_amem_analysis_schema_gains_required_and_closed_objects():
    strict = strictify_output_schema(AMEM_ANALYSIS_SCHEMA)
    _assert_every_object_node_is_strict(strict)
    assert strict["required"] == ["keywords", "context", "tags"]
    assert strict["additionalProperties"] is False
    # The fields themselves are untouched: normalisation must never rename or
    # retype anything, only close and complete the document.
    assert strict["properties"] == AMEM_ANALYSIS_SCHEMA["properties"]


def test_nested_object_inside_array_items_is_closed():
    strict = strictify_output_schema(NESTED_ARRAY_SCHEMA)
    checked = _assert_every_object_node_is_strict(strict)
    assert checked == 2  # the root and the array item
    item = strict["properties"]["memory"]["items"]
    assert item["required"] == ["text"]
    assert item["additionalProperties"] is False


def test_pydantic_score_schema_is_already_strict():
    """The healthy scoring half must be a no-op here.

    ``ScoreOutput`` sets ``extra="forbid"``, so Pydantic already emits the two
    rules and the 5040 scoring calls never depended on this module.  If this
    test starts failing, normalisation is *changing* a schema that worked.
    """
    assert_strict_output_schema(ScoreOutput.model_json_schema())


@pytest.mark.parametrize(
    "schema",
    [AMEM_ANALYSIS_SCHEMA, NESTED_ARRAY_SCHEMA],
    ids=["amem-analysis", "nested-array"],
)
def test_strictification_is_idempotent(schema):
    once = strictify_output_schema(schema)
    twice = strictify_output_schema(once)
    assert once == twice
    assert_strict_output_schema(once)


@pytest.mark.parametrize(
    "node",
    [
        {"type": "object", "additionalProperties": True},
        {"type": "object"},
        {"type": "object", "additionalProperties": {"type": "string"}},
    ],
    ids=["open", "bare", "typed-map"],
)
def test_open_objects_are_refused(node):
    """The subset cannot say "some object"; inferring one would lose the response."""
    with pytest.raises(SchemaNotExpressibleError):
        strictify_output_schema(node)


def test_explicitly_empty_properties_is_honoured():
    """A caller that *declares* an empty object is not guessing, so it is legal.

    Measured against the pinned CLI, ``properties: {}`` with an empty
    ``required`` is accepted and the model returns ``{}``.  The distinction the
    module enforces is declared-empty versus inferred-empty.
    """
    strict = strictify_output_schema({"type": "object", "properties": {}})
    assert strict["properties"] == {}
    assert strict["required"] == []
    assert strict["additionalProperties"] is False


def test_nested_free_form_object_is_refused_with_its_path():
    schema = {
        "type": "object",
        "properties": {"payload": {"type": "object", "additionalProperties": True}},
    }
    with pytest.raises(SchemaNotExpressibleError) as excinfo:
        strictify_output_schema(schema)
    assert "properties.payload" in str(excinfo.value)


def test_opaque_keywords_holding_objects_are_not_treated_as_schemas():
    """``default``/``enum`` may contain JSON objects; they are values, not schemas."""
    schema = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "default": {"type": "object", "additionalProperties": True},
                "enum": ["a", {"nested": "value"}],
            }
        },
    }
    strict = strictify_output_schema(schema)
    assert strict["properties"]["mode"]["default"] == {
        "type": "object",
        "additionalProperties": True,
    }


def test_definitions_and_union_branches_are_normalized():
    schema = {
        "type": "object",
        "properties": {
            "value": {
                "anyOf": [
                    {"type": "object", "properties": {"a": {"type": "string"}}},
                    {"type": "string"},
                ]
            }
        },
        "$defs": {
            "Extra": {"type": "object", "properties": {"b": {"type": "number"}}}
        },
    }
    strict = strictify_output_schema(schema)
    checked = _assert_every_object_node_is_strict(strict)
    assert checked == 3  # root, the anyOf object branch, and the $defs entry
    assert strict["properties"]["value"]["anyOf"][0]["required"] == ["a"]
    assert strict["$defs"]["Extra"]["required"] == ["b"]


def test_assert_strict_names_the_offending_node():
    with pytest.raises(AssertionError) as excinfo:
        assert_strict_output_schema(AMEM_ANALYSIS_SCHEMA)
    assert "<root>" in str(excinfo.value)
    assert "additionalProperties" in str(excinfo.value)


def test_assert_strict_rejects_a_missing_required_entry():
    """The second rule, isolated: closed objects but an incomplete `required`."""
    with pytest.raises(AssertionError) as excinfo:
        assert_strict_output_schema(
            {
                "type": "object",
                "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                "required": ["a"],
                "additionalProperties": False,
            }
        )
    assert "required" in str(excinfo.value)
