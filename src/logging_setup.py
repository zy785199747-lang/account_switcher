# Logging configuration.
#
# - Always logs to %APPDATA%\RiotAccountSwitcher\logs\app.log (rotating).
# - Console output is INFO by default, DEBUG when --debug flag is set.
# - Each module does `log = logging.getLogger(__name__)` so log lines are
#   prefixed with the module path. Easy to grep when debugging.

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_FILE_MAX_BYTES = 1_000_000  # 1 MB
LOG_FILE_BACKUP_COUNT = 5


def default_log_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home()
    return base / "RiotAccountSwitcher" / "logs"


def setup(debug: bool = False) -> Path:
    # Wire up handlers on the root logger. Returns the log file path so main
    # can print it on exit (handy when something goes wrong).
    log_dir = default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    root = logging.getLogger()
    # Reset so calling setup() twice (e.g. during tests) doesn't stack handlers.
    root.handlers.clear()
    root.setLevel(logging.DEBUG)  # let handlers filter

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_FILE_MAX_BYTES,
        backupCount=LOG_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(console_handler)

    logging.getLogger(__name__).info("logging initialised, file=%s debug=%s",
                                     log_file, debug)
    return log_file
