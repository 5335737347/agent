"""Workspace-scoped text search tool."""

import fnmatch

from sandbox import Workspace, WorkspacePathError
from tools.base import Tool, ToolResult, tool

from ._walk import iter_files


def make_search_text_tool(
    workspace: Workspace,
    *,
    max_files: int = 2_000,
    max_matches: int = 200,
    max_line_length: int = 500,
) -> Tool:
    """Create a bounded literal-text search tool for a workspace.

    Args:
        workspace: Workspace that constrains accessible paths.
        max_files: Maximum number of files visited by one call.
        max_matches: Maximum number of matching lines returned by one call.
        max_line_length: Maximum number of characters retained per matching line.

    Returns:
        A configured ``search_text`` tool.

    Raises:
        ValueError: If any configured limit is less than one.
    """
    if max_files < 1:
        raise ValueError("max_files must be at least 1")
    if max_matches < 1:
        raise ValueError("max_matches must be at least 1")
    if max_line_length < 1:
        raise ValueError("max_line_length must be at least 1")

    @tool
    def search_text(
        query: str, path: str = ".", glob: str = "*"
    ) -> str | ToolResult:
        """Search workspace files for case-sensitive literal text.

        Args:
            query: Non-empty literal text to find.
            path: Workspace-relative file or directory to search.
            glob: File pattern, such as ``*.py`` or ``src/*.py``.

        Returns:
            Matches formatted as ``path:line: text``.
        """
        if not query:
            return ToolResult.error("ERROR: query must not be empty")
        if not glob:
            return ToolResult.error("ERROR: glob must not be empty")

        try:
            target = workspace.resolve(path)
        except WorkspacePathError as error:
            return ToolResult.error(f"ERROR: {error}")

        if not target.exists():
            return ToolResult.error(f"ERROR: path does not exist: {path}")
        if not target.is_file() and not target.is_dir():
            return ToolResult.error(
                f"ERROR: path is not a regular file or directory: {path}"
            )

        matches: list[str] = []
        matched_files = 0
        visited_files = 0
        file_limit_reached = False

        for candidate in iter_files(target):
            if visited_files == max_files:
                file_limit_reached = True
                break
            visited_files += 1

            relative = candidate.relative_to(workspace.root).as_posix()
            if not (
                fnmatch.fnmatchcase(relative, glob)
                or fnmatch.fnmatchcase(candidate.name, glob)
            ):
                continue

            try:
                file_matched = False
                with candidate.open(encoding="utf-8") as source:
                    for line_number, line in enumerate(source, start=1):
                        if query not in line:
                            continue
                        if not file_matched:
                            matched_files += 1
                            file_matched = True
                        text = line.rstrip("\r\n")
                        if len(text) > max_line_length:
                            text = f"{text[:max_line_length]}…"
                        matches.append(f"{relative}:{line_number}: {text}")
                        if len(matches) == max_matches:
                            return (
                                "\n".join(matches)
                                + f"\n[Output truncated after {max_matches} matches.]"
                            )
            except (OSError, UnicodeDecodeError):
                continue

        output = (
            "\n".join(matches)
            if matches
            else f"No matches found in {visited_files} files."
        )
        if file_limit_reached:
            output += f"\n[File scan truncated after {max_files} files.]"
        elif matches:
            output += f"\n[Found {len(matches)} matches in {matched_files} files.]"
        return output

    return search_text
