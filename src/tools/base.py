"""Tool abstractions and registry support for the coding harness."""

import asyncio
import copy
import inspect
import json
import re
import types
from collections.abc import Awaitable, Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import PurePath
from typing import (
    Annotated,
    Any,
    Literal,
    NotRequired,
    Required,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
    overload,
)
from uuid import UUID

JSONSchema = dict[str, Any]
ToolCallable = Callable[..., Any]
ToolArguments = str | Mapping[str, Any] | None

_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PARAM_RE = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)(?:\s*\([^)]*\))?\s*:\s*(?P<description>.*)$"
)
_SPHINX_PARAM_RE = re.compile(
    r"^:param\s+(?P<name>[A-Za-z_]\w*)\s*:\s*(?P<description>.*)$"
)
_ARG_SECTIONS = {"args:", "arguments:", "parameters:"}
_END_SECTIONS = {
    "attributes:",
    "examples:",
    "notes:",
    "raises:",
    "returns:",
    "warnings:",
    "yields:",
}


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Represent the normalized result of a tool invocation.

    Attributes:
        content: Text returned to the model.
        is_error: Whether the tool invocation failed.
    """

    content: str
    is_error: bool = False

    @classmethod
    def ok(cls, content: str) -> ToolResult:
        """Create a successful tool result.

        Args:
            content: Text returned by the tool.

        Returns:
            A successful tool result.
        """
        return cls(content=content)

    @classmethod
    def error(cls, message: str) -> ToolResult:
        """Create a failed tool result.

        Args:
            message: Error text returned to the model.

        Returns:
            A failed tool result that does not raise into the agent loop.
        """
        return cls(content=message, is_error=True)

    def __str__(self) -> str:
        """Return the result content.

        Returns:
            Text returned to the model.
        """
        return self.content


_TYPE_MAP: dict[type, JSONSchema] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    list: {"type": "array", "items": {}},
    tuple: {"type": "array", "items": {}},
    set: {"type": "array", "items": {}, "uniqueItems": True},
    frozenset: {"type": "array", "items": {}, "uniqueItems": True},
    dict: {"type": "object"},
}


def _type_to_schema(annotation: Any, seen: frozenset[Any] | None = None) -> JSONSchema:
    """Translate a Python type annotation into JSON Schema.

    Args:
        annotation: Annotation to translate.
        seen: Types already visited while resolving recursive definitions.

    Returns:
        A JSON Schema fragment. Unsupported annotations produce an empty schema.
    """
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {}
    if annotation is None or annotation is type(None):
        return {"type": "null"}
    if isinstance(annotation, str):
        return {}

    seen = seen or frozenset()
    try:
        if annotation in seen:
            return {}
    except TypeError:
        pass

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Annotated:
        schema = _type_to_schema(args[0], seen)
        for metadata in args[1:]:
            if isinstance(metadata, str) and "description" not in schema:
                schema["description"] = metadata
            elif isinstance(metadata, Mapping):
                schema.update(metadata)
        return schema

    if origin in (Required, NotRequired):
        return _type_to_schema(args[0], seen)

    if origin is Literal:
        values = [value.value if isinstance(value, Enum) else value for value in args]
        schema: JSONSchema = {"enum": values}
        value_types = {type(value) for value in values}
        if len(value_types) == 1:
            schema = {**_type_to_schema(value_types.pop(), seen), **schema}
        return schema

    if origin in (types.UnionType, Union):
        choices: list[JSONSchema] = []
        for choice in args:
            schema = _type_to_schema(choice, seen)
            if schema not in choices:
                choices.append(schema)
        if len(choices) == 1:
            return choices[0]
        return {"anyOf": choices}

    if isinstance(annotation, TypeVar):
        if annotation.__constraints__:
            return {
                "anyOf": [
                    _type_to_schema(constraint, seen)
                    for constraint in annotation.__constraints__
                ]
            }
        if annotation.__bound__ is not None:
            return _type_to_schema(annotation.__bound__, seen)
        return {}

    supertype = getattr(annotation, "__supertype__", None)
    if supertype is not None:
        return _type_to_schema(supertype, seen)

    if annotation in _TYPE_MAP:
        return copy.deepcopy(_TYPE_MAP[annotation])

    if origin in (list, Sequence, set, frozenset, AbstractSet):
        item_schema = _type_to_schema(args[0], seen) if args else {}
        schema = {"type": "array", "items": item_schema}
        if origin in (set, frozenset, AbstractSet):
            schema["uniqueItems"] = True
        return schema

    if origin is tuple:
        if not args:
            return {"type": "array", "items": {}}
        if len(args) == 2 and args[1] is Ellipsis:
            return {"type": "array", "items": _type_to_schema(args[0], seen)}
        return {
            "type": "array",
            "prefixItems": [_type_to_schema(item, seen) for item in args],
            "minItems": len(args),
            "maxItems": len(args),
        }

    if origin in (dict, Mapping):
        value_schema = _type_to_schema(args[1], seen) if len(args) == 2 else {}
        return {"type": "object", "additionalProperties": value_schema}

    if annotation in (datetime, date, time):
        formats = {datetime: "date-time", date: "date", time: "time"}
        return {"type": "string", "format": formats[annotation]}
    if annotation is UUID:
        return {"type": "string", "format": "uuid"}
    if inspect.isclass(annotation) and issubclass(annotation, PurePath):
        return {"type": "string", "format": "path"}

    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        values = [member.value for member in annotation]
        schema = {"enum": values}
        value_types = {type(value) for value in values}
        if len(value_types) == 1:
            schema = {**_type_to_schema(value_types.pop(), seen), **schema}
        return schema

    next_seen = seen | {annotation}
    if is_typeddict(annotation):
        hints = _resolved_type_hints(annotation)
        required_keys: set[str] = set(getattr(annotation, "__required_keys__", ()))
        optional_keys: set[str] = set(getattr(annotation, "__optional_keys__", ()))
        for name, value in hints.items():
            qualifier = get_origin(value)
            if qualifier is Required:
                required_keys.add(name)
                optional_keys.discard(name)
            elif qualifier is NotRequired:
                required_keys.discard(name)
                optional_keys.add(name)
            elif name not in required_keys and name not in optional_keys:
                if getattr(annotation, "__total__", True):
                    required_keys.add(name)
        return {
            "type": "object",
            "properties": {
                name: _type_to_schema(value, next_seen) for name, value in hints.items()
            },
            "required": [name for name in hints if name in required_keys],
            "additionalProperties": False,
        }

    if inspect.isclass(annotation) and is_dataclass(annotation):
        hints = _resolved_type_hints(annotation)
        properties: dict[str, JSONSchema] = {}
        required: list[str] = []
        for item in fields(annotation):
            schema = _type_to_schema(hints.get(item.name, item.type), next_seen)
            if item.default is MISSING and item.default_factory is MISSING:
                required.append(item.name)
            elif item.default is not MISSING and _is_json_value(item.default):
                schema["default"] = item.default
            properties[item.name] = schema
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    return {}


def _resolved_type_hints(target: Any) -> dict[str, Any]:
    """Resolve annotations on a function or class.

    Args:
        target: Function or class whose annotations should be resolved.

    Returns:
        Resolved type hints, or the original annotations when a referenced name
        is unavailable.
    """
    try:
        return get_type_hints(target, include_extras=True)
    except NameError, TypeError:
        return dict(getattr(target, "__annotations__", {}))


def _is_json_value(value: Any) -> bool:
    """Check whether a value can be encoded as JSON.

    Args:
        value: Value to inspect.

    Returns:
        True when json.dumps can encode the value; otherwise False.
    """
    try:
        json.dumps(value)
    except TypeError, ValueError:
        return False
    return True


def _format_result(value: Any) -> str:
    """Convert a tool return value to text for the model.

    Args:
        value: Value returned by a tool callable.

    Returns:
        The value as plain text or JSON text.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError, ValueError:
        return str(value)


