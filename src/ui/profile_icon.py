# Profile icon resolver.
#
# Returns a QPixmap for the small avatar shown at the left of each card.
# Resolution order:
#
#   1. If `cached_profile_icon_id` is set and the DDragon PNG is on disk
#      under cache_root()/profile_icons/<id>.png -> load + circle-crop +
#      scale + cache.
#   2. Otherwise -> draw a procedural badge: the first letter of the game
#      name on a dark circle. Always non-null so the card never goes blank.
#
# All paths flow through the same (icon_id, game_name, size) pixmap cache
# so the grid rebuild doesn't redo the work for every card.
#
# Why a separate module:
#   - Mirrors rank_icon.py's structure for consistency.
#   - Keeps the disk-IO + Qt drawing out of account_card.py so the card
#     widget stays focused on layout.
#   - Network downloads live in src/riot/ddragon.py — this file only
#     touches disk + the Qt pixmap cache.

import logging
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from src.riot.ddragon import profile_icon_local_path

log = logging.getLogger(__name__)

# Pixmap cache so re-rendering 5 cards doesn't redo the file load + circle
# crop work. Key includes the game-name first letter so the fallback path
# caches per-account too.
_pixmap_cache: Dict[Tuple[Optional[int], str, int], QPixmap] = {}

# Procedural-fallback styling: dark slate circle + light letter, deliberately
# muted so the colorful DDragon icons stand out when present.
FALLBACK_BG = "#3a3a3a"
FALLBACK_RING = "#555"
FALLBACK_TEXT = "#ddd"


# ---------- public API ----------

def profile_icon_pixmap(icon_id: Optional[int],
                        game_name: str,
                        size: int = 40) -> QPixmap:
    # Returns a non-null QPixmap for the card to display. game_name is used
    # only for the fallback letter, but is part of the cache key so two
    # accounts that share icon_id=None still get distinct pixmaps.
    letter = _first_letter(game_name)
    key = (icon_id, letter, size)
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached

    pix = None
    if icon_id is not None:
        pix = _try_load_disk(icon_id, size)
    if pix is None:
        pix = _draw_fallback(letter, size)
    _pixmap_cache[key] = pix
    return pix


def clear_cache() -> None:
    # Test hook + a knob for "user dropped a new PNG" mid-session if we
    # ever wire that up. Drops every cached pixmap.
    _pixmap_cache.clear()


# ---------- implementation ----------

def _first_letter(game_name: Optional[str]) -> str:
    # Strip whitespace then take the first non-space char. Falls back to "?"
    # so the fallback is never blank — empty game names happen during the
    # Add Account dialog's typing.
    if not game_name:
        return "?"
    s = game_name.strip()
    if not s:
        return "?"
    return s[0].upper()


def _try_load_disk(icon_id: int, size: int) -> Optional[QPixmap]:
    path = profile_icon_local_path(icon_id)
    if not path.exists() or path.stat().st_size == 0:
        return None
    raw = QPixmap(str(path))
    if raw.isNull():
        log.warning("profile icon file unreadable: %s", path)
        return None
    return _circle_crop_and_scale(raw, size)


def _circle_crop_and_scale(src: QPixmap, size: int) -> QPixmap:
    # DDragon icons are 256x256 squares — we want a soft circular avatar
    # that matches the rank-emblem aesthetic on the rest of the card. Steps:
    #   1. Scale source to the target square (KeepAspectRatioByExpanding so
    #      a non-square source crops rather than letterboxes).
    #   2. Paint into a transparent pixmap clipped by a circular path.
    scaled = src.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addEllipse(QRectF(0, 0, size, size))
        painter.setClipPath(path)
        # Center-paste in case the scaled image is slightly larger than the
        # target on the cropped axis.
        x = (size - scaled.width()) // 2
        y = (size - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        # Thin ring on top for definition against dark cards.
        painter.setClipping(False)
        pen = QPen(QColor(FALLBACK_RING))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # drawEllipse with int coords antialiases cleaner than QRectF here.
        painter.drawEllipse(0, 0, size - 1, size - 1)
    finally:
        painter.end()
    return out


def _draw_fallback(letter: str, size: int) -> QPixmap:
    # Dark circle + a single bright letter centered. The letter is sized at
    # ~55% of the pixmap so it reads on a 40px circle but doesn't get
    # clipped on smaller sizes.
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Background disc with a slightly-lighter ring.
        bg = QColor(FALLBACK_BG)
        ring = QColor(FALLBACK_RING)
        pen = QPen(ring)
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(QBrush(bg))
        painter.drawEllipse(0, 0, size - 1, size - 1)

        # Centered letter.
        font = QFont()
        font.setPointSizeF(max(8.0, size * 0.50))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(FALLBACK_TEXT))
        painter.drawText(
            QRectF(0, 0, size, size),
            int(Qt.AlignmentFlag.AlignCenter),
            letter,
        )
    finally:
        painter.end()
    return out
