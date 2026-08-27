"""Shared traversal policy for filesystem tools."""

from collections.abc import Iterator
from pathlib import Path

DEFAULT_IGNORED_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
    }
)


def sorted_children(directory: Path) -> tuple[Path, ...]:
    """Return directory children in deterministic name order.

    Args:
        directory: Directory whose immediate children should be collected.

    Returns:
        Children sorted case-insensitively by name.

    Raises:
        OSError: If the directory cannot be read.
    """
    return tuple(sorted(directory.iterdir(), key=lambda item: item.name.casefold()))


def iter_files(target: Path) -> Iterator[Path]:
    """Yield regular files without following symlinks.

    Args:
        target: File or directory from which traversal starts.

    Yields:
        Regular files in deterministic path order.

    Notes:
        Unreadable paths and names in ``DEFAULT_IGNORED_NAMES`` are skipped.
    """
    if target.is_file():
        if not target.is_symlink():
            yield target
        return

    try:
        children = sorted_children(target)
    except OSError:
        return

    for child in children:
        if child.name in DEFAULT_IGNORED_NAMES or child.is_symlink():
            continue
        try:
            if child.is_dir():
                yield from iter_files(child)
            elif child.is_file():
                yield child
        except OSError:
            continue