def _error_result(error: Exception) -> ToolResult:
    """Convert an exception to a failed tool result.

    Args:
        error: Exception raised by a tool callable.

    Returns:
        A normalized failed result.
    """
    detail = str(error)
    suffix = f": {detail}" if detail else ""
    return ToolResult.error(f"ERROR: {type(error).__name__}{suffix}")


def _parse_docstring(func: ToolCallable) -> tuple[str, dict[str, str]]:
    """Extract tool metadata from a callable docstring.

    Args:
        func: Callable whose docstring should be parsed.

    Returns:
        A pair containing the tool description and parameter descriptions.
    """
    doc = inspect.getdoc(func) or ""
    if not doc:
        return "", {}

    description_lines: list[str] = []
    parameter_descriptions: dict[str, str] = {}
    in_arguments = False
    current_parameter: str | None = None

    for line in doc.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()

        sphinx_match = _SPHINX_PARAM_RE.match(stripped)
        if sphinx_match:
            parameter_descriptions[sphinx_match["name"]] = sphinx_match[
                "description"
            ].strip()
            current_parameter = None
            continue

        if lowered in _ARG_SECTIONS:
            in_arguments = True
            current_parameter = None
            continue
        if lowered in _END_SECTIONS:
            break

        if in_arguments:
            match = _PARAM_RE.match(stripped)
            if match:
                current_parameter = match["name"]
                parameter_descriptions[current_parameter] = match["description"].strip()
            elif stripped and current_parameter:
                existing = parameter_descriptions[current_parameter]
                parameter_descriptions[current_parameter] = " ".join(
                    part for part in (existing, stripped) if part
                )
            continue

        if not stripped.startswith(":"):
            description_lines.append(line.rstrip())

    description = "\n".join(description_lines).strip()
    return description, parameter_descriptions


