import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAIError

from agent.loop import Agent, AgentLoopError, Message
from agent.model import OpenAICompatibleClient
from sandbox import Workspace
from tools import ToolRegistry
from tools.file_tools import make_read_file_tool


async def run_conversation(agent: Agent) -> None:
    """Run an interactive multi-turn conversation.

    Args:
        agent: Configured agent used for each user turn.
    """
    history: tuple[Message, ...] = ()

    print("Enter /clear to reset the conversation or /exit to quit.")

    while True:
        try:
            prompt = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not prompt:
            continue
        if prompt == "/exit":
            return
        if prompt == "/clear":
            history = ()
            print("Conversation cleared.")
            continue

        try:
            result = await agent.arun(prompt, history=history)
        except (AgentLoopError, OpenAIError) as error:
            print(f"ERROR: {type(error).__name__}: {error}")
            continue

        history = result.messages
        print(f"Assistant: {result.content}")


async def arun() -> None:
    """Configure the agent and start an interactive conversation."""
    load_dotenv()

    client = AsyncOpenAI(
        base_url=os.getenv("MODEL_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.getenv("MODEL_API_KEY", "ollama"),
    )
    model = OpenAICompatibleClient(
        client=client,
        model=os.getenv("MODEL_NAME", "deepcoder:14b"),
    )

    registry = ToolRegistry()
    registry.register(make_read_file_tool(Workspace(Path.cwd())))

    agent = Agent(
        model=model,
        registry=registry,
        system_prompt=(
            "You are a coding agent. Use the available tools when you need "
            "to inspect workspace files."
        ),
    )

    await run_conversation(agent)


def main() -> None:
    """Start the asynchronous command-line application."""
    asyncio.run(arun())


if __name__ == "__main__":
    main()
