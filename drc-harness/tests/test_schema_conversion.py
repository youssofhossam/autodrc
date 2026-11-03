from __future__ import annotations

from google.genai import types

from app.llm.schema import (
    build_tool_set,
    json_schema_to_gemini,
    sanitize_function_name,
)
from app.mcp_client.client import MCPTool
from tests.conftest import make_tool


def test_basic_object_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "A name"},
            "count": {"type": "integer", "minimum": 1, "maximum": 10},
            "enabled": {"type": "boolean", "default": True},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["name"],
        "additionalProperties": False,
    }

    converted = json_schema_to_gemini(schema)

    assert converted.type == types.Type.OBJECT
    assert converted.properties["name"].type == types.Type.STRING
    assert converted.properties["name"].description == "A name"
    assert converted.properties["count"].type == types.Type.INTEGER
    assert converted.properties["count"].minimum == 1
    assert converted.properties["count"].maximum == 10
    assert converted.properties["enabled"].default is True
    assert converted.properties["tags"].type == types.Type.ARRAY
    assert converted.properties["tags"].items.type == types.Type.STRING
    assert converted.required == ["name"]
    # The REST API rejects additionalProperties, so the converter omits it.
    assert converted.additional_properties is None


def test_string_enum_is_preserved() -> None:
    converted = json_schema_to_gemini(
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["fast", "safe"]},
            },
        }
    )

    assert converted.properties["mode"].enum == ["fast", "safe"]


def test_non_string_enum_becomes_description() -> None:
    converted = json_schema_to_gemini(
        {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "enum": [1, 2, 3]},
            },
        }
    )

    level = converted.properties["level"]
    assert level.enum is None
    assert "Allowed values: [1, 2, 3]" in level.description


def test_local_ref_is_resolved() -> None:
    schema = {
        "type": "object",
        "properties": {
            "item": {"$ref": "#/$defs/Item"},
        },
        "required": ["item"],
        "$defs": {
            "Item": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "size": {"type": "number"},
                },
                "required": ["name"],
            }
        },
    }

    converted = json_schema_to_gemini(schema)
    item = converted.properties["item"]

    assert item.type == types.Type.OBJECT
    assert set(item.properties) == {"name", "size"}
    assert item.required == ["name"]


def test_recursive_ref_does_not_loop_forever() -> None:
    schema = {
        "type": "object",
        "properties": {
            "node": {"$ref": "#/$defs/Node"},
        },
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {
                    "child": {"$ref": "#/$defs/Node"},
                },
            }
        },
    }

    converted = json_schema_to_gemini(schema)

    node = converted.properties["node"]
    assert node.type == types.Type.OBJECT
    assert "Recursive" in node.properties["child"].description


def test_nullable_any_of_collapses_to_one_type() -> None:
    converted = json_schema_to_gemini(
        {
            "type": "object",
            "properties": {
                "notes": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
        }
    )

    notes = converted.properties["notes"]
    assert notes.type == types.Type.STRING
    assert notes.nullable is True
    assert notes.any_of is None


def test_union_any_of_uses_any_of() -> None:
    converted = json_schema_to_gemini(
        {
            "type": "object",
            "properties": {
                "value": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            },
        }
    )

    value = converted.properties["value"]
    assert value.type is None
    assert value.any_of is not None
    assert {branch.type for branch in value.any_of} == {
        types.Type.STRING,
        types.Type.INTEGER,
    }


def test_type_list_with_null_is_nullable() -> None:
    converted = json_schema_to_gemini(
        {"type": "object", "properties": {"x": {"type": ["integer", "null"]}}}
    )

    assert converted.properties["x"].type == types.Type.INTEGER
    assert converted.properties["x"].nullable is True


def test_untyped_array_gets_permissive_items_schema() -> None:
    converted = json_schema_to_gemini(
        {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "minItems": 2,
                    "description": "[x, y] coordinates",
                }
            },
        }
    )

    items = converted.properties["points"].items
    assert items is not None
    assert items.any_of is not None
    assert {branch.type for branch in items.any_of} >= {
        types.Type.STRING,
        types.Type.NUMBER,
        types.Type.OBJECT,
        types.Type.ARRAY,
    }


def test_all_of_is_merged() -> None:
    converted = json_schema_to_gemini(
        {
            "type": "object",
            "properties": {
                "thing": {
                    "allOf": [
                        {
                            "type": "object",
                            "properties": {"a": {"type": "string"}},
                            "required": ["a"],
                        },
                        {"properties": {"b": {"type": "boolean"}}},
                    ]
                }
            },
        }
    )

    thing = converted.properties["thing"]
    assert set(thing.properties) == {"a", "b"}
    assert thing.required == ["a"]


def test_function_names_are_sanitized_and_unique() -> None:
    tools = [
        MCPTool(
            server="one",
            name="klink.find_tools",
            description="Find tools.",
            input_schema={"type": "object", "properties": {}},
        ),
        MCPTool(
            server="two",
            name="find_tools",
            description="Also find tools.",
            input_schema={"type": "object", "properties": {}},
        ),
        MCPTool(
            server="one",
            name="9 weird-name!",
            description="Odd name.",
            input_schema={"type": "object", "properties": {}},
        ),
    ]

    tool_set = build_tool_set(tools)
    names = [declaration.name for declaration in tool_set.declarations]

    assert names[0] == "klink_find_tools"
    assert len(set(names)) == len(names)
    assert tool_set.resolve("klink_find_tools") is tools[0]
    assert tool_set.resolve("find_tools") is tools[1]
    assert names[2].startswith("_9_weird_name")
    assert all(len(name) <= 64 for name in names)


def test_sanitize_function_name_edge_cases() -> None:
    assert sanitize_function_name("klink.find_tools") == "klink_find_tools"
    assert sanitize_function_name("") == "tool"
    assert sanitize_function_name("123") == "_123"
    assert sanitize_function_name("!!!") == "tool"
    assert len(sanitize_function_name("x" * 100)) == 64


def test_no_argument_tool_gets_empty_object_schema() -> None:
    tool_set = build_tool_set([make_tool("ping", schema={"type": "object"})])
    parameters = tool_set.declarations[0].parameters

    assert parameters.type == types.Type.OBJECT
    assert parameters.properties == {}


def test_tool_description_includes_server_name() -> None:
    tool_set = build_tool_set([make_tool("ping", server="my-server")])

    assert tool_set.declarations[0].description.startswith("[MCP server: my-server]")


def test_default_root_type_is_object() -> None:
    converted = json_schema_to_gemini({})

    assert converted.type == types.Type.OBJECT


def test_string_constraint_keywords_are_preserved() -> None:
    converted = json_schema_to_gemini(
        {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "pattern": "^[A-Z]+$",
                    "minLength": 2,
                    "maxLength": 5,
                }
            },
        }
    )

    code = converted.properties["code"]
    assert code.pattern == "^[A-Z]+$"
    assert code.min_length == 2
    assert code.max_length == 5


def test_declaration_is_json_serializable() -> None:
    tool_set = build_tool_set(
        [
            make_tool(
                "complex.tool",
                schema={
                    "type": "object",
                    "properties": {
                        "config": {
                            "type": "object",
                            "properties": {"key": {"type": "string"}},
                            "additionalProperties": True,
                        }
                    },
                },
            )
        ]
    )

    dumped = tool_set.declarations[0].model_dump(exclude_none=True, mode="json")
    assert dumped["name"] == "complex_tool"
    assert dumped["parameters"]["properties"]["config"]["type"] == "OBJECT"