def _validate_tool_name(name: str) -> None:
    """Validate a tool name against provider naming restrictions.

    Args:
        name: Tool name to validate.

    Raises:
        ValueError: If the name cannot be sent to a model provider.
    """
    if not _TOOL_NAME_RE.fullmatch(name):
        raise ValueError(
            "Tool name must be 1-64 characters containing only letters, "
            "numbers, underscores, or hyphens"
        )


@dataclass(slots=True)
class Tool:
    """Combine a callable with metadata exposed to model providers.

    Attributes:
        name: Provider-facing tool name.
        description: Instructions shown to the model.
        parameters: JSON Schema describing accepted arguments.
        func: Python callable executed for the tool.
    """

    name: str
    description: str
    parameters: JSONSchema
    func: ToolCallable

    def __post_init__(self) -> None:
        """Validate the configured tool metadata.

        Raises:
            TypeError: If the callable or parameter schema has an invalid type.
            ValueError: If the provider-facing name is invalid.
        """
        _validate_tool_name(self.name)
        if not callable(self.func):
            raise TypeError("Tool func must be callable")
        if not isinstance(self.parameters, dict):
            raise TypeError("Tool parameters must be a JSON Schema object")

    def to_openai_schema(self) -> JSONSchema:
        """Build the OpenAI function-tool definition.

        Returns:
            A new OpenAI-compatible tool schema.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": copy.deepcopy(self.parameters),
            },
        }

    def to_anthropic_schema(self) -> JSONSchema:
        """Build the Anthropic tool definition.

        Returns:
            A new Anthropic-compatible tool schema.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": copy.deepcopy(self.parameters),
        }

    def run(self, **kwargs: Any) -> ToolResult:
        """Invoke the tool from synchronous harness code.

        Args:
            **kwargs: Arguments passed to the underlying callable.

        Returns:
            The normalized tool result. Callable and argument errors are returned
            as failed results rather than raised.
        """
        argument_error = self._argument_error(kwargs)
        if argument_error is not None:
            return argument_error

        if inspect.iscoroutinefunction(self.func):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(self.arun(**kwargs))
            return ToolResult.error(
                f"ERROR: async tool '{self.name}' must be called with arun() "
                "while an event loop is running"
            )

        try:
            value = self.func(**kwargs)
        except Exception as error:  # noqa: BLE001 - Isolate failures at tool boundary.
            return _error_result(error)

        if inspect.isawaitable(value):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(self._await_result(value))
            if inspect.iscoroutine(value):
                value.close()
            return ToolResult.error(
                f"ERROR: async tool '{self.name}' must be called with arun() "
                "while an event loop is running"
            )
        return self._normalize_result(value)

    async def arun(self, **kwargs: Any) -> ToolResult:
        """Invoke the tool from an asynchronous agent loop.

        Args:
            **kwargs: Arguments passed to the underlying callable.

        Returns:
            The normalized tool result. Awaitable return values are awaited.
        """
        argument_error = self._argument_error(kwargs)
        if argument_error is not None:
            return argument_error

        try:
            if inspect.iscoroutinefunction(self.func):
                value = await self.func(**kwargs)
            else:
                value = self.func(**kwargs)
                if inspect.isawaitable(value):
                    value = await value
        except Exception as error:  # noqa: BLE001 - Isolate failures at tool boundary.
            return _error_result(error)
        return self._normalize_result(value)

    async def _await_result(self, value: Awaitable[Any]) -> ToolResult:
        """Await and normalize a tool return value.

        Args:
            value: Awaitable returned by a tool callable.

        Returns:
            The normalized tool result.
        """
        try:
            return self._normalize_result(await value)
        except Exception as error:  # noqa: BLE001 - Isolate failures at tool boundary.
            return _error_result(error)

    def _argument_error(self, kwargs: Mapping[str, Any]) -> ToolResult | None:
        """Validate keyword arguments against the callable signature.

        Args:
            kwargs: Arguments intended for the callable.

        Returns:
            A failed result when binding fails, or None when arguments are valid.
        """
        try:
            inspect.signature(self.func).bind(**kwargs)
        except (TypeError, ValueError) as error:
            return ToolResult.error(
                f"ERROR: invalid arguments for '{self.name}': {error}"
            )
        return None

    @staticmethod
    def _normalize_result(value: Any) -> ToolResult:
        """Normalize a callable return value.

        Args:
            value: Value returned by the callable.

        Returns:
            The existing tool result or a new successful result.
        """
        if isinstance(value, ToolResult):
            return value
        return ToolResult.ok(_format_result(value))


