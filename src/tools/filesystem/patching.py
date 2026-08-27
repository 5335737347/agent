from sandbox import Workspace
from tools import Tool, ToolResult, tool


def make_apply_patch_tool(
    workspace: Workspace, *, max_patch_bytes: int = 100_100, max_fils: int = 20
) -> Tool:
    """..."""

    @tool
    def apply_patch(patch: str) -> str | ToolResult:
        """..."""
