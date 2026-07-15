"""
Static resources.

Marks the resources directory as a package so bundled assets can be resolved
via package paths in a way that works both in development and when the app is
frozen (e.g. with PyInstaller).

Contents typically include:

- ``oui.csv``      -- offline MAC/OUI vendor database (see
  :class:`app.network.vendor_lookup.VendorLookup`).
- stylesheets, icons, and other files loaded by the UI at runtime.

Use :func:`resource_path` to build an absolute path to a bundled file rather
than hard-coding relative paths, which break once the app is packaged.

Note
----
This is distinct from the project-root ``assets/`` directory, which holds raw
files (screenshots, logos) that are *not* imported by Python and therefore is
not a package.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["RESOURCES_DIR", "resource_path"]

# Absolute path to this resources directory.
RESOURCES_DIR = Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    """
    Return an absolute path to a bundled resource.

    Parameters
    ----------
    *parts:
        Path components relative to this directory, e.g.
        ``resource_path("oui.csv")`` or ``resource_path("icons", "app.png")``.

    Returns
    -------
    pathlib.Path
        The absolute path (whether or not the file exists).
    """
    return RESOURCES_DIR.joinpath(*parts)