@overload
def tool(
    func: ToolCallable,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Tool: ...


@overload
def tool(
    func: None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Callable[[ToolCallable], Tool]: ...


def tool(
    func: ToolCallable | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Tool | Callable[[ToolCallable], Tool]:
    """Convert a typed function into a model-callable tool.

    Args:
        func: Callable to convert immediately. Omit it when using the configured
            decorator form.
        name: Optional provider-facing name. Defaults to the callable name.
        description: Optional model-facing description. Defaults to the callable
            docstring.

    Returns:
        A tool when func is provided, or a decorator that creates one.

    Raises:
        TypeError: If the callable contains positional-only or variadic positional
            parameters.
        ValueError: If the selected tool name is invalid.

    Examples:
        Decorate a typed callable directly:

        >>> @tool
        ... def read_file(path: str) -> str:
        ...     return path
    """

    def decorator(target: ToolCallable) -> Tool:
        signature = inspect.signature(target)
        type_hints = _resolved_type_hints(target)
        doc_description, parameter_descriptions = _parse_docstring(target)
        properties: dict[str, JSONSchema] = {}
        required: list[str] = []
        additional_properties: bool | JSONSchema = False

        for parameter_name, parameter in signature.parameters.items():
            if parameter.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.VAR_POSITIONAL,
            ):
                raise TypeError(
                    f"Tool '{getattr(target, '__name__', type(target).__name__)}' "
                    f"cannot expose parameter '{parameter_name}' because model "
                    "tool calls only support keyword arguments"
                )

            annotation = type_hints.get(parameter_name, parameter.annotation)
            if parameter.kind is inspect.Parameter.VAR_KEYWORD:
                additional_properties = _type_to_schema(annotation)
                if not additional_properties:
                    additional_properties = True
                continue

            schema = _type_to_schema(annotation)
            parameter_description = parameter_descriptions.get(parameter_name)
            if parameter_description and "description" not in schema:
                schema["description"] = parameter_description

            if parameter.default is inspect.Parameter.empty:
                required.append(parameter_name)
            elif _is_json_value(parameter.default):
                schema["default"] = parameter.default

            properties[parameter_name] = schema

        tool_name = name or getattr(target, "__name__", type(target).__name__)
        tool_description = (
            description
            if description is not None
            else doc_description or f"Call {tool_name}"
        )
        return Tool(
            name=tool_name,
            description=tool_description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": additional_properties,
            },
            func=target,
        )

    if func is not None:
        return decorator(func)
    return decorator


@dataclass(slots=True)
class ToolRegister:
    """Store, expose, and dispatch coding harness tools.

    Attributes:
        tools: Registered tools keyed by provider-facing name.
    """

    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, candidate: Tool | ToolCallable) -> Tool:
        """Register a tool or plain callable.

        Args:
            candidate: Tool to register, or callable to convert into a tool.

        Returns:
            The registered tool.

        Raises:
            ValueError: If another tool already uses the same name.
        """
        registered = candidate if isinstance(candidate, Tool) else tool(candidate)
        if registered.name in self.tools:
            raise ValueError(f"Duplicate tool name: {registered.name}")
        self.tools[registered.name] = registered
        return registered

    def unregister(self, name: str) -> Tool:
        """Remove a registered tool.

        Args:
            name: Name of the tool to remove.

        Returns:
            The removed tool.

        Raises:
            KeyError: If no tool is registered under the name.
        """
        return self.tools.pop(name)

    def get(self, name: str) -> Tool | None:
        """Look up a registered tool.

        Args:
            name: Name of the tool to retrieve.

        Returns:
            The registered tool, or None when the name is unknown.
        """
        return self.tools.get(name)

    def __contains__(self, name: object) -> bool:
        """Check whether a tool name is registered.

        Args:
            name: Name to look up.

        Returns:
            True when the name is registered; otherwise False.
        """
        return name in self.tools

    def __len__(self) -> int:
        """Return the number of registered tools.

        Returns:
            Number of tools in the registry.
        """
        return len(self.tools)

    def openai_schema(self) -> list[JSONSchema]:
        """Build definitions for all registered OpenAI tools.

        Returns:
            OpenAI-compatible tool definitions in registration order.
        """
        return [registered.to_openai_schema() for registered in self.tools.values()]

    def anthropic_schema(self) -> list[JSONSchema]:
        """Build definitions for all registered Anthropic tools.

        Returns:
            Anthropic-compatible tool definitions in registration order.
        """
        return [registered.to_anthropic_schema() for registered in self.tools.values()]

    def authropic_schema(self) -> list[JSONSchema]:
        """Build Anthropic definitions through the legacy method name.

        Returns:
            Anthropic-compatible tool definitions in registration order.
        """
        return self.anthropic_schema()

    def run(self, name: str, arguments: ToolArguments = None) -> ToolResult:
        """Synchronously dispatch a provider tool call.

        Args:
            name: Name of the tool to invoke.
            arguments: JSON text, a mapping, or None for an argument-free call.

        Returns:
            The normalized tool result. Lookup and parsing errors are returned as
            failed results.
        """
        registered = self.tools.get(name)
        if registered is None:
            return ToolResult.error(f"ERROR: Unknown tool '{name}'")
        kwargs, error = self._parse_arguments(arguments)
        if error is not None:
            return error
        return registered.run(**kwargs)

    async def arun(self, name: str, arguments: ToolArguments = None) -> ToolResult:
        """Asynchronously dispatch a provider tool call.

        Args:
            name: Name of the tool to invoke.
            arguments: JSON text, a mapping, or None for an argument-free call.

        Returns:
            The normalized tool result. Lookup and parsing errors are returned as
            failed results.
        """
        registered = self.tools.get(name)
        if registered is None:
            return ToolResult.error(f"ERROR: Unknown tool '{name}'")
        kwargs, error = self._parse_arguments(arguments)
        if error is not None:
            return error
        return await registered.arun(**kwargs)

    @staticmethod
    def _parse_arguments(
        arguments: ToolArguments,
    ) -> tuple[dict[str, Any], ToolResult | None]:
        """Normalize provider arguments into keyword arguments.

        Args:
            arguments: JSON text, a mapping, or None.

        Returns:
            A pair containing parsed keyword arguments and an optional failed
            result.
        """
        if arguments is None:
            return {}, None
        if isinstance(arguments, str):
            if not arguments.strip():
                return {}, None
            try:
                decoded = json.loads(arguments)
            except json.JSONDecodeError as error:
                message = (
                    "ERROR: invalid JSON arguments: "
                    f"{error.msg} at line {error.lineno} column {error.colno}"
                )
                return {}, ToolResult.error(message)
        elif isinstance(arguments, Mapping):
            decoded = dict(arguments)
        else:
            return {}, ToolResult.error(
                "ERROR: arguments must be a JSON string, object, or None"
            )

        if not isinstance(decoded, dict):
            return {}, ToolResult.error("ERROR: arguments must decode to a JSON object")
        if any(not isinstance(key, str) for key in decoded):
            return {}, ToolResult.error("ERROR: argument names must be strings")
        return decoded, None


# Prefer the conventional name in new code while keeping the project's public API.
ToolRegistry = ToolRegister
