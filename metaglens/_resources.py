"""Cross-version packaged-resource loading.

``importlib.resources.files()`` is the modern API but only exists on
Python 3.9+; the package still supports Python 3.8, where
``resources.path()`` is the fallback. Everything that reads a bundled
resource (shell templates, logo assets, rules, knowledge) goes through
:func:`read_resource` so the same code works on every supported Python.
"""

from __future__ import annotations


def read_resource(package: str, name: str) -> str:
    """Read a packaged resource file as UTF-8 text."""
    try:
        from importlib import resources

        return resources.files(package).joinpath(name).read_text(
            encoding="utf-8")
    except AttributeError:  # pragma: no cover - Python 3.8 fallback
        from importlib import resources

        with resources.path(package, name) as path:
            return path.read_text(encoding="utf-8")
