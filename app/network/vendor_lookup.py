"""
MAC address -> vendor (manufacturer) lookup.
​
Resolves the hardware manufacturer of a device from the OUI (Organizationally
Unique Identifier) -- the first 24 bits (3 bytes) of its MAC address, which
IEEE assigns to each vendor.
​
Design
------
- **Offline first.** Lookups run against a locally bundled OUI table so the
  application never depends on an internet connection or a rate-limited API.
  The table is loaded once and cached in memory as ``{oui: vendor}``.
- **Pluggable data file.** The table is read from a CSV/text file (default:
  ``app/resources/oui.csv``). Both the official IEEE ``oui.csv`` export and a
  simple ``PREFIX,Vendor`` format are accepted. A small built-in table of
  common vendors is used as a fallback when no data file is present.
- **Randomized-MAC aware.** Modern phones and laptops use locally administered
  (randomized) MACs that have no real vendor; these are detected and reported
  as such instead of returning a misleading match.
- GUI-agnostic; safe to import from the network layer only.
​
Typical usage
-------------
    >>> lookup = VendorLookup()
    >>> lookup.lookup("3c:5a:b4:11:22:33")
    'Google, Inc.'
    >>> lookup.lookup("b4:e6:2d:aa:bb:cc")
    'Espressif Inc.'
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

__all__ = ["VendorLookup"]

logger = logging.getLogger(__name__)

# Default location of the bundled OUI table, relative to the project's
# ``app/resources`` directory. Resolved relative to this file so it works in
# development and when frozen.
_DEFAULT_DATA_FILE = Path(__file__).resolve().parent.parent / "resources" / "oui.csv"

# Label returned for locally administered / randomized MAC addresses.
_RANDOMIZED_LABEL = "Locally administered (randomized)"

# Minimal built-in fallback so the feature is useful even before a full IEEE
# OUI export is bundled. Keys are 6 hex chars (OUI), upper-case, no separators.
_BUILTIN_OUIS: dict[str, str] = {
    "FCFBFB": "Cisco Systems, Inc.",
    "001A2B": "Cisco Systems, Inc.",
    "3C5AB4": "Google, Inc.",
    "F4F5E8": "Google, Inc.",
    "B4E62D": "Espressif Inc.",
    "A4CF12": "Espressif Inc.",
    "DCA632": "Raspberry Pi Trading Ltd",
    "B827EB": "Raspberry Pi Foundation",
    "E45F01": "Raspberry Pi Trading Ltd",
    "001C42": "Parallels, Inc.",
    "000C29": "VMware, Inc.",
    "005056": "VMware, Inc.",
    "080027": "PCS Systemtechnik GmbH (VirtualBox)",
    "525400": "QEMU / KVM virtual NIC",
    "D83ADD": "Apple, Inc.",
    "F0189E": "Apple, Inc.",
    "A4C361": "Apple, Inc.",
    "ACDE48": "Apple, Inc.",
    "001B63": "Apple, Inc.",
    "D4619D": "Samsung Electronics Co.,Ltd",
    "5CF370": "Samsung Electronics Co.,Ltd",
    "F8A9D0": "LG Electronics",
    "001E58": "D-Link Corporation",
    "1CBDB9": "D-Link International",
    "C0A0BB": "D-Link International",
    "00179A": "D-Link Corporation",
    "6045CB": "ASUSTek COMPUTER INC.",
    "AC220B": "ASUSTek COMPUTER INC.",
    "E03F49": "ASUSTek COMPUTER INC.",
    "F4EC38": "TP-LINK TECHNOLOGIES CO.,LTD.",
    "50C7BF": "TP-LINK TECHNOLOGIES CO.,LTD.",
    "A42BB0": "TP-LINK TECHNOLOGIES CO.,LTD.",
    "9C5C8E": "ASRock Incorporation",
    "001478": "HUAWEI TECHNOLOGIES CO.,LTD",
    "48435A": "HUAWEI TECHNOLOGIES CO.,LTD",
    "04BD70": "Xiaomi Communications Co Ltd",
    "286C07": "Xiaomi Communications Co Ltd",
    "001132": "Synology Incorporated",
    "0011D8": "ASUSTek COMPUTER INC.",
    "C46E1F": "TP-LINK TECHNOLOGIES CO.,LTD.",
    "18FE34": "Espressif Inc.",
}

_HEX_ONLY = re.compile(r"[^0-9A-Fa-f]")


class VendorLookup:
    """
    Resolves device vendors from MAC addresses using an OUI table.

    Parameters
    ----------
    data_file:
        Path to an OUI data file. Defaults to ``app/resources/oui.csv``. If
        the file does not exist, only the built-in fallback table is used.
    use_builtin_fallback:
        When ``True`` (default), the built-in common-vendor table is merged in
        underneath any entries loaded from *data_file*.
    """

    def __init__(
        self,
        data_file: str | Path | None = None,
        use_builtin_fallback: bool = True,
    ) -> None:
        self._data_file = Path(data_file) if data_file else _DEFAULT_DATA_FILE
        self._table: dict[str, str] = {}

        if use_builtin_fallback:
            self._table.update(_BUILTIN_OUIS)

        loaded = self._load_data_file(self._data_file)
        # File entries take precedence over the built-in fallback.
        self._table.update(loaded)

        logger.info(
            "VendorLookup ready: %d OUI entries (%s)",
            len(self._table),
            self._data_file if loaded else "built-in table only",
        )

    # -- public API ------------------------------------------------------

    @property
    def size(self) -> int:
        """Number of OUI entries currently loaded."""
        return len(self._table)

    def lookup(self, mac: str) -> str | None:
        """
        Return the vendor for *mac*, or ``None`` if unknown.

        Randomized / locally administered MAC addresses return a descriptive
        label rather than ``None`` so the UI can distinguish "no vendor" from
        "vendor deliberately hidden".

        Parameters
        ----------
        mac:
            A MAC address in any common notation (colons, hyphens, dots, or
            none). Case-insensitive.
        """
        oui = self._extract_oui(mac)
        if oui is None:
            return None

        if self._is_locally_administered(oui):
            return _RANDOMIZED_LABEL

        return self._table.get(oui)

    # -- parsing / loading ----------------------------------------------

    @staticmethod
    def _extract_oui(mac: str) -> str | None:
        """
        Normalize *mac* and return its 6-hex-char OUI (upper-case).

        Returns ``None`` if the input does not contain at least 6 hex digits.
        """
        if not mac:
            return None
        hex_digits = _HEX_ONLY.sub("", mac).upper()
        if len(hex_digits) < 6:
            return None
        return hex_digits[:6]

    @staticmethod
    def _is_locally_administered(oui: str) -> bool:
        """
        Return ``True`` if the OUI's second-least-significant bit of the first
        octet is set, marking a locally administered (often randomized) MAC.
        """
        try:
            first_octet = int(oui[:2], 16)
        except ValueError:
            return False
        return bool(first_octet & 0b0000_0010)

    def _load_data_file(self, path: Path) -> dict[str, str]:
        """
        Load OUI -> vendor mappings from *path*.

        Accepts two formats transparently:

        1. **IEEE ``oui.csv``** with a header row containing
           ``Assignment`` and ``Organization Name`` columns.
        2. **Simple** two-column ``PREFIX,Vendor`` rows (prefix in any MAC
           notation), with or without a header.

        Missing or unreadable files yield an empty mapping (a warning is
        logged) so construction never fails.
        """
        if not path.exists():
            logger.info("OUI data file not found at %s; using built-in table", path)
            return {}

        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                sample = handle.read(4096)
                handle.seek(0)
                if "Organization Name" in sample and "Assignment" in sample:
                    return self._parse_ieee_csv(handle)
                return self._parse_simple_csv(handle)
        except OSError as exc:
            logger.warning("Could not read OUI data file %s: %s", path, exc)
            return {}

    def _parse_ieee_csv(self, handle) -> dict[str, str]:
        """Parse the official IEEE ``oui.csv`` export."""
        table: dict[str, str] = {}
        reader = csv.DictReader(handle)
        for row in reader:
            assignment = (row.get("Assignment") or "").strip()
            vendor = (row.get("Organization Name") or "").strip()
            oui = self._extract_oui(assignment)
            if oui and vendor:
                table[oui] = vendor
        return table

    def _parse_simple_csv(self, handle) -> dict[str, str]:
        """Parse a simple ``PREFIX,Vendor`` file, tolerating a header row."""
        table: dict[str, str] = {}
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 2:
                continue
            oui = self._extract_oui(row[0])
            vendor = row[1].strip()
            if not oui or not vendor:
                continue
            # Skip an obvious header line such as "prefix,vendor".
            if vendor.lower() in {"vendor", "organization name", "manufacturer"}:
                continue
            table[oui] = vendor
        return table
