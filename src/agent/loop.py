from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from tools import Tool, ToolArguments, ToolRegistry


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: ToolArguments = None


@dataclass(frozen=True, slots=True)
class SystemMessage:
    content: str


@dataclass(frozen=True, slots=True)
class UserMessage:
    content: str


@dataclass(frozen=True, slots=True)
class AssistantMessages:
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolMessage:
    tool_call_id: str
    content: str
    is_error: bool = False


type Message = SystemMessage | UserMessage | AssistantMessages | ToolMessage


class ModelClient(Protocol):
    """Interface imaplemented by each model provider."""

    async def complete(
        self,
        *,
        messages: Sequence[Message],
        tools: Sequence[Tool],
    ) -> AssistantMessages: ...


@dataclass(frozen=True, slots=True)
class AgentResult:
    """The final answer and conversation produced by an agent run."""

    content: str
    messages: tuple[Message, ...]


class AgentLoopError(RuntimeError):
    """Raised when the agent cannot reach a final answer."""


@dataclass(slots=True)
class Agent:
    model: ModelClient
    registry: ToolRegistry
    system_prompt: str | None = None
    max_turns: int = 8

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError("max_turns must be at least 1")

    async def arun(self, prompt: str) -> AgentResult:
        messages: list[Message] = []

        if self.system_prompt is not None:
            messages.append(SystemMessage(self.system_prompt))

        messages.append(UserMessage(prompt))

        for _ in range(self.max_turns):
            response = await self.model.complete(
                messages=tuple(messages),
                tools=tuple(self.registry.tools.values()),
            )
            messages.append(response)

            if not response.tool_calls and response.content is None:
                raise AgentLoopError("Model returned neither content nor tool calls")

            for call in response.tool_calls:
                result = await self.registry.arun(
                    call.name,
                    call.arguments,
                )
                messages.append(
                    ToolMessage(
                        tool_call_id=call.id,
                        content=result.content,
                        is_error=result.is_error,
                    )
                )

        raise AgentLoopError(
            f"Agent exceeded the maximum of {self.max_turns} model truns"
        )
