"""Workspace-scoped file tools for the coding harness."""

from itertools import islice

from sandbox import Workspace, WorkspacePathError
from tools import Tool, ToolResult, tool


def make_read_file_tool(workspace: Workspace, *, max_lines: int = 200) -> Tool:
    """Create a file-reading tool bound to a workspace.

    Args:
        workspace: Workspace that limits which files the tool may access.
        max_lines: Maximum number of lines returned by one invocation.

    Returns:
        A configured tool named ``read_file``.
    """

    @tool
    def read_file(
        path: str, start_line: int = 1, end_line: int | None = None
    ) -> str | ToolResult:
        """Read a UTF-8 text file inside the workspace.

        Args:
            path: Workspace-relative file path.
            start_line: First line to return, starting from 1.
            end_line: Last line to return, inclusive. When omitted, at most
                ``max_lines`` lines are returned.

        Returns:
            Numbered file content, or an error result when the path or line
            range is invalid.
        """
        if start_line < 1:
            return ToolResult.error("ERROR: start_line must be at least 1")

        if end_line is not None and end_line < start_line:
            return ToolResult.error(
                "ERROR: end_line must be greater than or equal to start_line"
            )

        requested_end = end_line or start_line + max_lines - 1
        requested_count = requested_end - start_line + 1

        if requested_count > max_lines:
            return ToolResult.error(
                f"ERROR: cannot read more than {max_lines} lines at once"
            )

        try:
            target = workspace.resolve(path)
        except WorkspacePathError as error:
            return ToolResult.error(f"ERROR: {error}")

        if not target.exists():
            return ToolResult.error(f"ERROR: file does not exist: {path}")

        if not target.is_file():
            return ToolResult.error(f"ERROR: path is not a file: {path}")

        try:
            with target.open(encoding="utf-8") as file:
                raw_lines = list(islice(file, start_line - 1, requested_end + 1))
        except UnicodeDecodeError:
            return ToolResult.error(f"ERROR: file is not valid UTF-8: {path}")
        except OSError as error:
            return ToolResult.error(f"ERROR: could not read {path}: {error}")

        has_more = len(raw_lines) > requested_count
        selected = raw_lines[:requested_count]

        if not selected:
            return "(no lines in requested range)"

        content = "\n".join(
            f"{number}: {line.rstrip(chr(10) + chr(13))}"
            for number, line in enumerate(selected, start_line)
        )

        if has_more:
            content += (
                f"\n[Output truncated. Continue with start_line = {requested_end + 1}.]"
            )

        return content

    return read_file


if __name__ == "__main__":
    from pathlib import Path

    from tools import ToolRegister

    workspace = Workspace(Path.cwd())
    registry = ToolRegister()
    registry.register(make_read_file_tool(workspace))

    res = registry.run(
        "read_file",
        {"path": "test.py", "start_line": 1, "end_line": 20},
    )

    print(res.is_error)
