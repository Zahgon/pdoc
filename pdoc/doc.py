"""
This module defines pdoc's documentation objects. A documentation object corresponds to *something*
in your Python code that has a docstring or type annotation. Typically, this only includes
modules, classes, functions and methods. However, `pdoc` adds support for extracting documentation
from the abstract syntax tree, which means that variables (module, class or instance) are supported too.

There are four main types of documentation objects:

- `Module`
- `Class`
- `Function`
- `Variable`

All documentation types make heavy use of `@functools.cached_property` decorators.
This means they have a large set of attributes that are lazily computed on first access.
By convention, all attributes are read-only, although this is not enforced at runtime.
"""

from __future__ import annotations

from abc import ABCMeta
from abc import abstractmethod
from collections.abc import Callable
import dataclasses
import enum
from functools import cache
from functools import cached_property
from functools import singledispatchmethod
from functools import wraps
import inspect
import os
from pathlib import Path
import re
import sys
import textwrap
import traceback
import types
from typing import Any
from typing import ClassVar
from typing import Generic
from typing import TypeAlias
from typing import TypedDict
from typing import TypeVar
from typing import cast
from typing import get_origin
from typing import is_typeddict
import warnings

from pdoc import _pydantic
from pdoc import doc_ast
from pdoc import doc_pyi
from pdoc import extract
from pdoc._compat import TypeAliasType
from pdoc._compat import formatannotation
from pdoc.doc_types import GenericAlias
from pdoc.doc_types import NonUserDefinedCallables
from pdoc.doc_types import empty
from pdoc.doc_types import resolve_annotations
from pdoc.doc_types import safe_eval_type


def _include_fullname_in_traceback(f):
    """
    Doc.__repr__ should not raise, but it may raise if we screwed up.
    Debugging this is a bit tricky, because, well, we can't repr() in the traceback either then.
    This decorator adds location information to the traceback, which helps tracking down bugs.
    """

    @wraps(f)
    def wrapper(self):
        try:
            return f(self)
        except Exception as e:
            raise RuntimeError(f"Error in {self.fullname}'s repr!") from e

    return wrapper


T = TypeVar("T")


class Doc(Generic[T]):
    """
    A base class for all documentation objects.
    """

    modulename: str
    """
    The module that this object is in, for example `pdoc.doc`.
    """

    qualname: str
    """
    The qualified identifier name for this object. For example, if we have the following code:
    
    ```python
    class Foo:
        def bar(self):
            pass
    ```
    
    The qualname of `Foo`'s `bar` method is `Foo.bar`. The qualname of the `Foo` class is just `Foo`.
    
    See <https://www.python.org/dev/peps/pep-3155/> for details.
    """

    obj: T
    """
    The underlying Python object.
    """

    taken_from: tuple[str, str]
    """
    `(modulename, qualname)` of this doc object's original location.
    In the context of a module, this points to the location it was imported from,
    in the context of classes, this points to the class an attribute is inherited from.
    """

    kind: ClassVar[str]
    """
    The type of the doc object, either `"module"`, `"class"`, `"function"`, or `"variable"`.
    """

    @property
    def type(self) -> str:  # pragma: no cover
        pass

    def __init__(
        self, modulename: str, qualname: str, obj: T, taken_from: tuple[str, str]
    ):
        """
        Initializes a documentation object, where
        `modulename` is the name this module is defined in,
        `qualname` contains a dotted path leading to the object from the module top-level, and
        `obj` is the object to document.
        """
        self.modulename = modulename
        self.qualname = qualname
        self.obj = obj
        self.taken_from = taken_from

    @cached_property
    def fullname(self) -> str:
        """The full qualified name of this doc object, for example `pdoc.doc.Doc`."""
        pass

    @cached_property
    def name(self) -> str:
        """The name of this object. For top-level functions and classes, this is equal to the qualname attribute."""
        pass

    @cached_property
    def docstring(self) -> str:
        """
        The docstring for this object. It has already been cleaned by `inspect.cleandoc`.

        If no docstring can be found, an empty string is returned.
        """
        pass

    @cached_property
    def source(self) -> str:
        """
        The source code of the Python object as a `str`.

        If the source cannot be obtained (for example, because we are dealing with a native C object),
        an empty string is returned.
        """
        pass

    @cached_property
    def source_file(self) -> Path | None:
        """The name of the Python source file in which this object was defined. `None` for built-in objects."""
        pass

    @cached_property
    def source_lines(self) -> tuple[int, int] | None:
        """
        Return a `(start, end)` line number tuple for this object.

        If no source file can be found, `None` is returned.
        """
        pass

    @cached_property
    def is_inherited(self) -> bool:
        """
        If True, the doc object is inherited from another location.
        This most commonly refers to methods inherited by a subclass,
        but can also apply to variables that are assigned a class defined
        in a different module.
        """
        pass

    def __lt__(self, other):
        assert isinstance(other, Doc)
        return self.fullname.replace("__init__", "").__lt__(
            other.fullname.replace("__init__", "")
        )


