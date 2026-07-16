"""Network Monitor Pro -- application entry point.
​
Responsibilities
----------------
1. Configure application-wide **logging** (console + rotating file in ``logs/``).
2. Load runtime **configuration** from ``config.json`` (falling back to sane
   defaults, and writing a default file on first run).
3. Open the **SQLite database** used for persistence.
4. Create the ``QApplication`` and the :class:`~app.ui.main_window.MainWindow`,
   injecting the database so the window can own the background services
   (bandwidth monitor, scanner, persistence).
5. Wire the Settings page so saved changes are persisted back to disk.
6. Run the Qt event loop and close the database cleanly on exit.
​
This module is intentionally thin: it wires top-level components together and
owns the process lifecycle, while all feature logic lives in the ``app``
package.
​
Run with::
​
    python main.py
​
Note: ARP scanning needs elevated privileges (Administrator on Windows with
Npcap installed, or root on Linux).
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

# Project root and important paths.
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
LOG_DIR = BASE_DIR / "logs"

# Default configuration written on first run and used to fill missing keys.
DEFAULT_CONFIG: dict[str, Any] = {
    "refresh_interval": 1.0,
    "interface": None,
    "theme": "dark",
    "database_path": str(BASE_DIR / "network_monitor.db"),
    "log_level": "INFO",
}

logger = logging.getLogger("network_monitor")


def setup_logging(log_level: str = "INFO") -> None:
    """Configure root logging with a console handler and a rotating file handler.

    Logs are written to ``logs/network_monitor.log`` (rotated at 1 MB, keeping
    five backups) so errors, warnings, scans, and device discovery are all
    captured per the spec.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "network_monitor.log"

    level = getattr(logging, str(log_level).upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers if setup is called more than once.
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    logger.info("Logging initialized (level=%s, file=%s)", log_level, log_file)


def load_config() -> dict[str, Any]:
    """Load configuration from ``config.json``.

    Missing files are created with :data:`DEFAULT_CONFIG`; missing individual
    keys are backfilled from the defaults so upgrades never crash on new keys.
    """
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        logger.info("Created default config at %s", CONFIG_PATH)
        return dict(DEFAULT_CONFIG)

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read config (%s); using defaults", exc)
        return dict(DEFAULT_CONFIG)

    config = dict(DEFAULT_CONFIG)
    config.update(loaded or {})
    return config


def save_config(config: dict[str, Any]) -> None:
    """Persist *config* to ``config.json`` (merged over the defaults)."""
    merged = dict(DEFAULT_CONFIG)
    merged.update(config or {})
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2)
        logger.info("Configuration saved to %s", CONFIG_PATH)
    except OSError as exc:
        logger.error("Failed to save config: %s", exc)


def _open_database(config: dict[str, Any]):
    """Open the SQLite database, returning the instance or ``None`` on failure."""
    # Prefer the packaged path (app.database.database); fall back to a
    # top-level database package so the app works regardless of how it is run.
    Database = None
    for module_path in ("app.database.database", "database.database"):
        try:
            module = __import__(module_path, fromlist=["Database"])
            Database = module.Database
            break
        except Exception as exc:
            logger.debug("Could not import %s: %s", module_path, exc)
    if Database is None:
        logger.error("Database layer unavailable; running without persistence")
        return None

    db_path = config.get("database_path") or str(BASE_DIR / "network_monitor.db")
    try:
        database = Database(db_path)
        logger.info("Database opened at %s", db_path)
        return database
    except Exception as exc:
        logger.error(
            "Could not open database at %s (%s); running without persistence",
            db_path,
            exc,
        )
        return None


def main() -> int:
    """Application entry point. Returns the process exit code."""
    config = load_config()
    setup_logging(config.get("log_level", "INFO"))
    logger.info("Starting Network Monitor Pro")

    # Import Qt lazily so logging/config errors are reported cleanly even if
    # the GUI dependencies are missing.
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover
        logger.critical("PySide6 is required to run the GUI: %s", exc)
        print(
            "Error: PySide6 is not installed. Install dependencies with\n"
            "    pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    from app import __app_name__
    from app.ui.main_window import MainWindow

    # Open the database before building the window so services can use it.
    database = _open_database(config)

    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setOrganizationName("Network Monitor Pro")

    window = MainWindow(config=config, database=database)

    # Persist settings changes back to config.json when the user saves.
    settings_page = getattr(window, "settings_page", None)
    if settings_page is not None and hasattr(settings_page, "settings_changed"):
        settings_page.settings_changed.connect(_on_settings_changed)

    window.show()
    logger.info("Main window shown; entering event loop")

    try:
        exit_code = app.exec()
    finally:
        # Close the database once the UI has shut down (window.closeEvent has
        # already stopped the background services by this point).
        if database is not None:
            try:
                database.close()
            except Exception as exc:
                logger.debug("Error closing database: %s", exc)

    logger.info("Network Monitor Pro exited with code %s", exit_code)
    return exit_code


def _on_settings_changed(settings: dict[str, Any]) -> None:
    """Merge updated settings into config.json when the user saves them."""
    current = load_config()
    current.update(settings)
    save_config(current)


if __name__ == "__main__":
    sys.exit(main())
