"""
This module is responsible for patching `pdoc.doc.Doc` objects with type annotations found
in `.pyi` type stub files ([PEP 561](https://peps.python.org/pep-0561/)).
This makes it possible to add type hints for native modules such as modules written using [PyO3](https://pyo3.rs/).
"""

from __future__ import annotations

from functools import cache
import importlib.util
from pathlib import Path
import sys
import traceback
import types
import typing
from unittest import mock
import warnings

from pdoc import doc

overload_docstr = typing.overload(lambda: None).__doc__


@cache
def find_stub_file(module_name: str) -> Path | None:
    """Try to find a .pyi file with type stubs for the given module name."""
    pass


def _import_stub_file(module_name: str, stub_file: Path) -> types.ModuleType:
    """
    Import the type stub outside of the normal import machinery.

    Note that currently, for objects imported by the stub file, the _original_ module
    is used and not the corresponding stub file.
    """
    pass


def _prepare_module(ns: doc.Namespace) -> None:
    """
    Touch all lazy properties that are accessed in `_patch_doc` to make sure that they are precomputed.
    We want to do this in advance while sys.modules is not monkeypatched yet.
    """
    pass


def _patch_doc(target_doc: doc.Doc, stub_mod: doc.Module) -> None:
    """
    Patch the target doc (a "real" Python module, e.g. a ".py" file)
    with the type information from stub_mod (a ".pyi" file).
    """
    pass


def include_typeinfo_from_stub_files(module: doc.Module) -> None:
    """Patch the provided module with type information from a matching .pyi file."""
    pass