U = TypeVar("U", bound=types.ModuleType | type)


class Namespace(Doc[U], metaclass=ABCMeta):
    """
    A documentation object that can have children. In other words, either a module or a class.
    """

    @cached_property
    @abstractmethod
    def _member_objects(self) -> dict[str, Any]:
        """
        A mapping from *all* public and private member names to their Python objects.
        """

    @cached_property
    @abstractmethod
    def _var_docstrings(self) -> dict[str, str]:
        """A mapping from some member variable names to their docstrings."""

    @cached_property
    @abstractmethod
    def _func_docstrings(self) -> dict[str, str]:
        """A mapping from some member function names to their raw (not processed by any @decorators) docstrings."""

    @cached_property
    @abstractmethod
    def _var_annotations(self) -> dict[str, Any]:
        """A mapping from some member variable names to their type annotations."""

    @abstractmethod
    def _taken_from(self, member_name: str, obj: Any) -> tuple[str, str]:
        """The location this member was taken from. If unknown, (modulename, qualname) is returned."""

    @cached_property
    @abstractmethod
    def own_members(self) -> list[Doc]:
        """A list of all own (i.e. non-inherited) `members`."""

    @cached_property
    def members(self) -> dict[str, Doc]:
        """A mapping from all members to their documentation objects.

        This mapping includes private members; they are only filtered out as part of the template logic.
        Constructors for enums, dicts, and abstract base classes are not picked up unless they have a custom docstring.
        """
        pass

    @cached_property
    def _members_by_origin(self) -> dict[tuple[str, str], list[Doc]]:
        """A mapping from (modulename, qualname) locations to the attributes taken from that path"""
        pass

    @cached_property
    def inherited_members(self) -> dict[tuple[str, str], list[Doc]]:
        """A mapping from (modulename, qualname) locations to the attributes inherited from that path"""
        pass

    @cached_property
    def flattened_own_members(self) -> list[Doc]:
        """
        A list of all documented members and their child classes, recursively.
        """
        pass

    @cache
    def get(self, identifier: str) -> Doc | None:
        """Returns the documentation object for a particular identifier, or `None` if the identifier cannot be found."""
        head, _, tail = identifier.partition(".")
        if tail:
            h = self.members.get(head, None)
            if isinstance(h, Class):
                return h.get(tail)
            return None
        else:
            return self.members.get(identifier, None)


class Module(Namespace[types.ModuleType]):
    """
    Representation of a module's documentation.
    """

    def __init__(
        self,
        module: types.ModuleType,
    ):
        """
        Creates a documentation object given the actual
        Python module object.
        """
        super().__init__(module.__name__, "", module, (module.__name__, ""))

    kind = "module"

    @classmethod
    @cache
    def from_name(cls, name: str) -> Module:
        """Create a `Module` object by supplying the module's (full) name."""
        return cls(extract.load_module(name))

    @cache
    @_include_fullname_in_traceback
    def __repr__(self):
        return f"<module {self.fullname}{_docstr(self)}{_children(self)}>"

    @cached_property
    def is_package(self) -> bool:
        """
        `True` if the module is a package, `False` otherwise.

        Packages are a special kind of module that may have submodules.
        Typically, this means that this file is in a directory named like the
        module with the name `__init__.py`.
        """
        pass

    @cached_property
    def _var_docstrings(self) -> dict[str, str]:
        pass

    @cached_property
    def _func_docstrings(self) -> dict[str, str]:
        pass

    @cached_property
    def _var_annotations(self) -> dict[str, Any]:
        pass

    def _taken_from(self, member_name: str, obj: Any) -> tuple[str, str]:
        pass

    @cached_property
    def own_members(self) -> list[Doc]:
        pass

    @cached_property
    def submodules(self) -> list[Module]:
        """A list of all (direct) submodules."""
        pass

    @cached_property
    def _ast_keys(self) -> set[str]:
        pass

    @cached_property
    def _member_objects(self) -> dict[str, Any]:
        pass

    @cached_property
    def variables(self) -> list[Variable]:
        """
        A list of all documented module level variables.
        """
        pass

    @cached_property
    def classes(self) -> list[Class]:
        """
        A list of all documented module level classes.
        """
        pass

    @cached_property
    def functions(self) -> list[Function]:
        """
        A list of all documented module level functions.
        """
        pass


