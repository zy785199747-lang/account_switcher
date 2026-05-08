# Riot Client launcher and credential auto-fill.
#
# The "switch account" pipeline is:
#
#   1. Kill anything Riot/League related so the next launch starts clean.
#   2. Delete RiotClientPrivateSettings.yaml so Riot Client doesn't auto-
#      resume the previous session.
#   3. subprocess.Popen the Riot Client with the LoL launch flags.
#   4. Wait for the login window to appear (poll pywinauto, up to N seconds).
#   5. Bring it to the foreground and type:
#        Ctrl+A, Delete, <username>, Tab, Ctrl+A, Delete, <password>, Enter.
#
# Why each piece exists:
#   - Killing is reliable; sending a logout API call is not. We don't have
#     credentials to log out gracefully anyway.
#   - PrivateSettings.yaml is what Riot Client reads to "stay signed in".
#     Removing it forces a fresh login screen on next launch.
#   - pywinauto with the UIA backend can find the window even though Riot
#     Client uses CEF (Chromium) inside, because the window itself is a
#     native Win32 host. We don't reach into the webview — once the window
#     has focus, send_keys delivers keystrokes to whichever field has caret
#     focus. Riot Client opens with the username field focused.

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

import psutil

# pywinauto is a Windows-only dependency. Import lazily so the test suite
# (which we run on any OS via the offscreen Qt platform) can still import
# this module without exploding.
try:
    import pywinauto
    from pywinauto import keyboard as pwa_keyboard
    PYWINAUTO_AVAILABLE = True
except ImportError:  # pragma: no cover
    pywinauto = None
    pwa_keyboard = None
    PYWINAUTO_AVAILABLE = False

# ---------- tweakable constants ----------

DEFAULT_RIOT_INSTALL_PATH = r"C:\Riot Games\Riot Client\RiotClientServices.exe"

# Process names we hunt for and kill. Matched case-insensitively.
RIOT_PROCESS_NAMES = {
    "RiotClientServices.exe",
    "RiotClientUx.exe",
    "RiotClientUxRender.exe",
    "RiotClientCrashHandler.exe",
    "LeagueClient.exe",
    "LeagueClientUx.exe",
    "LeagueClientUxRender.exe",
    "League of Legends.exe",
}

# Where Riot Client persists "stay signed in" data. Killing this file resets
# it. Path is %LOCALAPPDATA%\Riot Games\Riot Client\Data\RiotClientPrivateSettings.yaml.
def _private_settings_path() -> Path:
    base = os.environ.get("LOCALAPPDATA", "")
    return Path(base) / "Riot Games" / "Riot Client" / "Data" / "RiotClientPrivateSettings.yaml"

# Window title regex used by pywinauto to find the Riot Client window.
RIOT_WINDOW_TITLE_REGEX = "Riot Client.*"

# Time budgets.
KILL_WAIT_SECONDS = 5.0      # how long to wait for processes to actually exit
WINDOW_WAIT_SECONDS = 60.0   # how long we'll wait for the login window
WINDOW_POLL_INTERVAL = 1.0
FOCUS_SETTLE_SECONDS = 0.6   # let the field be ready after set_focus
KEY_PAUSE_SECONDS = 0.03     # delay between keystrokes (avoids dropped chars)

log = logging.getLogger(__name__)


# ---------- exceptions ----------

class LauncherError(Exception):
    pass


class RiotClientNotFound(LauncherError):
    # The configured install path doesn't point at RiotClientServices.exe.
    pass


class RiotWindowNotFound(LauncherError):
    # The Riot Client process started but its login window never appeared
    # within the timeout. Usually means Riot is updating itself, or the
    # install is broken.
    pass


class PywinautoUnavailable(LauncherError):
    # pywinauto failed to import. Should never happen on Windows but the
    # message tells the user clearly what's wrong.
    pass


# ---------- progress callback type ----------

# A simple callback the worker can use to push status messages to the UI
# without coupling the launcher to PyQt. The worker (Phase 4) will pass a
# QtSignal-emitting wrapper.
ProgressCb = Callable[[str], None]


def _noop_progress(_msg: str) -> None:
    pass


# ---------- process management ----------

def find_riot_processes() -> list[psutil.Process]:
    # Returns every running Riot/League process. Used by both kill and tests.
    found: list[psutil.Process] = []
    target_lower = {n.lower() for n in RIOT_PROCESS_NAMES}
    for p in psutil.process_iter(["name"]):
        try:
            name = p.info.get("name") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name and name.lower() in target_lower:
            found.append(p)
    return found


def kill_riot_processes(timeout: float = KILL_WAIT_SECONDS) -> int:
    # Kill all matched processes. Returns count attempted.
    procs = find_riot_processes()
    log.info("found %d Riot/League processes", len(procs))
    for p in procs:
        try:
            log.debug("killing pid=%s name=%s", p.pid, p.info.get("name"))
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            log.warning("could not kill pid=%s: %s", p.pid, exc)

    # Wait for them to actually disappear so the next launch isn't fighting
    # zombies still releasing file locks.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not find_riot_processes():
            return len(procs)
        time.sleep(0.2)

    remaining = find_riot_processes()
    if remaining:
        log.warning("after %.1fs, %d Riot processes still running", timeout, len(remaining))
    return len(procs)


# ---------- session clearing ----------

