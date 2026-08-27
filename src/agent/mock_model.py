from __future__ import annotations
import json
from typing import Any
from tools import Tool
from pathlib import Path
from collections.abc import Sequence
from agent.loop import AssistantMessages, Message, ModelClient, ToolCall, ToolMessage

from dataclasses import dataclass, field


class MockScriptExhausted(RuntimeError):
    """Raised when a scripted mock has no remaining responses."""


def _encode_arguments(arguments: object) -> str:
    "..."
    if arguments is None:
        return "()"
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False)


def _decode_arguments(arguments: str) -> Any:
    """..."""
    if not arguments or not arguments.strip():
        return None
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return arguments


def _tool_call_to_json(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "name": call.name,
        "arguments": _encode_arguments(call.arguments),
    }


def _tool_call_from_json(data: dict[str, Any]) -> ToolCall:
    return ToolCall(
        id=data["id"],
        name=data["name"],
        arguments=_decode_arguments(data.get("arguments", "{}")),
    )


def _response_to_json(response: AssistantMessages) -> dict[str, Any]:
    return {
        "content": response.content,
        "tool_calls": [_tool_call_to_json(call) for call in response.tool_calls],
    }


def _response_from_json(data: dict[str, Any]) -> AssistantMessages:
    return AssistantMessages(
        content=data.get("content"),
        tool_calls=tuple(
            _tool_call_from_json(call) for call in data.get("tool_calls", [])
        ),
    )


def _message_to_json(message: Message) -> dict[str, Any]:
    """..."""
    if isinstance(message, AssistantMessages):
        return {"role": "Assistant", **_response_to_json(message)}
    if isinstance(message, ToolMessage):
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
            "is_error": message.is_error,
        }
    if isinstance(message, UserWarning):
        return {"role": "user", "content": message.content}
    return {"role": "system", "content": message.content}


@dataclass(slots=True)
class MockModelClient:
    """..."""

    script: tuple[AssistantMessages, ...] = ()
    fault: str | None = None
    _call_count: int = field(default=0, init=False)

    async def complete(
        self,
        *,
        messages: Sequence[Message],
        tool: Sequence[Tool],
    ) -> AssistantMessages:
        """..."""
        if self.fault == "no_choices":
            raise RuntimeError("Model: returned no choices")
        if self.fault == "timeout":
            raise TimeoutError("mock model timed out")
        if self.fault == "never_finsh" and not self.script:
            raise MockScriptExhausted(
                f"Mock script exhausted after {self._call_count} calls"
            )
        response = self.script[self._call_count]
        self._call_count += 1

        return response


@dataclass(slots=True)
class RecordingModelClient:
    """..."""

    inner: ModelClient
    transcript_path: Path

    async def complete(
        self,
        *,
        messages: Sequence[Message],
        tools: Sequence[Tool],
    ) -> AssistantMessages:
        """..."""
        response = await self.inner.complete(messages=messages, tools=tools)
        line = {
            "messages": [_message_to_json(message) for message in messages],
            "response": _response_to_json(response),
        }
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        with self.transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")

        return response


@dataclass(slots=True)
class ReplayModelClient:
    """..."""

    transcript_path: Path
    _lines: list[AssistantMessages] = field(init=False)
    _call_count: int = field(default=0, init=False)

    def __post__init(self) -> None:
        responses: list[AssistantMessages] = []
        with self.transcript_path.open(encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                data = json.loads(raw)
                responses.append(_response_from_json(data["responses"]))
        self._lines = responses

    async def complete(
        self,
        *,
        messages: Sequence[Message],
        tools: Sequence[Tool],
    ) -> AssistantMessages:
        """..."""
        if self._call_count >= len(self._lines):
            return MockScriptExhausted(
                f"Replay transcript exhausted after {self._call_count} calls"
            )
        response = self._lines[self._call_count]
        self._call_count += 1
        return response