class Class(Namespace[type]):
    """
    Representation of a class's documentation.
    """

    kind = "class"

    @cache
    @_include_fullname_in_traceback
    def __repr__(self):
        return f"<{_decorators(self)}class {self.modulename}.{self.qualname}{_docstr(self)}{_children(self)}>"

    @cached_property
    def docstring(self) -> str:
        pass

    @cached_property
    def _var_docstrings(self) -> dict[str, str]:
        pass

    @cached_property
    def _func_docstrings(self) -> dict[str, str]:
        pass

    @cached_property
    def _var_annotations(self) -> dict[str, type]:
        # this is a bit tricky: __annotations__ also includes annotations from parent classes,
        # but we need to execute them in the namespace of the parent class.
        # Our workaround for this is to walk the MRO backwards, and only update/evaluate only if the annotation changes.
        pass

    @cached_property
    def _bases(self) -> tuple[type, ...]:
        pass

    @cached_property
    def _declarations(self) -> dict[str, tuple[str, str]]:
        pass

    def _taken_from(self, member_name: str, obj: Any) -> tuple[str, str]:
        pass

    @cached_property
    def own_members(self) -> list[Doc]:
        pass

    @cached_property
    def _member_objects(self) -> dict[str, Any]:
        pass

    @cached_property
    def bases(self) -> list[tuple[str, str, str]]:
        """
        A list of all base classes, i.e. all immediate parent classes.

        Each parent class is represented as a `(modulename, qualname, display_text)` tuple.
        """
        pass

    @cached_property
    def decorators(self) -> list[str]:
        """A list of all decorators the class is decorated with."""
        pass

    @cached_property
    def class_variables(self) -> list[Variable]:
        """
        A list of all documented class variables in the class.

        Class variables are variables that are explicitly annotated with `typing.ClassVar`.
        All other variables are treated as instance variables.
        """
        pass

    @cached_property
    def instance_variables(self) -> list[Variable]:
        """
        A list of all instance variables in the class.
        """
        pass

    @cached_property
    def classmethods(self) -> list[Function]:
        """
        A list of all documented `@classmethod`s.
        """
        pass

    @cached_property
    def staticmethods(self) -> list[Function]:
        """
        A list of all documented `@staticmethod`s.
        """
        pass

    @cached_property
    def methods(self) -> list[Function]:
        """
        A list of all documented methods in the class that are neither static- nor classmethods.
        """
        pass


WrappedFunction = types.FunctionType | staticmethod | classmethod


