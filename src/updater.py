# GitHub Releases updater for the portable Windows build.

import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from src.version import APP_VERSION

REPO_OWNER = "zy785199747-lang"
REPO_NAME = "account_switcher"
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
)
RELEASES_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/latest"
APPDATA_DIR_NAME = "RiotAccountSwitcher"
DOWNLOAD_TIMEOUT_SECONDS = 30

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    release_url: str
    asset_name: str
    asset_url: str
    body: str = ""


def _version_tuple(version: str) -> tuple[int, ...]:
    cleaned = version.strip().lower()
    if cleaned.startswith("v"):
        cleaned = cleaned[1:]
    parts = re.findall(r"\d+", cleaned)
    return tuple(int(part) for part in parts) or (0,)


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    candidate_parts = _version_tuple(candidate)
    current_parts = _version_tuple(current)
    length = max(len(candidate_parts), len(current_parts))
    candidate_parts += (0,) * (length - len(candidate_parts))
    current_parts += (0,) * (length - len(current_parts))
    return candidate_parts > current_parts


def _updates_dir() -> Path:
    base = os.environ.get("APPDATA", "")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    path = root / APPDATA_DIR_NAME / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _best_windows_asset(assets: list[dict]) -> Optional[dict]:
    exe_assets = [
        asset for asset in assets
        if isinstance(asset, dict)
        and str(asset.get("name", "")).lower().endswith(".exe")
        and asset.get("browser_download_url")
    ]
    if not exe_assets:
        return None

    preferred = [
        asset for asset in exe_assets
        if "riotaccountswitcher" in str(asset.get("name", "")).lower()
    ]
    return (preferred or exe_assets)[0]


def check_for_update(current_version: str = APP_VERSION) -> Optional[UpdateInfo]:
    log.info("checking GitHub release for updates")
    response = requests.get(
        LATEST_RELEASE_API,
        headers={"Accept": "application/vnd.github+json"},
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    release = response.json()
    tag = str(release.get("tag_name", "")).strip()
    if not tag or not is_newer_version(tag, current_version):
        log.info("no update available (current=%s latest=%s)", current_version, tag)
        return None

    asset = _best_windows_asset(release.get("assets") or [])
    if asset is None:
        raise RuntimeError("Latest release has no Windows .exe asset")

    return UpdateInfo(
        current_version=current_version,
        latest_version=tag,
        release_url=str(release.get("html_url") or RELEASES_URL),
        asset_name=str(asset.get("name")),
        asset_url=str(asset.get("browser_download_url")),
        body=str(release.get("body") or ""),
    )


def download_update(info: UpdateInfo) -> Path:
    target = _updates_dir() / info.asset_name
    temp = target.with_suffix(target.suffix + ".download")
    log.info("downloading update %s to %s", info.latest_version, target)

    with requests.get(
        info.asset_url,
        stream=True,
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
    ) as response:
        response.raise_for_status()
        with temp.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

    temp.replace(target)
    return target


def running_from_frozen_exe() -> bool:
    return bool(getattr(sys, "frozen", False))


def _ps_single_quote(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _install_script_text(downloaded_exe: Path, current_exe: Path,
                         pid_to_wait: int) -> str:
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$pidToWait = {pid_to_wait}",
        f"$source = {_ps_single_quote(downloaded_exe)}",
        f"$target = {_ps_single_quote(current_exe)}",
        "$targetDir = Split-Path -Parent $target",
        "Wait-Process -Id $pidToWait -ErrorAction SilentlyContinue",
        "Start-Sleep -Seconds 2",
        "$deadline = (Get-Date).AddSeconds(20)",
        "while ((Get-Date) -lt $deadline) {",
        "    try {",
        "        $stream = [System.IO.File]::Open(",
        "            $target,",
        "            [System.IO.FileMode]::OpenOrCreate,",
        "            [System.IO.FileAccess]::ReadWrite,",
        "            [System.IO.FileShare]::None",
        "        )",
        "        $stream.Close()",
        "        break",
        "    } catch {",
        "        Start-Sleep -Milliseconds 250",
        "    }",
        "}",
        "Copy-Item -LiteralPath $source -Destination $target -Force",
        "Start-Sleep -Seconds 2",
        "Get-ChildItem Env:_PYI* -ErrorAction SilentlyContinue | Remove-Item",
        "$env:PYINSTALLER_RESET_ENVIRONMENT = '1'",
        "Start-Process -FilePath $target -WorkingDirectory $targetDir",
    ])


def install_downloaded_update(downloaded_exe: Path) -> None:
    if not running_from_frozen_exe():
        raise RuntimeError("Auto-install is only available in the packaged exe")

    current_exe = Path(sys.executable).resolve()
    if not current_exe.exists():
        raise RuntimeError(f"Current executable not found: {current_exe}")

    script = _updates_dir() / "install_update.ps1"
    script.write_text(
        _install_script_text(downloaded_exe, current_exe, os.getpid()),
        encoding="utf-8",
    )

    subprocess.Popen(
        [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def format_update_summary(info: UpdateInfo) -> str:
    return (
        f"Version {info.latest_version} is available.\n\n"
        f"Current version: {info.current_version}\n"
        f"Asset: {info.asset_name}"
    )
