"""Convert MCP JSON Schemas into Gemini function declarations.

MCP tools describe their arguments with JSON Schema. Gemini's function-calling
API uses a smaller, Google-specific Schema message. This module is the explicit
translation layer between the two; the MCP client never imports Gemini types.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from google.genai import types

from app.mcp_client.client import MCPTool

_MAX_FUNCTION_NAME = 64
_NAME_INVALID = re.compile(r"[^A-Za-z0-9_]")
_MISSING = object()
_JSON_SCHEMA_TYPE_TO_GEMINI = {
    "string": types.Type.STRING,
    "number": types.Type.NUMBER,
    "integer": types.Type.INTEGER,
    "boolean": types.Type.BOOLEAN,
    "array": types.Type.ARRAY,
    "object": types.Type.OBJECT,
}


@dataclass(frozen=True)
class GeminiToolSet:
    """Gemini declarations plus the mapping back to the original MCP tools."""

    mcp_tools: tuple[MCPTool, ...]
    declarations: tuple[types.FunctionDeclaration, ...]
    by_function_name: Mapping[str, MCPTool]

    def resolve(self, function_name: str) -> MCPTool | None:
        """Find the MCP tool Gemini referred to, or None if it was invented."""

        return self.by_function_name.get(function_name)

    def as_gemini_tools(self) -> list[types.Tool]:
        if not self.declarations:
            return []
        return [types.Tool(function_declarations=list(self.declarations))]


def build_tool_set(mcp_tools: Sequence[MCPTool]) -> GeminiToolSet:
    """Create unique Gemini declarations for every discovered MCP tool."""

    used_names: set[str] = set()
    declarations: list[types.FunctionDeclaration] = []
    by_name: dict[str, MCPTool] = {}

    for tool in mcp_tools:
        function_name = _unique_function_name(tool, used_names)
        used_names.add(function_name)
        description = _tool_description(tool)
        parameters = json_schema_to_gemini(tool.input_schema)

        declarations.append(
            types.FunctionDeclaration(
                name=function_name,
                description=description,
                parameters=parameters,
            )
        )
        by_name[function_name] = tool

    return GeminiToolSet(
        mcp_tools=tuple(mcp_tools),
        declarations=tuple(declarations),
        by_function_name=MappingProxyType(by_name),
    )


def json_schema_to_gemini(schema: Mapping[str, Any] | None) -> types.Schema:
    """Translate a JSON Schema object into a Gemini `types.Schema`."""

    root: Mapping[str, Any] = schema if isinstance(schema, Mapping) else {}
    converted = _convert(root, root, frozenset(), is_root=True)
    if converted.type is None and not converted.any_of:
        converted.type = types.Type.OBJECT
    return converted


def sanitize_function_name(name: str) -> str:
    """Make a name acceptable to Gemini's function-calling API."""

    cleaned = _NAME_INVALID.sub("_", name).strip()
    if not cleaned or not any(character.isalnum() for character in cleaned):
        cleaned = "tool"
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = f"_{cleaned}"
    return cleaned[:_MAX_FUNCTION_NAME]


def _unique_function_name(tool: MCPTool, used: set[str]) -> str:
    base = sanitize_function_name(tool.name)
    if base not in used:
        return base

    # A readable, deterministic fallback when two servers expose the same
    # tool name (or names that differ only by punctuation).
    server_prefix = sanitize_function_name(f"{tool.server}__{tool.name}")
    if server_prefix not in used:
        return server_prefix

    counter = 2
    while True:
        suffix = f"_{counter}"
        candidate = f"{server_prefix[: _MAX_FUNCTION_NAME - len(suffix)]}{suffix}"
        if candidate not in used:
            return candidate
        counter += 1


def _tool_description(tool: MCPTool) -> str:
    description = tool.description.strip() or f"MCP tool '{tool.name}'."
    return f"[MCP server: {tool.server}] {description}"