class Function(Doc[types.FunctionType]):
    """
    Representation of a function's documentation.

    This class covers all "flavors" of functions, for example it also
    supports `@classmethod`s or `@staticmethod`s.
    """

    kind = "function"

    wrapped: WrappedFunction
    """The original wrapped function (e.g., `staticmethod(func)`)"""

    obj: types.FunctionType
    """The unwrapped "real" function."""

    def __init__(
        self,
        modulename: str,
        qualname: str,
        func: WrappedFunction,
        taken_from: tuple[str, str],
    ):
        """Initialize a function's documentation object."""
        unwrapped: types.FunctionType
        if isinstance(func, (classmethod, staticmethod)):
            unwrapped = func.__func__  # type: ignore
        elif isinstance(func, singledispatchmethod):
            unwrapped = func.func  # type: ignore
        elif hasattr(func, "__wrapped__"):
            unwrapped = func.__wrapped__
        else:
            unwrapped = func
        super().__init__(modulename, qualname, unwrapped, taken_from)
        self.wrapped = func  # type: ignore

    @cache
    @_include_fullname_in_traceback
    def __repr__(self):
        if self.is_classmethod:
            t = "class"
        elif self.is_staticmethod:
            t = "static"
        elif self.qualname != _safe_getattr(self.obj, "__name__", None):
            t = "method"
        else:
            t = "function"
        return f"<{_decorators(self)}{t} {self.funcdef} {self.name}{self.signature}: ...{_docstr(self)}>"

    @cached_property
    def docstring(self) -> str:
        pass

    @cached_property
    def is_classmethod(self) -> bool:
        """
        `True` if this function is a `@classmethod`, `False` otherwise.
        """
        pass

    @cached_property
    def is_staticmethod(self) -> bool:
        """
        `True` if this function is a `@staticmethod`, `False` otherwise.
        """
        pass

    @cached_property
    def decorators(self) -> list[str]:
        """A list of all decorators the function is decorated with."""
        pass

    @cached_property
    def funcdef(self) -> str:
        """
        The string of keywords used to define the function, i.e. `"def"` or `"async def"`.
        """
        pass

    @cached_property
    def signature(self) -> inspect.Signature:
        """
        The function's signature.

        This usually returns an instance of `_PrettySignature`, a subclass of `inspect.Signature`
        that contains pdoc-specific optimizations. For example, long argument lists are split over multiple lines
        in repr(). Additionally, all types are already resolved.

        If the signature cannot be determined, a placeholder Signature object is returned.
        """
        pass

    @cached_property
    def signature_without_self(self) -> inspect.Signature:
        """Like `signature`, but without the first argument.

        This is useful to display constructors.
        """
        pass


class Variable(Doc[None]):
    """
    Representation of a variable's documentation. This includes module, class and instance variables.
    """

    kind = "variable"

    default_value: (
        Any | empty
    )  # technically Any includes empty, but this conveys intent.
    """
    The variable's default value.
    
    In some cases, no default value is known. This may either be because a variable is only defined in the constructor,
    or it is only declared with a type annotation without assignment (`foo: int`).
    To distinguish this case from a default value of `None`, `pdoc.doc_types.empty` is used as a placeholder.
    """

    annotation: type | empty
    """
    The variable's type annotation.
    
    If there is no type annotation, `pdoc.doc_types.empty` is used as a placeholder.
    """

    def __init__(
        self,
        modulename: str,
        qualname: str,
        *,
        taken_from: tuple[str, str],
        docstring: str,
        annotation: type | empty = empty,
        default_value: Any | empty = empty,
    ):
        """
        Construct a variable doc object.

        While classes and functions can introspect themselves to see their docstring,
        variables can't do that as we don't have a "variable object" we could query.
        As such, docstring, declaration location, type annotation, and the default value
        must be passed manually in the constructor.
        """
        super().__init__(modulename, qualname, None, taken_from)
        # noinspection PyPropertyAccess
        self.docstring = inspect.cleandoc(docstring)
        self.annotation = annotation
        self.default_value = default_value

    @cache
    @_include_fullname_in_traceback
    def __repr__(self):
        if self.default_value_str:
            default = f" = {self.default_value_str}"
        else:
            default = ""
        return f"<var {self.qualname.rsplit('.')[-1]}{self.annotation_str}{default}{_docstr(self)}>"

    @cached_property
    def is_classvar(self) -> bool:
        """`True` if the variable is a class variable, `False` otherwise."""
        pass

    @cached_property
    def is_typevar(self) -> bool:
        """`True` if the variable is a `typing.TypeVar`, `False` otherwise."""
        pass

    @cached_property
    def is_type_alias_type(self) -> bool:
        """`True` if the variable is a `typing.TypeAliasType`, `False` otherwise."""
        pass

    @cached_property
    def is_enum_member(self) -> bool:
        """`True` if the variable is an enum member, `False` otherwise."""
        pass

    @cached_property
    def default_value_str(self) -> str:
        """The variable's default value as a pretty-printed str."""
        pass

    @cached_property
    def annotation_str(self) -> str:
        """The variable's type annotation as a pretty-printed str."""
        pass


@cache
def _environ_lookup():
    """
    A reverse lookup of os.environ. This is a cached function so that it is evaluated lazily.
    """
    return {value: key for key, value in os.environ.items()}


