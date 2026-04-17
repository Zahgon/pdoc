"""Work with Pydantic models."""

from __future__ import annotations

import types
from typing import Any
from typing import TypeAlias
from typing import TypeGuard

try:
    import pydantic
except ImportError:  # pragma: no cover
    pydantic = None  # type: ignore

ClassOrModule: TypeAlias = type | types.ModuleType
"""Type alias for the type of `Namespace.obj`."""

IGNORED_FIELDS: frozenset[str] = frozenset(
    (["__fields__"] + list(pydantic.BaseModel.__dict__.keys()))
    if pydantic is not None
    else []
)
"""Fields to ignore when generating docs, e.g. those that emit deprecation
warnings or that are not relevant to users of BaseModel-derived classes."""


def is_pydantic_model(obj: ClassOrModule) -> TypeGuard[pydantic.BaseModel]:
    """Returns whether an object is a Pydantic model."""
    pass


def default_value(parent: ClassOrModule, name: str, obj: Any) -> Any:
    """Determine the default value of obj.

    For pydantic BaseModels, extract the default value from field metadata.
    For all other objects, return `obj` as-is.
    """
    pass


def get_field_docstring(parent: ClassOrModule, field_name: str) -> str | None:
    pass
