"""The examples are documentation, so they are checked like documentation.

Nothing here runs an example (that would need GCP): each file has to compile,
the names it imports from syros have to exist, the options it passes have to be
real fields, and the index has to list it. That is enough to catch a renamed
option or a dropped export before a reader does.
"""

import ast
import dataclasses
import importlib
from pathlib import Path

import pytest

from syros import AgentOptions

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SCRIPTS = sorted(EXAMPLES.glob("*.py"))
OPTION_FIELDS = {f.name for f in dataclasses.fields(AgentOptions)}


def tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def syros_imports(module: ast.Module) -> list[tuple[str, str]]:
    """Every (module, name) a file imports from the syros package."""
    return [
        (node.module, alias.name)
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "syros"
        for alias in node.names
    ]


def option_kwargs(module: ast.Module) -> list[str]:
    """Every keyword passed to an AgentOptions(...) call."""
    return [
        keyword.arg
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AgentOptions"
        for keyword in node.keywords
        if keyword.arg is not None
    ]


def test_examples_exist():
    assert SCRIPTS, "examples/ has no scripts"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_compiles(path):
    compile(path.read_text(), str(path), "exec")


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_documented(path):
    assert ast.get_docstring(tree(path)), f"{path.name} needs a module docstring"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_imported_names_exist(path):
    for module_name, name in syros_imports(tree(path)):
        module = importlib.import_module(module_name)
        assert hasattr(module, name), f"{path.name}: {module_name} has no {name!r}"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_option_fields_exist(path):
    for name in option_kwargs(tree(path)):
        assert name in OPTION_FIELDS, f"{path.name}: AgentOptions has no {name!r}"


def test_index_lists_every_example():
    index = (EXAMPLES / "README.md").read_text()
    missing = [path.name for path in SCRIPTS if path.name not in index]
    assert not missing, f"examples/README.md does not mention {missing}"