class _PrettySignature(inspect.Signature):
    """
    A subclass of `inspect.Signature` that pads __str__ over several lines
    for complex signatures.
    """

    MULTILINE_CUTOFF = 70

    def _params(self) -> list[str]:
        # redeclared here to keep code snipped below as-is.
        _POSITIONAL_ONLY = inspect.Parameter.POSITIONAL_ONLY
        _VAR_POSITIONAL = inspect.Parameter.VAR_POSITIONAL
        _KEYWORD_ONLY = inspect.Parameter.KEYWORD_ONLY

        # https://github.com/python/cpython/blob/799f8489d418b7f9207d333eac38214931bd7dcc/Lib/inspect.py#L3083-L3117
        # Change: added re.sub() to formatted = ....
        # ✂ start ✂
        result = []
        render_pos_only_separator = False
        render_kw_only_separator = True
        for param in self.parameters.values():
            formatted = str(param)
            formatted = _remove_memory_addresses(formatted)
            formatted = _remove_collections_abc(formatted)

            kind = param.kind

            if kind == _POSITIONAL_ONLY:
                render_pos_only_separator = True
            elif render_pos_only_separator:
                # It's not a positional-only parameter, and the flag
                # is set to 'True' (there were pos-only params before.)
                result.append("/")
                render_pos_only_separator = False

            if kind == _VAR_POSITIONAL:
                # OK, we have an '*args'-like parameter, so we won't need
                # a '*' to separate keyword-only arguments
                render_kw_only_separator = False
            elif kind == _KEYWORD_ONLY and render_kw_only_separator:
                # We have a keyword-only parameter to render and we haven't
                # rendered an '*args'-like parameter before, so add a '*'
                # separator to the parameters list ("foo(arg1, *, arg2)" case)
                result.append("*")
                # This condition should be only triggered once, so
                # reset the flag
                render_kw_only_separator = False

            result.append(formatted)

        if render_pos_only_separator:
            # There were only positional-only parameters, hence the
            # flag was not reset to 'False'
            result.append("/")
        # ✂ end ✂

        return result

    def _return_annotation_str(self) -> str:
        if self.return_annotation is not empty:
            formatted = formatannotation(self.return_annotation)
            return _remove_collections_abc(formatted)
        else:
            return ""

    def __str__(self):
        result = self._params()
        return_annot = self._return_annotation_str()

        total_len = sum(len(x) + 2 for x in result) + len(return_annot)

        if total_len > self.MULTILINE_CUTOFF:
            rendered = "(\n    " + ",\n    ".join(result) + "\n)"
        else:
            rendered = "({})".format(", ".join(result))
        if return_annot:
            rendered += f" -> {return_annot}"

        return rendered


def _cut(x: str) -> str:
    """helper function for Doc.__repr__()"""
    pass


def _docstr(doc: Doc) -> str:
    """helper function for Doc.__repr__()"""
    pass


def _decorators(doc: Class | Function) -> str:
    """helper function for Doc.__repr__()"""
    pass


def _children(doc: Namespace) -> str:
    pass


def _safe_getattr(obj, attr, default):
    """Like `getattr()`, but never raises."""
    try:
        return getattr(obj, attr, default)
    except Exception as e:
        warnings.warn(
            f"getattr({obj!r}, {attr!r}, {default!r}) raised an exception: {e!r}"
        )
        return default


def _safe_getdoc(obj: Any) -> str:
    """Like `inspect.getdoc()`, but never raises. Always returns a stripped string."""
    try:
        doc = inspect.getdoc(obj) or ""
    except Exception as e:
        warnings.warn(f"inspect.getdoc({obj!r}) raised an exception: {e!r}")
        return ""
    else:
        return doc.strip()


_Enum_default_docstrings = tuple(
    {
        _safe_getdoc(enum.Enum),
        _safe_getdoc(enum.IntEnum),
        _safe_getdoc(_safe_getattr(enum, "StrEnum", enum.Enum)),
    }
)


def _remove_memory_addresses(x: str) -> str:
    """Remove memory addresses from repr() output"""
    return re.sub(r" at 0x[0-9a-fA-F]+(?=>)", "", x)


def _remove_collections_abc(x: str) -> str:
    """Remove 'collections.abc' from type signatures."""
    return re.sub(r"(?!\.)\bcollections\.abc\.", "", x)
