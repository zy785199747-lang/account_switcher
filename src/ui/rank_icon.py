# Rank icon resolver.
#
# Returns a QPixmap for a given Riot tier ("DIAMOND", "GOLD", ...) at the
# requested size. Two paths:
#
#   1. If a recognised PNG exists under assets/ranks/, load + scale + cache it.
#      We try several filename conventions per tier (TIER_FILES below) so the
#      user can drop in Riot's official zip directly (Season_2023_-_Diamond.png)
#      OR a cleaner lowercase rename (diamond.png) — first match wins.
#
#   2. Otherwise, draw a procedural badge: a tier-colored circle with a darker
#      ring. Works out-of-the-box, ships no copyrighted artwork, and stays
#      consistent with the rest of the dark-mode card UI.
#
# Both branches go through the same on-disk pixmap cache keyed by (tier, size),
# so subsequent card renders are free.
#
# Why a separate module:
#   - Resource resolution differs between dev and PyInstaller. Concentrating
#     the path logic here keeps account_card.py focused on layout.
#   - The cache and procedural drawer are reusable from anywhere (e.g. the
#     Settings dialog later, if we ever add a "preview" widget).

import logging
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap

log = logging.getLogger(__name__)

# Tier -> primary fill colour. Roughly matches the well-known LoL ranked
# palette so a glance at the card body is enough to know the rank tier.
# Keys are normalised to upper-case; lookup goes through _normalise().
TIER_COLORS: Dict[str, str] = {
    "IRON":        "#5a5a5a",
    "BRONZE":      "#a87446",
    "SILVER":      "#a6a6a6",
    "GOLD":        "#cd9b16",
    "PLATINUM":    "#4ba3a8",
    "EMERALD":     "#1fb872",
    "DIAMOND":     "#5e8eff",
    "MASTER":      "#a04bd1",
    "GRANDMASTER": "#c34141",
    "CHALLENGER":  "#e8d36b",
}

# Candidate filenames per tier, tried in order — first existing file wins.
# Supports:
#   - Riot's official "Ranked Emblems 2023" pack (Season_2023_-_<Tier>.png)
#   - cleaner lowercase rename (<tier>.png)
# Add more variants here if you ever ship a different art pack.
TIER_FILES: Dict[str, list] = {
    "IRON":        ["iron.png", "Iron.png", "Season_2023_-_Iron.png"],
    "BRONZE":      ["bronze.png", "Bronze.png", "Season_2023_-_Bronze.png"],
    "SILVER":      ["silver.png", "Silver.png", "Season_2023_-_Silver.png"],
    "GOLD":        ["gold.png", "Gold.png", "Season_2023_-_Gold.png"],
    "PLATINUM":    ["platinum.png", "Platinum.png", "Season_2023_-_Platinum.png"],
    "EMERALD":     ["emerald.png", "Emerald.png", "Season_2023_-_Emerald.png"],
    "DIAMOND":     ["diamond.png", "Diamond.png", "Season_2023_-_Diamond.png"],
    "MASTER":      ["master.png", "Master.png", "Season_2023_-_Master.png"],
    "GRANDMASTER": ["grandmaster.png", "Grandmaster.png",
                    "Season_2023_-_Grandmaster.png"],
    "CHALLENGER":  ["challenger.png", "Challenger.png",
                    "Season_2023_-_Challenger.png"],
}

# Used when an account has no tier yet (cached_tier is None / unranked).
# The Unranked badge is also looked up via TIER_FILES below.
UNRANKED_KEY = "__UNRANKED__"
UNRANKED_COLOR = "#3a3a3a"
TIER_FILES[UNRANKED_KEY] = [
    "unranked.png", "Unranked.png", "Season_2023_-_Unranked.png",
]

# Pixmap cache so re-rendering 5 cards doesn't redo the QPainter work.
_pixmap_cache: Dict[Tuple[str, int], QPixmap] = {}


# ---------- resource resolution ----------

def _resource_root() -> Path:
    # PyInstaller extracts bundled data files into sys._MEIPASS at runtime.
    # In dev mode that attribute isn't set, so we walk up to the repo root.
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    # src/ui/rank_icon.py -> parents[0]=ui, [1]=src, [2]=repo root.
    return Path(__file__).resolve().parents[2]


def _png_path_for(tier_upper: str) -> Optional[Path]:
    # Try each candidate filename for the tier; return the first that exists.
    # None means "no user art available — caller should draw a procedural
    # badge instead." Iteration is over a tiny list (~3 entries) so cost is
    # negligible per call, and rank_pixmap caches the result anyway.
    candidates = TIER_FILES.get(tier_upper)
    if not candidates:
        return None
    base = _resource_root() / "assets" / "ranks"
    for name in candidates:
        candidate = base / name
        if candidate.exists():
            return candidate
    return None


# ---------- public API ----------

def _normalise(tier: Optional[str]) -> str:
    # "DIAMOND" / "Diamond" / "diamond" all collapse to "DIAMOND".
    # None / empty -> UNRANKED_KEY so we still return a sensible pixmap.
    if not tier:
        return UNRANKED_KEY
    return tier.strip().upper()


def rank_pixmap(tier: Optional[str], size: int = 36) -> QPixmap:
    # Single entry point used by AccountCard. Caching is intentional: the
    # grid rebuild rerenders every card, and procedural drawing isn't free.
    key = (_normalise(tier), size)
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached

    tier_upper, _ = key
    pix = _try_load_png(tier_upper, size)
    if pix is None:
        pix = _draw_procedural(tier_upper, size)
    _pixmap_cache[key] = pix
    return pix


# ---------- implementation ----------

def _try_load_png(tier_upper: str, size: int) -> Optional[QPixmap]:
    # User-supplied PNGs win when present. Scale on load so cards don't have
    # to do it every paint.
    png_path = _png_path_for(tier_upper)
    if png_path is None:
        return None
    pix = QPixmap(str(png_path))
    if pix.isNull():
        # File exists but isn't a valid image — log and let the procedural
        # path take over rather than show a broken icon.
        log.warning("rank icon file unreadable: %s", png_path)
        return None
    log.debug("loaded rank icon from %s", png_path)
    return pix.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _draw_procedural(tier_upper: str, size: int) -> QPixmap:
    # Filled circle with a slightly-darker ring. Cheap, readable, themeless.
    color_hex = TIER_COLORS.get(tier_upper, UNRANKED_COLOR)
    fill = QColor(color_hex)
    ring = fill.darker(140)  # 40% darker — gives the badge a defined edge

    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Inset by 1px so the ring isn't clipped against the pixmap edge.
        inset = 1
        rect_size = size - 2 * inset

        # Ring first (drawn as a stroke).
        pen = QPen(ring)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(QBrush(fill))
        painter.drawEllipse(inset, inset, rect_size, rect_size)

        # If we're rendering "unranked", overlay a subtle dashed inner line
        # so the icon visually reads as "no rank yet" rather than just dark.
        if tier_upper == UNRANKED_KEY:
            dash_pen = QPen(QColor("#555"))
            dash_pen.setWidth(1)
            dash_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(dash_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            margin = max(3, size // 6)
            painter.drawEllipse(margin, margin,
                                size - 2 * margin, size - 2 * margin)
    finally:
        # Painter must finish before the QPixmap can be returned safely.
        painter.end()
    return pix
