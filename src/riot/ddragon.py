# Data Dragon (DDragon) static-asset CDN client.
#
# Data Dragon is Riot's free, unauthenticated CDN that hosts the PNG bytes
# behind every numeric ID the Riot Web API returns (champions, items,
# profile icons, summoner spells, etc.). No API key, no rate limit, just
# HTTP GETs. The catch: every URL is versioned by game patch, so we have to
# fetch the patch list once and pick the latest.
#
# Endpoints we use:
#   - https://ddragon.leagueoflegends.com/api/versions.json
#       -> JSON list of every patch ever released; index 0 is latest.
#   - https://ddragon.leagueoflegends.com/cdn/<version>/img/profileicon/<id>.png
#       -> the actual icon PNG.
#
# What this module does:
#   1. Cache the latest version string in %APPDATA%\RiotAccountSwitcher\
#      cache\ddragon_version.json, refresh once per day.
#   2. Cache downloaded profile-icon PNGs by id in the same cache folder so
#      we only ever download each icon id once. Each PNG is ~5–15 KB.
#   3. Hand callers an absolute Path they can hand to QPixmap.
#
# No PyQt here on purpose — this module is pure I/O so it stays testable
# and won't drag Qt into background-download paths.

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests

# Tweakables — top of file so they're obvious during debugging.
DDRAGON_BASE = "https://ddragon.leagueoflegends.com"
VERSIONS_URL = f"{DDRAGON_BASE}/api/versions.json"
HTTP_TIMEOUT_SECONDS = 5.0      # DDragon is fast; bail quickly if it's not
VERSION_CACHE_TTL_SECONDS = 24 * 3600  # one day — patches don't change often

# Fallback used when the versions.json call fails on first launch and we
# have nothing cached. Picked deliberately high so the URL is unlikely to
# 404; DDragon serves icons against any prior version too.
FALLBACK_VERSION = "14.21.1"

log = logging.getLogger(__name__)


# ---------- cache paths ----------

def cache_root() -> Path:
    # %APPDATA%\RiotAccountSwitcher\cache (or ~ fallback if APPDATA missing).
    # Always writable for the current user — distinct from the read-only
    # PyInstaller-bundled assets/ directory.
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home()
    return base / "RiotAccountSwitcher" / "cache"


def _version_cache_path() -> Path:
    return cache_root() / "ddragon_version.json"


def _profile_icon_dir() -> Path:
    return cache_root() / "profile_icons"


def profile_icon_local_path(icon_id: int) -> Path:
    # Where icon_id will live on disk after a successful download.
    return _profile_icon_dir() / f"{icon_id}.png"


# ---------- version lookup ----------

def _read_cached_version() -> Optional[str]:
    path = _version_cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ddragon version cache unreadable: %s", exc)
        return None
    version = data.get("version")
    fetched = data.get("fetched_at")
    if not isinstance(version, str) or not isinstance(fetched, (int, float)):
        return None
    age = time.time() - float(fetched)
    if age >= VERSION_CACHE_TTL_SECONDS:
        log.debug("ddragon version cache expired (%.0fs old)", age)
        return None
    return version


def _write_cached_version(version: str) -> None:
    path = _version_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(
            json.dumps({"version": version, "fetched_at": time.time()}),
            encoding="utf-8",
        )
    except OSError as exc:
        # Cache is a nice-to-have; don't crash a launch just because the
        # disk is full or APPDATA is locked.
        log.warning("could not write ddragon version cache: %s", exc)


def _fetch_latest_version() -> Optional[str]:
    # Hits DDragon's versions.json. Returns None on any failure so the
    # caller can fall back gracefully.
    log.info("fetching DDragon versions list")
    try:
        r = requests.get(VERSIONS_URL, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        log.info("ddragon versions fetch failed: %s", exc)
        return None
    if r.status_code != 200:
        log.info("ddragon versions returned HTTP %d", r.status_code)
        return None
    try:
        versions = r.json()
    except ValueError as exc:
        log.warning("ddragon versions.json was not valid JSON: %s", exc)
        return None
    if not isinstance(versions, list) or not versions:
        log.warning("ddragon versions.json had unexpected shape")
        return None
    latest = versions[0]
    if not isinstance(latest, str):
        return None
    log.info("ddragon latest version: %s", latest)
    return latest


def latest_version(force: bool = False) -> str:
    # Returns the patch version string ("14.21.1") to use for DDragon URLs.
    # Cached in %APPDATA% for VERSION_CACHE_TTL_SECONDS. Worst case, we
    # return FALLBACK_VERSION when everything fails — better an old icon
    # than a crash.
    if not force:
        cached = _read_cached_version()
        if cached:
            return cached
    fresh = _fetch_latest_version()
    if fresh:
        _write_cached_version(fresh)
        return fresh
    # Last-resort: any previously cached version (even stale) beats the
    # hard-coded fallback. Try the file again, ignoring TTL.
    path = _version_cache_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stale = data.get("version")
            if isinstance(stale, str):
                log.info("using stale ddragon version: %s", stale)
                return stale
        except (OSError, json.JSONDecodeError):
            pass
    log.info("using fallback ddragon version: %s", FALLBACK_VERSION)
    return FALLBACK_VERSION


# ---------- profile icon download ----------

def profile_icon_url(icon_id: int, version: Optional[str] = None) -> str:
    # Builds the full DDragon URL for a given icon id. Pass a known version
    # to skip the cache lookup (handy when iterating in tests).
    ver = version or latest_version()
    return f"{DDRAGON_BASE}/cdn/{ver}/img/profileicon/{icon_id}.png"


def ensure_profile_icon(icon_id: int) -> Optional[Path]:
    # Downloads icon_id to disk if it's not already there. Returns the
    # local path on success, or None if the download failed (network down,
    # 404, etc.). Safe to call from any thread that has network access.
    if icon_id is None:
        return None
    target = profile_icon_local_path(icon_id)
    if target.exists() and target.stat().st_size > 0:
        return target

    # First attempt with the cached/latest version.
    if _try_download(icon_id, latest_version(), target):
        return target
    # 404? Maybe a new patch added the icon. Force-refresh versions.json
    # and retry once.
    log.info("retrying icon %d after refreshing ddragon version", icon_id)
    if _try_download(icon_id, latest_version(force=True), target):
        return target
    return None


def _try_download(icon_id: int, version: str, target: Path) -> bool:
    url = profile_icon_url(icon_id, version=version)
    log.debug("GET %s", url)
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        log.info("profile icon %d download failed: %s", icon_id, exc)
        return False
    if r.status_code != 200:
        log.info("profile icon %d returned HTTP %d at version %s",
                 icon_id, r.status_code, version)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file then rename so a half-written PNG never makes
    # it onto disk (network drops, app killed mid-write, etc.).
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_bytes(r.content)
        os.replace(tmp, target)
    except OSError as exc:
        log.warning("could not save profile icon %d: %s", icon_id, exc)
        return False
    log.info("cached profile icon %d (%d bytes)", icon_id, len(r.content))
    return True
