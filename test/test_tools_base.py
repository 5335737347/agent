from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, NotRequired, TypedDict

from tools import Tool, ToolRegister, ToolResult, tool


class Mode(Enum):
    READ = "read"
    WRITE = "write"


class Options(TypedDict):
    root: str
    depth: NotRequired[int]


@dataclass
class Position:
    line: int
    column: int = 1


class ToolDecoratorTests(unittest.TestCase):
    def test_builds_schema_from_annotations_and_docstring(self) -> None:
        @tool
        def search(
            query: Annotated[str, "Text to search for"],
            paths: list[str],
            mode: Mode = Mode.READ,
            limit: int | None = None,
            options: Options | None = None,
            position: Position | None = None,
        ) -> dict[str, object]:
            """Search workspace files.

            Args:
                query: Search expression.
                paths: Workspace-relative paths.
                mode: Search mode.
                limit: Maximum matches.
                options: Extra search options.
                position: Starting position.

            Returns:
                A JSON-compatible search result.
            """
            return {"query": query, "count": limit or len(paths)}

        schema = search.parameters
        self.assertEqual(search.description, "Search workspace files.")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["required"], ["query", "paths"])
        self.assertEqual(
            schema["properties"]["query"],
            {"type": "string", "description": "Text to search for"},
        )
        self.assertEqual(schema["properties"]["paths"]["items"], {"type": "string"})
        self.assertEqual(schema["properties"]["mode"]["enum"], ["read", "write"])
        self.assertEqual(
            schema["properties"]["limit"]["anyOf"],
            [{"type": "integer"}, {"type": "null"}],
        )
        self.assertEqual(
            schema["properties"]["options"]["anyOf"][0]["required"],
            ["root"],
        )
        self.assertEqual(
            schema["properties"]["position"]["anyOf"][0]["required"],
            ["line"],
        )
        json.dumps(search.to_openai_schema())
        json.dumps(search.to_anthropic_schema())

    def test_rejects_parameters_that_cannot_be_called_by_keyword(self) -> None:
        def positional_only(value: str, /) -> str:
            return value

        with self.assertRaises(TypeError):
            tool(positional_only)

    def test_validates_provider_tool_names(self) -> None:
        with self.assertRaises(ValueError):
            tool(name="not a valid name")(lambda: None)

    def test_provider_schemas_do_not_expose_mutable_internal_state(self) -> None:
        @tool
        def ping() -> str:
            return "pong"

        schema = ping.to_openai_schema()
        schema["function"]["parameters"]["properties"]["injected"] = {}
        self.assertNotIn("injected", ping.parameters["properties"])


class ToolExecutionTests(unittest.TestCase):
    def test_normalizes_values_and_exceptions(self) -> None:
        @tool
        def structured() -> dict[str, bool]:
            return {"changed": True}

        @tool
        def explode() -> None:
            raise RuntimeError("boom")

        self.assertEqual(structured.run().content, '{"changed": true}')
        failure = explode.run()
        self.assertTrue(failure.is_error)
        self.assertEqual(failure.content, "ERROR: RuntimeError: boom")

    def test_preserves_explicit_tool_result(self) -> None:
        expected = ToolResult.error("denied")
        wrapped = Tool(
            name="explicit",
            description="Return an explicit result",
            parameters={"type": "object", "properties": {}},
            func=lambda: expected,
        )
        self.assertIs(wrapped.run(), expected)


class ToolRegisterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.register = ToolRegister()

        @self.register.register
        def add(a: int, b: int = 1) -> int:
            return a + b

        self.add = add

    def test_registers_plain_callable_and_dispatches_json(self) -> None:
        self.assertIn("add", self.register)
        self.assertEqual(len(self.register), 1)
        self.assertEqual(self.register.run("add", '{"a": 2}').content, "3")
        self.assertEqual(self.register.run("add", {"a": 2, "b": 3}).content, "5")

    def test_reports_dispatch_errors_as_tool_results(self) -> None:
        cases = [
            self.register.run("missing", {}),
            self.register.run("add", "["),
            self.register.run("add", "[]"),
            self.register.run("add", {"b": 2}),
        ]
        self.assertTrue(all(result.is_error for result in cases))

    def test_duplicate_and_unregister(self) -> None:
        with self.assertRaises(ValueError):
            self.register.register(self.add)
        self.assertIs(self.register.unregister("add"), self.add)
        self.assertNotIn("add", self.register)

    def test_supports_both_anthropic_method_spellings(self) -> None:
        self.assertEqual(
            self.register.anthropic_schema(), self.register.authropic_schema()
        )


class AsyncToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_dispatch_and_sync_tool_fallback(self) -> None:
        register = ToolRegister()

        @register.register
        async def add(a: int, b: int) -> int:
            return a + b

        @register.register
        def upper(value: str) -> str:
            return value.upper()

        self.assertEqual((await register.arun("add", '{"a": 2, "b": 3}')).content, "5")
        self.assertEqual((await register.arun("upper", {"value": "ok"})).content, "OK")
        self.assertTrue(add.run(a=1, b=2).is_error)


if __name__ == "__main__":
    unittest.main()
