from dataclasses import dataclass
from pathlib import Path


class WorkspacePathError(ValueError):
    """Raised when a requested path escapes the workspace."""


@dataclass(frozen=True, slots=True)
class Workspace:
    """Represent the directory that coding tools may access."""

    root: Path

    def __post_init__(self) -> None:
        resolve_root = self.root.resolve()

        if not resolve_root.is_dir():
            raise ValueError(f"Workspace root is not a directory: {resolve_root}")

        object.__setattr__(self, "root", resolve_root)

    def resolve(self, path: str) -> Path:
        requested = Path(path)

        if requested.anchor:
            raise WorkspacePathError(f"Anchored paths are not allowed: {path}")

        candidate = (self.root / requested).resolve()

        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise WorkspacePathError(f"Path escapes workspace: {path}") from error

        return candidate
