"""
This module handles the conversion of docstring flavors to Markdown.

The conversion from docstring flavors to Markdown is mostly done with regular expressions.
This is not particularly beautiful, but good enough for our purposes.
The alternative would be to depend on <https://github.com/rr-/docstring_parser> or a similar project,
but that introduces more complexity than we are comfortable with.

If you miss a particular feature for your favorite flavor, contributions are welcome.
That being said, please keep the complexity low and make sure that changes are
accompanied by matching snapshot tests in `test/testdata/`.
"""

from __future__ import annotations

import base64
from functools import cache
import inspect
import mimetypes
import os
from pathlib import Path
import re
from textwrap import dedent
from textwrap import indent
import warnings

AnyException = (SystemExit, GeneratorExit, Exception)
"""BaseException, but excluding KeyboardInterrupt.

Modules may raise SystemExit on import (which we want to catch),
but we don't want to catch a user's KeyboardInterrupt.
"""


@cache
def convert(docstring: str, docformat: str, source_file: Path | None) -> str:
    """
    Convert `docstring` from `docformat` to Markdown.
    """
    docformat = docformat.lower()

    try:
        if any(x in docformat for x in ["google", "numpy", "restructuredtext"]):
            docstring = rst(docstring, source_file)

        if "google" in docformat:
            docstring = google(docstring)

        if "numpy" in docformat:
            docstring = numpy(docstring)

        if source_file is not None and os.environ.get("PDOC_EMBED_IMAGES") != "0":
            docstring = embed_images(docstring, source_file)

    except AnyException as e:
        raise RuntimeError(
            'Docstring processing failed for docstring=\n"""\n'
            + docstring
            + f'\n"""\n{source_file=}\n{docformat=}'
        ) from e

    return docstring


def embed_images(docstring: str, source_file: Path) -> str:
    def local_image_to_data_uri(href: str) -> str:
        image_path = source_file.parent / href
        image_data = image_path.read_bytes()
        image_mime = mimetypes.guess_type(image_path)[0]
        image_data_b64 = base64.b64encode(image_data).decode()
        return f"data:{image_mime};base64,{image_data_b64}"

    def embed_local_image(m: re.Match) -> str:
        pass

    # TODO: Could probably do more here, e.g. support rST replacements.
    for regex in [
        r"(?P<before>!\[\s*.*?\s*]\(\s*)(?P<href>.+?)(?P<after>\s*\))",
        r"""(?P<before>src=['"])(?P<href>.+?)(?P<after>['"])""",
    ]:
        docstring = re.sub(regex, embed_local_image, docstring)
    return docstring


def google(docstring: str) -> str:
    """Convert Google-style docstring sections into Markdown."""
    return re.sub(
        r"""
        ^(?P<name>[A-Z][A-Z a-z]+):\n
        (?P<contents>(
            \n        # empty lines
            |         # or
            [ \t]+.+  # lines with indentation
        )+)$
        """,
        _google_section,
        docstring,
        flags=re.VERBOSE | re.MULTILINE,
    )


GOOGLE_LIST_SECTIONS = ["Args", "Raises", "Attributes", "Keyword Args"]
"""Section headers listed in the official Google docstring style guide."""

GOOGLE_LIST_SECTION_ALIASES = {
    "Parameters": "Args",
    "Params": "Args",
    "Arguments": "Args",
    "Raise": "Raises",
    "Keyword Arguments": "Keyword Args",
}
"""
Alternative section headers that are not listed in the official Google
docstring style guide but that we recognize as sections containing lists
nevertheless.
"""


def _google_section(m: re.Match[str]) -> str:
    pass


def _indented_list(contents: str) -> list[str]:
    """
    Convert a list string into individual (dedented) elements. For example,

    foo:
        desc
    bar: int
        more desc
    baz:
        desc
            indented

    returns [
        "foo:\ndesc",
        "bar: int\nmore desc",
        "baz:\ndesc\n    indented",
    ]
    """
    # we expect this to be through cleandoc() already.
    assert not contents.startswith(" "), contents
    assert not contents.startswith("\n"), contents

    ret: list[str] = []
    for line in contents.splitlines(keepends=True):
        empty = not line.strip()
        indented = line.startswith(" ")
        if not (empty or indented):
            # new section
            ret.append(line)
        else:
            # append to current section
            ret[-1] += line

    return [inspect.cleandoc(x) for x in ret]


