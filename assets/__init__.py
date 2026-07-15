"""
Assets package marker.

The ``assets/`` directory holds raw project files such as icons, logos, and
screenshots. These are not normally imported by Python code (runtime,
code-accessible resources live in :mod:`app.resources` instead).

This ``__init__.py`` is optional and included only so the directory can be
treated as an importable package and referenced via package-relative paths if
desired. It intentionally exposes just a helper to locate bundled files.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["ASSETS_DIR", "asset_path"]

# Absolute path to this assets directory.
ASSETS_DIR = Path(__file__).resolve().parent


def asset_path(*parts: str) -> Path:
    """
    Return an absolute path to a file inside ``assets/``.

    Example: ``asset_path("icons", "app.png")``.
    """
    return ASSETS_DIR.joinpath(*parts)