def clear_riot_session() -> bool:
    # Delete the cached "stay signed in" settings file so Riot Client can't
    # auto-resume the previous account. Returns True if a file was removed.
    path = _private_settings_path()
    if not path.exists():
        log.info("no riot private-settings file at %s (already clean)", path)
        return False
    try:
        path.unlink()
        log.info("cleared %s", path)
        return True
    except Exception as exc:
        # Non-fatal. Worst case Riot Client comes up logged into the previous
        # account and the user has to click Sign Out manually.
        log.warning("could not delete %s: %s", path, exc)
        return False


# ---------- launching ----------

def launch_riot_client(
    install_path: str = DEFAULT_RIOT_INSTALL_PATH,
) -> subprocess.Popen:
    # Starts Riot Client. Does NOT wait for its window to appear — caller
    # uses wait_for_login_window() for that.
    if not Path(install_path).exists():
        raise RiotClientNotFound(
            f"Riot Client not found at:\n  {install_path}\n\n"
            f"Set the correct install path in Settings."
        )
    log.info("launching %s", install_path)
    # We don't redirect stdout/stderr; Riot Client logs to its own location.
    return subprocess.Popen([
        install_path,
        "--launch-product=league_of_legends",
        "--launch-patchline=live",
    ])


# ---------- window detection ----------

def wait_for_login_window(timeout: float = WINDOW_WAIT_SECONDS):
    # Polls pywinauto until a window matching RIOT_WINDOW_TITLE_REGEX appears.
    # Returns the wrapped window object.
    if not PYWINAUTO_AVAILABLE:
        raise PywinautoUnavailable(
            "pywinauto is not installed. Run `pip install pywinauto`."
        )

    log.info("waiting up to %.1fs for Riot Client window", timeout)
    deadline = time.monotonic() + timeout
    last_err: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            app = pywinauto.Application(backend="uia").connect(
                title_re=RIOT_WINDOW_TITLE_REGEX, timeout=1
            )
            window = app.top_window()
            # The window object is "lazy" — exists() actually probes it.
            if window.exists():
                log.info("Riot Client window detected")
                return window
        except Exception as exc:
            last_err = exc
        time.sleep(WINDOW_POLL_INTERVAL)

    raise RiotWindowNotFound(
        f"Riot Client window did not appear within {timeout:.0f}s "
        f"(last error: {last_err})"
    )


# ---------- keystroke helpers ----------

# Characters that pywinauto.send_keys treats as syntax. We wrap each in
# braces so a literal "{" or "+" goes through as that character.
_SEND_KEYS_SPECIAL = "{}()+^%~[]"


def _escape_for_send_keys(text: str) -> str:
    out_chunks = []
    for c in text:
        if c in _SEND_KEYS_SPECIAL:
            out_chunks.append("{" + c + "}")
        else:
            out_chunks.append(c)
    return "".join(out_chunks)


def type_credentials(window, username: str, password: str) -> None:
    # Type username and password into Riot Client login form.
    # Riot Client uses Chromium, so we can't find Edit controls via pywinauto.
    # We rely on the window being focused and the username field having focus
    # when the form appears.
    if not PYWINAUTO_AVAILABLE:
        raise PywinautoUnavailable("pywinauto not available")

    log.info("focusing Riot Client window")
    try:
        window.set_focus()
    except Exception as exc:
        log.warning("set_focus failed: %s — continuing anyway", exc)

    # Wait for the page to fully render and form to be interactive
    time.sleep(1.5)

    # Type username. Assume the username field is focused (Riot Client opens with it focused).
    log.info("typing username (length=%d)", len(username))
    escaped_username = _escape_for_send_keys(username)
    log.debug("escaped username: %s", escaped_username)
    pwa_keyboard.send_keys(escaped_username,
                           with_spaces=True, pause=0.08)
    log.info("username sent to focused field")
    time.sleep(0.5)

    # Tab to password field
    log.info("tabbing to password field")
    pwa_keyboard.send_keys("{TAB}", pause=0.08)
    time.sleep(0.5)

    # Type password
    log.info("typing password (length=%d)", len(password))
    escaped_password = _escape_for_send_keys(password)
    log.debug("escaped password length: %d", len(escaped_password))
    pwa_keyboard.send_keys(escaped_password,
                           with_spaces=True, pause=0.08)
    log.info("password sent to focused field")
    time.sleep(0.3)

    # Submit
    pwa_keyboard.send_keys("{ENTER}", pause=0.08)
    log.info("credentials sent")


# ---------- top-level orchestrator ----------

def switch_account(
    username: str,
    password: str,
    install_path: str = DEFAULT_RIOT_INSTALL_PATH,
    progress: ProgressCb = _noop_progress,
    window_timeout: float = WINDOW_WAIT_SECONDS,
) -> None:
    # Single entry point used by SwitchWorker (Phase 4). Each step pushes a
    # progress message so the UI can show what's happening.
    log.info("switch_account: starting (install_path=%s)", install_path)

    # Sanity check the install path early so we don't kill processes only to
    # discover we can't relaunch.
    if not Path(install_path).exists():
        raise RiotClientNotFound(
            f"Riot Client not found at:\n  {install_path}\n\n"
            f"Set the correct install path in Settings."
        )

    progress("Closing Riot Client...")
    kill_riot_processes()

    progress("Clearing previous session...")
    clear_riot_session()

    progress("Launching Riot Client...")
    launch_riot_client(install_path)

    progress("Waiting for login window...")
    window = wait_for_login_window(timeout=window_timeout)

    progress("Filling credentials...")
    type_credentials(window, username, password)

    progress("Logged in.")
    log.info("switch_account: complete")