def _convert(
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    ref_stack: frozenset[str],
    *,
    is_root: bool = False,
) -> types.Schema:
    flat, used_refs = _flatten(schema, root, ref_stack)
    ref_stack = ref_stack | used_refs
    description = _description(flat)

    any_of = flat.get("anyOf") or flat.get("oneOf")
    if isinstance(any_of, list) and any_of:
        branches = [
            branch
            for branch in any_of
            if isinstance(branch, Mapping) and not _is_null_schema(branch)
        ]
        nullable = bool(flat.get("nullable")) or len(branches) < len(any_of)
        if not branches:
            return types.Schema(
                type=types.Type.STRING,
                description=description,
                nullable=True,
            )
        if len(branches) == 1:
            result = _convert(branches[0], root, ref_stack)
            if description:
                result.description = description
            if nullable:
                result.nullable = True
            return result
        return types.Schema(
            any_of=[_convert(branch, root, ref_stack) for branch in branches],
            description=description,
            nullable=nullable,
        )

    raw_type = flat.get("type")
    nullable = bool(flat.get("nullable"))

    if isinstance(raw_type, list):
        type_names = [str(item).lower() for item in raw_type]
        if "null" in type_names:
            nullable = True
            type_names = [name for name in type_names if name != "null"]
        if len(type_names) > 1:
            return types.Schema(
                any_of=[
                    _convert({**flat, "type": name}, root, ref_stack)
                    for name in type_names
                ],
                description=description,
                nullable=nullable,
            )
        raw_type = type_names[0] if type_names else None

    if raw_type is None:
        if (
            is_root
            or isinstance(flat.get("properties"), Mapping)
            or "additionalProperties" in flat
        ):
            raw_type = "object"
        elif "items" in flat:
            raw_type = "array"
        else:
            raw_type = "string"

    type_name = str(raw_type).lower()
    gemini_type = _JSON_SCHEMA_TYPE_TO_GEMINI.get(type_name)
    if gemini_type is None:
        return types.Schema(
            type=types.Type.STRING,
            description=_append_note(description, f"Original JSON type: {raw_type}"),
            nullable=nullable,
        )

    kwargs: dict[str, Any] = {
        "type": gemini_type,
        "description": description or None,
        "nullable": nullable or None,
    }

    _copy_scalar_constraints(flat, kwargs)

    if gemini_type == types.Type.OBJECT:
        properties = flat.get("properties")
        kwargs["properties"] = (
            {
                str(name): _convert(prop, root, ref_stack)
                for name, prop in properties.items()
                if isinstance(prop, Mapping)
            }
            if isinstance(properties, Mapping)
            else {}
        )
        required = flat.get("required")
        if isinstance(required, list):
            property_names = set(kwargs.get("properties", {}))
            required_names = [
                str(name)
                for name in required
                if isinstance(name, str)
                and (not property_names or name in property_names)
            ]
            if required_names:
                kwargs["required"] = required_names
        # Gemini's REST API rejects `additionalProperties` in function
        # declarations (the Python Schema model exposes the field, but the
        # server does not accept it). The original JSON Schema is still used
        # for argument validation, so no validation strength is lost.

    if gemini_type == types.Type.ARRAY:
        items = flat.get("items")
        if isinstance(items, Mapping):
            kwargs["items"] = _convert(items, root, ref_stack)
        elif isinstance(items, list) and items:
            kwargs["items"] = types.Schema(
                any_of=[
                    _convert(item, root, ref_stack)
                    for item in items
                    if isinstance(item, Mapping)
                ]
            )
        else:
            # JSON Schema allows arrays without `items`; Gemini does not.
            # Use a permissive, finite union so coordinate pairs, layer names,
            # and small objects all remain expressible.
            kwargs["items"] = _untyped_array_items()

    return types.Schema(**kwargs)


def _untyped_array_items(depth: int = 0) -> types.Schema:
    """A permissive item schema for JSON Schema arrays with no `items`."""

    options = [
        types.Schema(type=types.Type.STRING),
        types.Schema(type=types.Type.NUMBER),
        types.Schema(type=types.Type.BOOLEAN),
        types.Schema(type=types.Type.OBJECT),
    ]
    if depth < 2:
        options.append(
            types.Schema(type=types.Type.ARRAY, items=_untyped_array_items(depth + 1))
        )
    return types.Schema(any_of=options)