def numpy(docstring: str) -> str:
    """Convert NumPy-style docstring sections into Markdown.

    See <https://numpydoc.readthedocs.io/en/latest/format.html> for details.
    """
    sections = re.split(
        r"""
        ^([A-Z][A-Za-z ]+)\n  # a heading
        ---+\n+              # followed by a dashed line
        """,
        docstring,
        flags=re.VERBOSE | re.MULTILINE,
    )
    contents = sections[0]
    for heading, content in zip(sections[1::2], sections[2::2]):
        if content.startswith(" ") and re.search(r"\n(?![ \n])", content):
            # If the first line of section content is indented, we consider the section to be finished
            # on the first non-indented line. We take out the rest - the tail - here.
            content, tail = re.split(r"\n(?![ \n])", content, maxsplit=1)
        else:
            tail = ""

        content = dedent(content)

        if heading in (
            "Parameters",
            "Returns",
            "Yields",
            "Receives",
            "Other Parameters",
            "Raises",
            "Warns",
            "Attributes",
        ):
            contents += f"###### {heading}\n{_numpy_parameters(content)}"
        elif heading == "See Also":
            contents += f"###### {heading}\n{_numpy_seealso(content)}"
        else:
            contents += f"###### {heading}\n{content}"
        contents += tail
    return contents


def _numpy_seealso(content: str) -> str:
    """Convert a NumPy-style "See Also" section into Markdown"""
    contents = ""
    for item in _indented_list(content):
        if ":" in item:
            funcstr, desc = item.split(":", maxsplit=1)
            desc = f": {desc}"
        else:
            funcstr, desc = item, ""

        funclist = [f.strip() for f in funcstr.split(" ")]
        funcs = ", ".join(f"`{f}`" for f in funclist if f)
        contents += f"{funcs}{desc}  \n"
    return contents


def _numpy_parameters(content: str) -> str:
    """Convert a NumPy-style parameter section into Markdown"""
    contents = ""
    for item in _indented_list(content):
        m = re.match(r"^(.+):(.+)([\s\S]*)", item)
        if m:
            contents += (
                f" - **{m.group(1).strip()}** ({m.group(2).strip()}):\n"
                f"{indent(m.group(3).strip(), '   ')}\n"
            )
        else:
            if "\n" in item:
                name, desc = item.split("\n", maxsplit=1)
                name = name.strip()
                desc = desc.strip()
            else:
                name, desc = item.strip(), ""

            if desc:
                contents += f" - **{name}**: {desc}\n"
            else:
                contents += f" - **{name}**\n"
    return f"{contents}\n"


def rst(contents: str, source_file: Path | None) -> str:
    """
    Convert reStructuredText elements to Markdown.
    We support the most common elements, but we do not aim to mirror the full complexity of the spec here.
    """
    contents = _rst_admonitions(contents, source_file)
    contents = _rst_links(contents)

    def replace_reference(m):
        pass

    # Code References: :obj:`foo` -> `foo`
    contents = re.sub(
        r"(:py)?:(mod|func|data|const|class|meth|attr|exc|obj):`([^`]+)`",
        replace_reference,
        contents,
    )

    # Math: :math:`foo` -> \\( foo \\)
    # We don't use $ as that's not enabled by MathJax by default.
    contents = re.sub(r":math:`(.+?)`", r"\\\\( \1 \\\\)", contents)

    contents = _rst_footnotes(contents)

    contents = _rst_fields(contents)

    return contents


