# Background updater worker.

import logging
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from src.updater import UpdateInfo, check_for_update, download_update

log = logging.getLogger(__name__)


class UpdateWorker(QObject):
    update_available = pyqtSignal(object)
    no_update = pyqtSignal()
    downloaded = pyqtSignal(object, object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, download: bool = False,
                 info: Optional[UpdateInfo] = None):
        super().__init__()
        self._download = download
        self._info = info

    def run(self) -> None:
        try:
            if self._download:
                if self._info is None:
                    raise RuntimeError("No update info was provided")
                path = download_update(self._info)
                self.downloaded.emit(self._info, path)
                return

            info = check_for_update()
            if info is None:
                self.no_update.emit()
            else:
                self.update_available.emit(info)
        except Exception as exc:
            log.info("update worker failed: %s", exc)
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
