"""Pure modules stay free of database, web and network code, so they can be tested directly.

Add a module here when it holds domain logic that should not touch I/O.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "tischlein"
PURE = ["planner.py", "paprika.py", "holidays.py", "prep.py", "shopping.py", "ideas.py", "models.py", "ics.py"]
FORBIDDEN = {"sqlite3", "urllib", "http", "socket", "fastapi", "starlette", "uvicorn", "sqlalchemy", "alembic",
             "requests", "httpx"}
FORBIDDEN_LOCAL = {"store", "app", "service", "views", "paprika_sync", "llm", "cli"}


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative: from .store import Store
                names.add("." + (node.module or ""))
            elif node.module:
                names.add(node.module)
    return names


@pytest.mark.parametrize("module", PURE)
def test_pure_module_has_no_io_imports(module):
    found = imports_of(SRC / module)
    external = {n for n in found if not n.startswith(".") and n.split(".")[0] in FORBIDDEN}
    local = {n for n in found if n.startswith(".") and n[1:].split(".")[0] in FORBIDDEN_LOCAL}
    assert not external | local, f"{module} importiert I/O-Code: {sorted(external | local)}"
