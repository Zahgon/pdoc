"""
This module handles all interpretation of the *Abstract Syntax Tree (AST)* in pdoc.

Parsing the AST is done to extract docstrings, type annotations, and variable declarations from `__init__`.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
import inspect
from itertools import tee
from itertools import zip_longest
import types
from typing import TYPE_CHECKING
from typing import Any
from typing import TypeVar
from typing import overload
import warnings

import pdoc

from ._compat import ast_TypeAlias

if TYPE_CHECKING:
    import pdoc.doc_types


def get_source(obj: Any) -> str:
    """
    Returns the source code of the Python object `obj` as a str.

    If this fails, an empty string is returned.
    """
    # Some objects may not be hashable, so we fall back to the non-cached version if that is the case.
    try:
        return _get_source(obj)
    except TypeError:
        return _get_source.__wrapped__(obj)


@cache
def _get_source(obj: Any) -> str:
    try:
        return inspect.getsource(obj)
    except Exception:
        return ""


@overload
def parse(obj: types.ModuleType) -> ast.Module: ...


@overload
def parse(obj: types.FunctionType) -> ast.FunctionDef | ast.AsyncFunctionDef: ...


@overload
def parse(obj: type) -> ast.ClassDef: ...


def parse(obj):
    """
    Parse a module, class or function and return the (unwrapped) AST node.
    If an object's source code cannot be found, this function returns an empty ast node stub
    which can still be walked.
    """
    src = get_source(obj)
    if isinstance(obj, types.ModuleType):
        return _parse_module(src)
    elif isinstance(obj, type):
        return _parse_class(src)
    else:
        return _parse_function(src)


@cache
def unparse(tree: ast.AST):
    """`ast.unparse`, but cached."""
    pass


@dataclass
class AstInfo:
    """The information extracted from walking the syntax tree."""

    var_docstrings: dict[str, str]
    """A qualname -> docstring mapping."""
    func_docstrings: dict[str, str]
    """A qualname -> docstring mapping for functions."""
    annotations: dict[str, str | type[pdoc.doc_types.empty]]
    """A qualname -> annotation mapping.
    
    Annotations are not evaluated by this module and only returned as strings."""


def walk_tree(obj: types.ModuleType | type) -> AstInfo:
    """
    Walks the abstract syntax tree for `obj` and returns the extracted information.
    """
    pass


@cache
def _walk_tree(
    tree: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> AstInfo:
    pass


T = TypeVar("T")


def sort_by_source(
    obj: types.ModuleType | type, sorted: dict[str, T], unsorted: dict[str, T]
) -> tuple[dict[str, T], dict[str, T]]:
    """
    Takes items from `unsorted` and inserts them into `sorted` in order of appearance in the source code of `obj`.
    The only exception to this rule is `__init__`, which (if present) is always inserted first.

    Some items may not be found, for example because they've been inherited from a superclass. They are returned as-is.

    Returns a `(sorted, not found)` tuple.
    """
    pass


def type_checking_sections(mod: types.ModuleType) -> ast.Module:
    """
    Walks the abstract syntax tree for `mod` and returns all statements guarded by TYPE_CHECKING blocks.
    """
    ret = ast.Module(body=[], type_ignores=[])
    tree = _parse_module(get_source(mod))
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            ret.body.extend(node.body)
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Attribute)
            and isinstance(node.test.value, ast.Name)
            # some folks do "import typing as t", the accuracy with just TYPE_CHECKING is good enough.
            # and node.test.value.id == "typing"
            and node.test.attr == "TYPE_CHECKING"
        ):
            ret.body.extend(node.body)
    return ret


@cache
def _parse_module(source: str) -> ast.Module:
    """
    Parse the AST for the source code of a module and return the ast.Module.

    Returns an empty ast.Module if source is empty.
    """
    tree = _parse(source)
    assert isinstance(tree, ast.Module)
    return tree


@cache
def _parse_class(source: str) -> ast.ClassDef:
    """
    Parse the AST for the source code of a class and return the ast.ClassDef.

    Returns an empty ast.ClassDef if source is empty.
    """
    tree = _parse(source)
    if tree.body and len(tree.body) == 1:
        t = tree.body[0]
        if isinstance(t, ast.ClassDef):
            return t
    return ast.ClassDef(name="PdocStub", body=[], decorator_list=[])  # type: ignore


@cache
def _parse_function(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """
    Parse the AST for the source code of a (async) function and return the matching AST node.

    Returns an empty ast.FunctionDef if source is empty.
    """
    tree = _parse(source)
    if tree.body and len(tree.body) == 1:
        t = tree.body[0]
        if isinstance(t, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return t
        else:
            # we have a lambda function,
            # to simplify the API return the ast.FunctionDef stub.
            pass
    return ast.FunctionDef(
        name="pdoc_stub", args=ast.arguments(), body=[], decorator_list=[]
    )  # type: ignore


def _parse(
    source: str,
) -> ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef:
    try:
        return ast.parse(_dedent(source))
    except Exception as e:
        warnings.warn(f"Error parsing source code: {e}\n===\n{source}\n===")
        return ast.parse("")


@cache
def _dedent(source: str) -> str:
    """
    Dedent the head of a function or class definition so that it can be parsed by `ast.parse`.
    This is an alternative to `textwrap.dedent`, which does not dedent if there are docstrings
    without indentation. For example, this is valid Python code but would not be dedented with `textwrap.dedent`:

    class Foo:
        def bar(self):
           '''
    this is a docstring
           '''
    """
    if not source or source[0] not in (" ", "\t"):
        return source
    source = source.lstrip()
    # we may have decorators before our function definition, in which case we need to dedent a few more lines.
    # the following heuristic should be good enough to detect if we have reached the definition.
    # it's easy to produce examples where this fails, but this probably is not a problem in practice.
    if not any(source.startswith(x) for x in ["async ", "def ", "class "]):
        first_line, rest = source.split("\n", 1)
        return first_line + "\n" + _dedent(rest)
    else:
        return source


@cache
def _nodes(tree: ast.Module | ast.ClassDef) -> list[ast.AST]:
    """
    Returns the list of all nodes in tree's body, but also inlines the body of __init__.

    This is useful to detect all declared variables in a class, even if they only appear in the constructor.
    """
    pass


def _nodes_iter(tree: ast.Module | ast.ClassDef) -> Iterator[ast.AST]:
    pass


def _init_nodes(tree: ast.FunctionDef) -> Iterator[ast.AST]:
    """
    Transform attribute assignments like "self.foo = 42" to name assignments like "foo = 42",
    keep all constant expressions, and no-op everything else.
    This essentially allows us to inline __init__ when parsing a class definition.
    """
    pass


def _pairwise_longest(iterable: Iterable[T]) -> Iterable[tuple[T, T]]:
    """s -> (s0,s1), (s1,s2), (s2, s3),  ..., (sN, None)"""
    pass