def _flatten(
    schema: Mapping[str, Any],
    root: Mapping[str, Any],
    ref_stack: frozenset[str],
) -> tuple[dict[str, Any], frozenset[str]]:
    """Resolve local `$ref` and `allOf`, reporting refs expanded in this subtree."""

    if not isinstance(schema, Mapping):
        return {}, frozenset()

    if "$ref" in schema:
        ref = schema.get("$ref")
        if not isinstance(ref, str):
            return dict(schema), frozenset()
        if ref in ref_stack:
            return (
                {
                    "type": "object",
                    "description": "Recursive object (schema cycle); pass a flat value.",
                },
                frozenset(),
            )
        target = _resolve_local_ref(ref, root)
        if target is None:
            return (
                {
                    **{k: v for k, v in schema.items() if k != "$ref"},
                    "description": _append_note(
                        _description(schema), f"Unresolved schema reference: {ref}"
                    ),
                },
                frozenset(),
            )
        merged, nested_refs = _flatten(target, root, ref_stack | {ref})
        merged.update({k: v for k, v in schema.items() if k != "$ref"})
        return merged, nested_refs | {ref}

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        merged = {k: v for k, v in schema.items() if k != "allOf"}
        expanded_refs: set[str] = set()
        for part in all_of:
            if isinstance(part, Mapping):
                flat_part, part_refs = _flatten(part, root, ref_stack)
                expanded_refs.update(part_refs)
                _merge_schema(merged, flat_part)
        return merged, frozenset(expanded_refs)

    return dict(schema), frozenset()


def _resolve_local_ref(ref: str, root: Mapping[str, Any]) -> Any:
    if not ref.startswith("#"):
        return None
    pointer = ref[1:]
    pointer = pointer.removeprefix("/")
    if not pointer:
        return root
    node: Any = root
    for token in pointer.split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, Mapping) or token not in node:
            return None
        node = node[token]
    return node


def _merge_schema(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if key == "properties" and isinstance(value, Mapping):
            existing = target.setdefault("properties", {})
            if isinstance(existing, dict):
                existing.update(value)
        elif key == "required" and isinstance(value, list):
            existing = target.setdefault("required", [])
            if isinstance(existing, list):
                for item in value:
                    if item not in existing:
                        existing.append(item)
        elif key not in target:
            target[key] = value


def _is_null_schema(schema: Mapping[str, Any]) -> bool:
    if str(schema.get("type", "")).lower() == "null":
        return True
    if schema.get("const", _MISSING) is None:
        return True
    enum = schema.get("enum")
    return isinstance(enum, list) and enum == [None]


def _description(schema: Mapping[str, Any]) -> str:
    value = schema.get("description")
    return str(value).strip() if value else ""


def _append_note(description: str, note: str) -> str:
    if not description:
        return note
    return f"{description}\n{note}"


def _copy_scalar_constraints(schema: Mapping[str, Any], kwargs: dict[str, Any]) -> None:
    numeric_fields = {
        "minimum": "minimum",
        "maximum": "maximum",
        "minLength": "min_length",
        "maxLength": "max_length",
        "minItems": "min_items",
        "maxItems": "max_items",
        "minProperties": "min_properties",
        "maxProperties": "max_properties",
    }
    for json_name, gemini_name in numeric_fields.items():
        value = schema.get(json_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            kwargs[gemini_name] = value

    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        kwargs["pattern"] = pattern

    format_value = schema.get("format")
    if isinstance(format_value, str):
        kwargs["format"] = format_value

    enum = schema.get("enum")
    if isinstance(enum, list) and enum and all(isinstance(item, str) for item in enum):
        kwargs["enum"] = enum
    elif isinstance(enum, list) and enum:
        kwargs["description"] = _append_note(
            kwargs.get("description") or "",
            "Allowed values: " + json.dumps(enum, ensure_ascii=False),
        )

    const = schema.get("const", _MISSING)
    if const is not _MISSING:
        if isinstance(const, str):
            kwargs["enum"] = [const]
        else:
            kwargs["description"] = _append_note(
                kwargs.get("description") or "",
                "Fixed value: " + json.dumps(const, ensure_ascii=False),
            )

    default = schema.get("default")
    if isinstance(default, (str, int, float, bool)):
        kwargs["default"] = default
