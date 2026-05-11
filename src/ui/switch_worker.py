# Background worker for the account-switch flow.
#
# Why this exists:
#   The switch pipeline (kill -> clear session -> launch -> wait for window
#   -> type) takes several seconds. Running it on the GUI thread would
#   freeze the UI ("not responding") until it finishes. PyQt's standard
#   pattern is QObject + moveToThread(QThread), with signals reporting
#   progress and completion back to the GUI thread.
#
# Lifecycle (managed by MainWindow):
#   1. Create SwitchWorker(...) and a QThread.
#   2. worker.moveToThread(thread).
#   3. thread.started.connect(worker.run)
#   4. worker.finished / worker.failed connect to GUI handlers.
#   5. Both finished and failed connect to thread.quit so the thread shuts down.
#   6. thread.finished.connect(worker.deleteLater) and thread.deleteLater so
#      Python doesn't leak.

import logging

from PyQt6.QtCore import QObject, pyqtSignal

from src.riot.launcher import (
    AUTO_FILL_CLIPBOARD,
    LauncherError,
    switch_account,
)

log = logging.getLogger(__name__)


class SwitchWorker(QObject):
    # Emitted with a short status string (e.g. "Launching Riot Client...").
    progress = pyqtSignal(str)
    # Emitted on success after the credentials have been typed.
    finished = pyqtSignal(str)
    # Emitted on any failure with a human-friendly message.
    failed = pyqtSignal(str)

    def __init__(self, username: str, password: str, install_path: str,
                 auto_fill_mode: str = AUTO_FILL_CLIPBOARD,
                 riot_id: str = ""):
        super().__init__()
        self._username = username
        self._password = password
        self._install_path = install_path
        self._auto_fill_mode = auto_fill_mode
        self._riot_id = riot_id

    def run(self) -> None:
        # Runs on the worker thread. Never throw — convert all exceptions to
        # the failed signal so the GUI thread stays sane.
        try:
            switch_account(
                username=self._username,
                password=self._password,
                install_path=self._install_path,
                auto_fill_mode=self._auto_fill_mode,
                progress=self._emit_progress,
            )
            self.finished.emit(self._riot_id)
        except LauncherError as exc:
            log.info("switch failed: %s", exc)
            self.failed.emit(str(exc))
        except Exception as exc:
            # Anything else is genuinely unexpected; log full traceback.
            log.exception("switch worker crashed")
            self.failed.emit(f"Internal error: {exc}")

    def _emit_progress(self, msg: str) -> None:
        log.debug("switch progress: %s", msg)
        self.progress.emit(msg)
