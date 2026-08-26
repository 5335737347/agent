import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from agent.loop import (
    AssistantMessages,
    Message,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tools import Tool


def _encode_arguments(arguments: object) -> str:
    """..."""
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments or {}, ensure_ascii=False)


def _convert_message(message: Message) -> dict[str, Any]:
    """..."""
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}

    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.content}

    if isinstance(message, ToolMessage):
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }

    tool_calls = [
        {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": _encode_arguments(call.arguments),
            },
        }
        for call in message.tool_calls
    ]

    result: dict[str, Any] = {
        "role": "assistant",
        "content": message.content,
    }
    if tool_calls:
        result["tool_calls"] = tool_calls

    return result


@dataclass(slots=True)
class OpenAICompatibleClient:
    """..."""

    client: AsyncOpenAI
    model: str

    async def complete(
        self,
        *,
        messages: Sequence[Message],
        tools: Sequence[Tool],
    ) -> AssistantMessages:
        provider_messages = [_convert_message(message) for message in messages]

        request: dict[str, Any] = {
            "model": self.model,
            "messages": provider_messages,
        }

        if tools:
            request["tools"] = [tool.to_openai_schema() for tool in tools]

        response = await self.client.chat.completions.create(**request)

        if not response.choices:
            raise RuntimeError("Mode; returned no choices")

        message = response.choices[0].message
        tool_calls = tuple(
            ToolCall(
                id=call.id, name=call.function.name, arguments=call.function.arguments
            )
            for call in message.tool_calls or ()
        )

        return AssistantMessages(
            content=message.content,
            tool_calls=tool_calls,
        )
