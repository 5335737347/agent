import tempfile
import unittest

from pathlib import Path
from sandbox.workspace import Workspace, WorkspacePathError


class WorkspaceTests(unittest.TestCase):
    def setUp(
        self,
    ) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.root = Path(self.temp_dir.name)
        self.workspace = Workspace(self.root)

    def test_resolves_relative_path(self) -> None:
        result = self.workspace.resolve("src/main.py")

        self.assertEqual(result, (self.root / "src/main.py").resolve())

    def test_rejects_parent_escape(self) -> None:
        with self.assertRaises(WorkspacePathError):
            self.workspace.resolve("../secret.txt")

    def test_resolve_absolute_path(self) -> None:
        absolute_path = self.root / "inside.txt"

        with self.assertRaises(WorkspacePathError):
            self.workspace.resolve(str(absolute_path))


if __name__ == "__main__":
    ...
