"""
This module implements pdoc's live-reloading webserver.

We want to keep the number of dependencies as small as possible,
so we are content with the builtin `http.server` module.
It is a bit unergonomic compared to let's say flask, but good enough for our purposes.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Iterator
from functools import cache
import http.server
import traceback
from typing import Mapping
import urllib.parse
import warnings
import webbrowser

from pdoc import doc
from pdoc import extract
from pdoc import render


class DocHandler(http.server.BaseHTTPRequestHandler):
    """A handler for individual requests."""

    server: DocServer
    """A reference to the main web server."""

    def do_HEAD(self):
        pass

    def do_GET(self):
        pass

    def handle_request(self) -> str:
        """Actually handle a request. Called by `do_HEAD` and `do_GET`."""
        pass

    def log_request(self, code: int | str = ..., size: int | str = ...) -> None:
        """Override logging to disable it."""


class DocServer(http.server.HTTPServer):
    """pdoc's live-reloading web server"""

    all_modules: AllModules

    def __init__(self, addr: tuple[str, int], specs: list[str], **kwargs):
        super().__init__(addr, DocHandler, **kwargs)  # type: ignore
        module_names = extract.walk_specs(specs)
        self.all_modules = AllModules(module_names)

    @cache
    def render_search_index(self) -> str:
        """Render the search index. For performance reasons this is always cached."""
        pass


class AllModules(Mapping[str, doc.Module]):
    """A lazy-loading implementation of all_modules.

    This behaves like a regular dict, but modules are only imported on demand for performance reasons.
    This has the somewhat annoying side effect that __getitem__ may raise a RuntimeError.
    We can ignore that when rendering HTML as the default templates do not access all_modules values,
    but we need to perform additional steps for the search index.
    """

    def __init__(self, allowed_modules: Iterable[str]):
        # use a dict to preserve order
        self.allowed_modules: dict[str, None] = dict.fromkeys(allowed_modules)

    def __len__(self) -> int:
        return self.allowed_modules.__len__()

    def __iter__(self) -> Iterator[str]:
        return self.allowed_modules.__iter__()

    def __contains__(self, item):
        return self.allowed_modules.__contains__(item)

    def __getitem__(self, item: str):
        if item in self.allowed_modules:
            return doc.Module.from_name(item)
        else:  # pragma: no cover
            raise KeyError(item)


# https://github.com/mitmproxy/mitmproxy/blob/af3dfac85541ce06c0e3302a4ba495fe3c77b18a/mitmproxy/tools/web/webaddons.py#L35-L61
def open_browser(url: str) -> bool:  # pragma: no cover
    """
    Open a URL in a browser window.
    In contrast to `webbrowser.open`, we limit the list of suitable browsers.
    This gracefully degrades to a no-op on headless servers, where `webbrowser.open`
    would otherwise open lynx.

    Returns:

    - `True`, if a browser has been opened
    - `False`, if no suitable browser has been found.
    """
    browsers = (
        "windows-default",
        "macosx",
        "wslview %s",
        "x-www-browser %s",
        "gnome-open %s",
        "google-chrome",
        "chrome",
        "chromium",
        "chromium-browser",
        "firefox",
        "opera",
        "safari",
    )
    for browser in browsers:
        try:
            b = webbrowser.get(browser)
        except webbrowser.Error:
            pass
        else:
            if b.open(url):
                return True
    return False