def _rst_footnotes(contents: str) -> str:
    """Convert reStructuredText footnotes"""
    footnotes: set[str] = set()
    autonum: int

    def register_footnote(m: re.Match[str]) -> str:
        pass

    # Register footnotes
    autonum = 1
    contents = re.sub(
        r"""
            ^(?P<indent>[ ]*)\.\.[ ]+\[(?P<id>\d+|[#*]\w*)](?P<content>.*
            (
                \n                 # empty lines
                |                  # or
                (?P=indent)[ ]+.+  # lines with indentation
            )*)$
            """,
        register_footnote,
        contents,
        flags=re.MULTILINE | re.VERBOSE,
    )

    def replace_references(m: re.Match[str]) -> str:
        pass

    autonum = 1
    contents = re.sub(r"\[(?P<id>\d+|[#*]\w*)]_", replace_references, contents)
    return contents


def _rst_links(contents: str) -> str:
    """Convert reStructuredText hyperlinks"""
    links = {}

    def register_link(m: re.Match[str]) -> str:
        pass

    def replace_link(m: re.Match[str]) -> str:
        pass

    # Embedded URIs
    contents = re.sub(
        r"`(?P<text>[^`]+)<(?P<url>.+?)>`_", r"[\g<text>](\g<url>)", contents
    )
    # External Hyperlink Targets
    contents = re.sub(
        r"^\s*..\s+_(?P<id>[^\n:]+):\s*(?P<url>http\S+)",
        register_link,
        contents,
        flags=re.MULTILINE,
    )
    contents = re.sub(r"(?P<id>[A-Za-z0-9_\-.:+]|`[^`]+`)_", replace_link, contents)
    return contents


def _rst_extract_options(contents: str) -> tuple[str, dict[str, str]]:
    """
    Extract options from the beginning of reStructuredText directives.

    Return the trimmed content and a dict of options.
    """
    options = {}
    while match := re.match(r"^\s*:(.+?):(.*)([\s\S]*)", contents):
        key, value, contents = match.groups()
        options[key] = value.strip()

    return contents, options


def _rst_include_trim(contents: str, options: dict[str, str]) -> str:
    """
    <https://docutils.sourceforge.io/docs/ref/rst/directives.html#include-options>
    """
    if "end-line" in options or "start-line" in options:
        lines = contents.splitlines()
        if i := options.get("end-line"):
            lines = lines[: int(i)]
        if i := options.get("start-line"):
            lines = lines[int(i) :]
        contents = "\n".join(lines)
    if x := options.get("start-after"):
        contents = contents[contents.index(x) + len(x) :]
    if x := options.get("end-before"):
        contents = contents[: contents.index(x)]
    return contents


def _rst_admonitions(contents: str, source_file: Path | None) -> str:
    """
    Convert reStructuredText admonitions - a bit tricky because they may already be indented themselves.
    <https://www.sphinx-doc.org/en/master/usage/restructuredtext/directives.html>
    """

    def _rst_admonition(m: re.Match[str]) -> str:
        pass

    admonition = "note|warning|danger|versionadded|versionchanged|deprecated|seealso|math|include|code-block"
    return re.sub(
        rf"""
            ^(?P<indent>[ ]*)\.\.[ ]+(?P<type>{admonition})::(?P<val>.*)
            (?P<contents>(
                \n                 # empty lines
                |                  # or
                (?P=indent)[ ]+.+  # lines with indentation
            )*)$
        """,
        _rst_admonition,
        contents,
        flags=re.MULTILINE | re.VERBOSE,
    )


def _rst_fields(contents: str) -> str:
    """
    Convert reStructuredText fields to Markdown.
    <https://www.sphinx-doc.org/en/master/usage/restructuredtext/basics.html#rst-field-lists>
    """

    _has_parameter_section = False
    _has_raises_section = False

    def _rst_field(m: re.Match[str]) -> str:
        pass

    field = "param|type|return|rtype|raises"
    return re.sub(
        rf"""
            ^:(?P<type>{field})(?:[ ]+(?P<name>.+))?:
            (?P<body>.*(
                (?:\n[ ]*)*  # maybe some empty lines followed by
                [ ]+.+       # lines with indentation
            )*(?:\n|$))
        """,
        _rst_field,
        contents,
        flags=re.MULTILINE | re.VERBOSE,
    )
