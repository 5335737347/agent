"""Workspace-scoped directory discovery tool."""

from pathlib import Path

from sandbox import Workspace, WorkspacePathError
from tools.base import Tool, ToolResult, tool

from ._walk import DEFAULT_IGNORED_NAMES, sorted_children


def make_list_files_tool(
    workspace: Workspace, *, max_entries: int = 500, max_depth: int = 4
) -> Tool:
    """Create a bounded directory-listing tool for a workspace.

    Args:
        workspace: Workspace that constrains accessible paths.
        max_entries: Maximum number of entries returned by one call.
        max_depth: Maximum accepted traversal depth.

    Returns:
        A configured ``list_files`` tool.

    Raises:
        ValueError: If an entry or depth limit is invalid.
    """
    if max_entries < 1:
        raise ValueError("max_entries must be at least 1")
    if max_depth < 0:
        raise ValueError("max_depth must be at least 0")

    @tool
    def list_files(path: str = ".", depth: int = 2) -> str | ToolResult:
        """List files and directories inside the workspace.

        Args:
            path: Workspace-relative directory to inspect.
            depth: Number of directory levels to descend. Zero lists only the
                direct children of ``path``.

        Returns:
            Sorted workspace-relative paths. Directories end with a slash.
        """
        if depth < 0:
            return ToolResult.error("ERROR: depth must be at least 0")
        if depth > max_depth:
            return ToolResult.error(f"ERROR: depth cannot exceed {max_depth}")

        try:
            target = workspace.resolve(path)
        except WorkspacePathError as error:
            return ToolResult.error(f"ERROR: {error}")

        if not target.exists():
            return ToolResult.error(f"ERROR: directory does not exist: {path}")
        if not target.is_dir():
            return ToolResult.error(f"ERROR: path is not a directory: {path}")

        entries: list[str] = []
        truncated = False

        def visit(directory: Path, current_depth: int) -> None:
            """Append visible children to the current listing.

            Args:
                directory: Directory currently being visited.
                current_depth: Directory depth relative to the requested path.
            """
            nonlocal truncated
            if truncated:
                return

            try:
                children = sorted_children(directory)
            except OSError as error:
                relative = directory.relative_to(workspace.root)
                entries.append(f"[ERROR: could not list {relative}: {error}]")
                return

            for child in children:
                if child.name in DEFAULT_IGNORED_NAMES:
                    continue
                try:
                    is_directory = child.is_dir() and not child.is_symlink()
                except OSError:
                    continue

                if len(entries) == max_entries:
                    truncated = True
                    return

                relative = child.relative_to(workspace.root).as_posix()
                entries.append(f"{relative}/" if is_directory else relative)
                if is_directory and current_depth < depth:
                    visit(child, current_depth + 1)

        visit(target, 0)

        if not entries:
            return "(directory is empty)"

        output = "\n".join(entries)
        if truncated:
            output += f"\n[Output truncated after {max_entries} entries.]"
        return output

    return list_files
