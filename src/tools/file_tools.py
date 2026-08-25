from pathlib import Path
from tools.base import Tool


def read_file(
    path: str, start_line: int | None = None, end_line: int | None = None
) -> str:
    p = Path(path)
    if not p.exists():
        return f"ERROR: File {path} is not existed"
    lines = p.read_text(encoding="utf-8").splitlines()
    start = (start_line or 1) - 1
    end = end_line or len(lines)
    selected = lines[start:end]
    numbered = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(selected, start))
    return numbered
