# Riot Client launcher and credential auto-fill.
#
# The "switch account" pipeline is:
#
#   1. Kill anything Riot/League related so the next launch starts clean.
#   2. Delete RiotClientPrivateSettings.yaml so Riot Client doesn't auto-
#      resume the previous session.
#   3. subprocess.Popen the Riot Client with the LoL launch flags.
#   4. Wait for the login window to appear (poll pywinauto, up to N seconds).
#   5. Bring it to the foreground and fill credentials. Clipboard paste is
#      the default fast path; slow typing remains as a fallback.
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
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import psutil

# pywinauto is a Windows-only dependency. Import lazily so the test suite
# (which we run on any OS via the offscreen Qt platform) can still import
# this module without exploding.
try:
    import pywinauto
    from pywinauto import clipboard as pwa_clipboard
    from pywinauto import Desktop as pwa_desktop
    from pywinauto import keyboard as pwa_keyboard
    PYWINAUTO_AVAILABLE = True
except ImportError:  # pragma: no cover
    pywinauto = None
    pwa_clipboard = None
    pwa_desktop = None
    pwa_keyboard = None
    PYWINAUTO_AVAILABLE = False

# winreg is Windows-only (Python stdlib). Wrap so the module imports on Linux
# during pytest runs.
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    import winreg  # type: ignore[import-not-found]
else:  # pragma: no cover
    ctypes = None  # type: ignore[assignment]
    wintypes = None  # type: ignore[assignment]
    winreg = None  # type: ignore[assignment]

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
WINDOW_POLL_INTERVAL = 0.10
USERNAME_VERIFY_TIMEOUT_SECONDS = 6.0
USERNAME_VERIFY_RETRY_SECONDS = 0.15
FOCUS_SETTLE_SECONDS = 0.6   # let the field be ready after set_focus
KEY_PAUSE_SECONDS = 0.03     # delay between keystrokes (avoids dropped chars)
CLIPBOARD_RETRY_COUNT = 8
CLIPBOARD_RETRY_SECONDS = 0.04
TYPE_FOCUS_SETTLE_SECONDS = 0.20
TYPE_FIELD_SETTLE_SECONDS = 0.10
TYPE_SUBMIT_SETTLE_SECONDS = 0.05
PASTE_FOCUS_SETTLE_SECONDS = 0.05
CLIPBOARD_SETTLE_SECONDS = 0.02
PASTE_SETTLE_SECONDS = 0.02
PASTE_TAB_SETTLE_SECONDS = 0.08
FOCUS_RETRY_SECONDS = 2.0
FOCUS_RETRY_INTERVAL = 0.05
PYWINAUTO_FOCUS_FALLBACK_SECONDS = 0.75

AUTO_FILL_CLIPBOARD = "clipboard"
AUTO_FILL_TYPING = "typing"
AUTO_FILL_MODES = {AUTO_FILL_CLIPBOARD, AUTO_FILL_TYPING}

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


# ---------- install-path auto-detection ----------
#
# Resolution order used by callers:
#   1. Whatever the vault has cached as `riot_install_path` (set after a
#      previous successful detect or via the Settings dialog later).
#   2. find_riot_install_path() — registry → running process → drive scan.
#   3. UI fallback: prompt the user with a file picker.
#
# Each helper returns the absolute path to RiotClientServices.exe, or None.

# Registry paths we probe, in priority order.
_REGISTRY_INSTALL_LOCATION_KEYS = [
    # 64-bit view of the 32-bit installer (Riot ships an x86 installer that
    # lands here on 64-bit Windows).
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Riot Game riot_client.live", "InstallLocation"),
    # 32-bit Windows or recent installs that don't go through the WOW node.
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Riot Game riot_client.live", "InstallLocation"),
]


def _from_registry() -> Optional[str]:
    # Look up Riot Client's install folder via the Windows registry.
    if sys.platform != "win32" or winreg is None:
        return None
    for subkey, value in _REGISTRY_INSTALL_LOCATION_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as k:
                folder, _kind = winreg.QueryValueEx(k, value)
        except OSError:
            continue
        exe = Path(folder) / "RiotClientServices.exe"
        if exe.exists():
            return str(exe)
    # Last resort: the riotclient:// URI handler. The default value of
    # HKEY_CLASSES_ROOT\riotclient\shell\open\command is a command line that
    # starts with the full path in quotes.
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"riotclient\shell\open\command") as k:
            cmd, _kind = winreg.QueryValueEx(k, None)
    except OSError:
        return None
    if isinstance(cmd, str) and cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 1:
            exe = cmd[1:end]
            if Path(exe).exists():
                return exe
    return None


def _from_running_process() -> Optional[str]:
    # Free path: if Riot Client is already running, psutil tells us where its
    # binary lives. Useful when the user previously launched Riot manually.
    target = "riotclientservices.exe"
    for p in psutil.process_iter(["name", "exe"]):
        try:
            name = (p.info.get("name") or "").lower()
            if name != target:
                continue
            exe = p.info.get("exe")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if exe and Path(exe).exists():
            return exe
    return None


def _from_filesystem_search() -> Optional[str]:
    # Scan every fixed drive for the standard install layout.
    # Cheap on modern Windows (drive root listing is O(1)) — only the path
    # existence check actually touches disk.
    if sys.platform != "win32":
        return None
    relative = Path("Riot Games") / "Riot Client" / "RiotClientServices.exe"
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = Path(f"{letter}:/")
        if not drive.exists():
            continue
        candidate = drive / relative
        if candidate.exists():
            return str(candidate)
    return None


def find_riot_install_path() -> Optional[str]:
    # Try the three strategies in order. First hit wins.
    log.info("auto-detecting Riot Client install path")

    found = _from_registry()
    if found:
        log.info("install path via registry: %s", found)
        return found

    found = _from_running_process()
    if found:
        log.info("install path via running process: %s", found)
        return found

    found = _from_filesystem_search()
    if found:
        log.info("install path via filesystem scan: %s", found)
        return found

    log.info("install path: auto-detect failed")
    return None


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


def wait_for_login_window_fast(timeout: float = WINDOW_WAIT_SECONDS):
    # Faster probe than Application.connect(timeout=1): scan the desktop
    # window tree without spending a full second on each failed poll. Try the
    # lighter Win32 backend first because we only need the top-level native
    # window for focusing and send_keys; UIA is the fallback.
    if not PYWINAUTO_AVAILABLE:
        raise PywinautoUnavailable(
            "pywinauto is not installed. Run `pip install pywinauto`."
        )

    log.info("fast-waiting up to %.1fs for Riot Client window", timeout)
    deadline = time.monotonic() + timeout
    last_err: Optional[Exception] = None
    while time.monotonic() < deadline:
        window = _visible_riot_native_window()
        if window is not None:
            log.info("Riot Client window detected via native")
            return window

        for backend in ("win32", "uia"):
            try:
                window = _last_visible_riot_window(backend)
                if window is not None:
                    log.info("Riot Client window detected via %s", backend)
                    return window
            except Exception as exc:
                last_err = exc
        time.sleep(WINDOW_POLL_INTERVAL)

    raise RiotWindowNotFound(
        f"Riot Client window did not appear within {timeout:.0f}s "
        f"(last error: {last_err})"
    )


def _iter_riot_windows():
    # Yield fresh wrappers each time. Riot Client can briefly expose a native
    # window whose handle goes stale while CEF finishes booting; re-querying
    # avoids pasting into nowhere after an invalid-handle focus failure.
    window = _visible_riot_native_window()
    if window is not None:
        yield "native", window

    yield from _iter_pywinauto_riot_windows()


def _iter_pywinauto_riot_windows():
    # Slower pywinauto fallbacks. Kept separate so focusing can try native
    # handles first without accidentally blocking on UIA wrapper creation.

    for backend in ("win32", "uia"):
        window = _last_visible_riot_window(backend)
        if window is not None:
            yield backend, window


class _NativeWindow:
    def __init__(self, hwnd: int):
        self.handle = hwnd
        self._hwnd = hwnd

    def exists(self, timeout=0):
        return _is_window_visible(self.handle)

    def is_visible(self):
        return _is_window_visible(self.handle)

    def set_focus(self):
        if not _focus_window_native(self):
            raise RuntimeError(f"native focus failed for hwnd={self.handle}")


def _is_window_visible(hwnd: int) -> bool:
    if sys.platform != "win32" or ctypes is None:
        return False
    try:
        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return False
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        return width >= 300 and height >= 200
    except Exception:
        return False


def _visible_riot_native_window():
    if sys.platform != "win32" or ctypes is None or wintypes is None:
        return None

    user32 = ctypes.windll.user32
    title_re = re.compile(RIOT_WINDOW_TITLE_REGEX)
    matches: list[int] = []

    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def enum_proc(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = buffer.value
            if title_re.match(title):
                matches.append(int(hwnd))
        except Exception:
            return True
        return True

    try:
        user32.EnumWindows(enum_proc_type(enum_proc), 0)
    except Exception as exc:
        log.debug("native Riot window enumeration failed: %s", exc)
        return None

    for hwnd in reversed(matches):
        if _is_window_visible(hwnd):
            return _NativeWindow(hwnd)
    return None


def _last_visible_riot_window(backend: str):
    # Riot can briefly keep a bootstrap window around while the real CEF login
    # window appears. Prefer the newest visible candidate to avoid spending the
    # focus retry budget on an old handle.
    try:
        windows = pwa_desktop(backend=backend).windows(
            title_re=RIOT_WINDOW_TITLE_REGEX,
            visible_only=True,
            enabled_only=False,
        )
    except Exception as exc:
        log.debug("listing Riot windows via %s failed: %s", backend, exc)
        return _visible_riot_window_probe(backend)

    for window in reversed(windows):
        try:
            if window.exists(timeout=0) and window.is_visible():
                return window
        except Exception as exc:
            log.debug("visible Riot candidate via %s was stale: %s", backend, exc)
    return _visible_riot_window_probe(backend)


def _visible_riot_window_probe(backend: str):
    try:
        window = pwa_desktop(backend=backend).window(
            title_re=RIOT_WINDOW_TITLE_REGEX
        )
        if window.exists(timeout=0) and window.is_visible():
            return window
    except Exception as exc:
        log.debug("window probe via %s failed: %s", backend, exc)
    return None


def focus_riot_window(initial_window=None,
                      timeout: float = FOCUS_RETRY_SECONDS):
    # Focus must actually succeed before we send Ctrl+V or keystrokes.
    # Returning a focused, freshly-valid wrapper is better than continuing
    # after set_focus() fails and pretending the login worked.
    deadline = time.monotonic() + timeout
    pywinauto_fallback_at = time.monotonic() + PYWINAUTO_FOCUS_FALLBACK_SECONDS
    last_err: Optional[Exception] = None

    while time.monotonic() < deadline:
        if initial_window is not None:
            if _focus_window_native(initial_window):
                log.info("Riot Client window focused via detected native")
                return initial_window

        native_window = _visible_riot_native_window()
        if native_window is not None and _focus_window_native(native_window):
            log.info("Riot Client window focused via native")
            return native_window

        if time.monotonic() >= pywinauto_fallback_at:
            for backend, window in _iter_pywinauto_riot_windows():
                if _focus_window_native(window):
                    log.info("Riot Client window focused via %s native", backend)
                    return window
                try:
                    window.set_focus()
                    log.info("Riot Client window focused via %s", backend)
                    return window
                except RuntimeError as exc:
                    # Some pywinauto wrappers raise when a title query matches
                    # both the bootstrap Riot window and the real login window.
                    # Pick the last visible candidate and focus that explicitly.
                    if "There are 2 elements" in str(exc):
                        focused = _focus_last_matching_window(backend)
                        if focused is not None:
                            return focused
                        last_err = exc
                        log.debug("ambiguous focus via %s had no usable candidate: %s",
                                  backend, exc)
                        continue
                    last_err = exc
                    log.debug("set_focus failed via %s: %s", backend, exc)
                except Exception as exc:
                    last_err = exc
                    log.debug("set_focus failed via %s: %s", backend, exc)

        time.sleep(FOCUS_RETRY_INTERVAL)

    raise RiotWindowNotFound(
        f"Riot Client window could not be focused "
        f"(last error: {last_err})"
    )


def _focus_last_matching_window(backend: str):
    window = _last_visible_riot_window(backend)
    if window is None:
        return None
    try:
        window.set_focus()
        log.info("Riot Client window focused via %s candidate", backend)
        return window
    except Exception as exc:
        log.debug("candidate focus via %s failed: %s", backend, exc)
    return None


def _window_handle(window) -> Optional[int]:
    if window is None:
        return None

    direct_handle = getattr(window, "_hwnd", None)
    if direct_handle:
        try:
            return int(direct_handle)
        except (TypeError, ValueError):
            pass

    owners = [window]
    try:
        element_info = getattr(window, "element_info", None)
    except Exception as exc:
        log.debug("could not resolve window element_info for handle: %s", exc)
        element_info = None
    if element_info is not None:
        owners.append(element_info)

    for owner in owners:
        if owner is None:
            continue
        handle = getattr(owner, "handle", None)
        if callable(handle):
            try:
                handle = handle()
            except Exception:
                handle = None
        if handle:
            try:
                return int(handle)
            except (TypeError, ValueError):
                pass

    try:
        wrapper = window.wrapper_object()
    except Exception as exc:
        log.debug("could not resolve window wrapper for handle: %s", exc)
        return None

    handle = getattr(wrapper, "handle", None)
    if callable(handle):
        try:
            handle = handle()
        except Exception:
            return None
    if not handle:
        return None
    try:
        return int(handle)
    except (TypeError, ValueError):
        return None


def _focus_window_native(window) -> bool:
    if sys.platform != "win32" or ctypes is None:
        return False

    hwnd = _window_handle(window)
    if not hwnd:
        return False

    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        show_cmd = 9 if user32.IsIconic(hwnd) else 5  # SW_RESTORE / SW_SHOW
        try:
            user32.ShowWindowAsync(hwnd, show_cmd)
        except Exception:
            user32.ShowWindow(hwnd, show_cmd)

        current_thread = kernel32.GetCurrentThreadId()
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        foreground_hwnd = user32.GetForegroundWindow()
        foreground_thread = (
            user32.GetWindowThreadProcessId(foreground_hwnd, None)
            if foreground_hwnd else 0
        )

        attached_target = False
        attached_foreground = False
        if target_thread and target_thread != current_thread:
            attached_target = bool(user32.AttachThreadInput(
                current_thread,
                target_thread,
                True,
            ))
        if foreground_thread and foreground_thread != current_thread:
            attached_foreground = bool(user32.AttachThreadInput(
                current_thread,
                foreground_thread,
                True,
            ))

        try:
            user32.BringWindowToTop(hwnd)
            focused = bool(user32.SetForegroundWindow(hwnd))
            user32.SetFocus(hwnd)
            time.sleep(0.01)
            return focused or user32.GetForegroundWindow() == hwnd
        finally:
            if attached_foreground:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
            if attached_target:
                user32.AttachThreadInput(current_thread, target_thread, False)
    except Exception as exc:
        log.debug("native focus failed for hwnd=%s: %s", hwnd, exc)
        return False


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
    window = focus_riot_window(window)

    time.sleep(TYPE_FOCUS_SETTLE_SECONDS)

    # Type username. Assume the username field is focused (Riot Client opens with it focused).
    log.info("typing username (length=%d)", len(username))
    escaped_username = _escape_for_send_keys(username)
    log.debug("escaped username: %s", escaped_username)
    pwa_keyboard.send_keys(escaped_username,
                           with_spaces=True, pause=0.08)
    log.info("username sent to focused field")
    time.sleep(TYPE_FIELD_SETTLE_SECONDS)

    # Tab to password field
    log.info("tabbing to password field")
    pwa_keyboard.send_keys("{TAB}", pause=0.08)
    time.sleep(TYPE_FIELD_SETTLE_SECONDS)

    # Type password
    log.info("typing password (length=%d)", len(password))
    escaped_password = _escape_for_send_keys(password)
    log.debug("escaped password length: %d", len(escaped_password))
    pwa_keyboard.send_keys(escaped_password,
                           with_spaces=True, pause=0.08)
    log.info("password sent to focused field")
    time.sleep(TYPE_SUBMIT_SETTLE_SECONDS)

    # Submit
    pwa_keyboard.send_keys("{ENTER}", pause=0.08)
    log.info("credentials sent")


def _clipboard_text() -> str:
    if not PYWINAUTO_AVAILABLE:
        raise PywinautoUnavailable("pywinauto not available")
    try:
        return pwa_clipboard.GetData()
    except Exception as exc:
        log.warning("could not read clipboard before paste fill: %s", exc)
        return ""


def _set_clipboard_text(text: str) -> None:
    if not PYWINAUTO_AVAILABLE:
        raise PywinautoUnavailable("pywinauto not available")
    last_exc: Optional[Exception] = None
    for _attempt in range(CLIPBOARD_RETRY_COUNT):
        try:
            pwa_clipboard.win32clipboard.OpenClipboard()
            try:
                pwa_clipboard.win32clipboard.EmptyClipboard()
                pwa_clipboard.win32clipboard.SetClipboardData(
                    pwa_clipboard.win32clipboard.CF_UNICODETEXT,
                    text,
                )
                return
            finally:
                pwa_clipboard.win32clipboard.CloseClipboard()
        except Exception as exc:
            last_exc = exc
            time.sleep(CLIPBOARD_RETRY_SECONDS)
    raise LauncherError(f"could not set clipboard text: {last_exc}")


def _paste_text(text: str) -> None:
    _set_clipboard_text(text)
    time.sleep(CLIPBOARD_SETTLE_SECONDS)
    pwa_keyboard.send_keys("^v", pause=0.01, vk_packet=False)
    time.sleep(PASTE_SETTLE_SECONDS)


def _focused_text_equals(expected: str) -> bool:
    sentinel = "__RAS_VERIFY_CLIPBOARD_SENTINEL__"
    _set_clipboard_text(sentinel)
    pwa_keyboard.send_keys("^a", pause=0.01, vk_packet=False)
    time.sleep(PASTE_SETTLE_SECONDS)
    pwa_keyboard.send_keys("^c", pause=0.01, vk_packet=False)
    time.sleep(PASTE_SETTLE_SECONDS)
    return _clipboard_text() == expected


def _paste_username_verified(username: str) -> None:
    # This is the important readiness check. Window stability and focus are
    # only proxies; confirming that Ctrl+A/C reads back the username proves
    # the focused control is the username field and that paste actually landed.
    deadline = time.monotonic() + USERNAME_VERIFY_TIMEOUT_SECONDS
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        pwa_keyboard.send_keys("^a{DELETE}", pause=0.01)
        _paste_text(username)
        if _focused_text_equals(username):
            log.info("username paste verified on attempt %d", attempt)
            return

        log.debug("username paste verification failed on attempt %d", attempt)
        pwa_keyboard.send_keys("{TAB}", pause=0.01)
        time.sleep(USERNAME_VERIFY_RETRY_SECONDS)

    raise LauncherError("username field did not accept clipboard paste")


def paste_credentials(window, username: str, password: str) -> None:
    # Faster and less visibly leaky than type_credentials(). We still need
    # Riot Client focused because the login form lives inside a CEF webview,
    # but this reduces the focus-sensitive window to a couple of paste events.
    if not PYWINAUTO_AVAILABLE:
        raise PywinautoUnavailable("pywinauto not available")

    log.info("focusing Riot Client window for clipboard fill")
    window = focus_riot_window(window)

    time.sleep(PASTE_FOCUS_SETTLE_SECONDS)
    original_clipboard = _clipboard_text()
    try:
        log.info("pasting username (length=%d)", len(username))
        _paste_username_verified(username)

        log.info("tabbing to password field")
        pwa_keyboard.send_keys("{TAB}", pause=0.01)
        time.sleep(PASTE_TAB_SETTLE_SECONDS)

        log.info("pasting password (length=%d)", len(password))
        pwa_keyboard.send_keys("^a{DELETE}", pause=0.01)
        _paste_text(password)

        pwa_keyboard.send_keys("{ENTER}", pause=0.01)
        log.info("credentials pasted")
    finally:
        try:
            _set_clipboard_text(original_clipboard)
            log.debug("clipboard restored after paste fill")
        except Exception as exc:
            log.warning("could not restore clipboard after paste fill: %s", exc)


def fill_credentials(window, username: str, password: str,
                     auto_fill_mode: str = AUTO_FILL_CLIPBOARD,
                     progress: ProgressCb = _noop_progress) -> None:
    mode = auto_fill_mode if auto_fill_mode in AUTO_FILL_MODES else AUTO_FILL_CLIPBOARD
    log.info("auto-fill mode selected: %s", mode)
    if mode == AUTO_FILL_TYPING:
        type_credentials(window, username, password)
        return

    try:
        paste_credentials(window, username, password)
    except Exception as exc:
        log.warning("clipboard fill failed; falling back to typing: %s", exc)
        progress("Paste failed; typing credentials...")
        type_credentials(window, username, password)


# ---------- top-level orchestrator ----------

def switch_account(
    username: str,
    password: str,
    install_path: str = DEFAULT_RIOT_INSTALL_PATH,
    progress: ProgressCb = _noop_progress,
    window_timeout: float = WINDOW_WAIT_SECONDS,
    auto_fill_mode: str = AUTO_FILL_CLIPBOARD,
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
    window = wait_for_login_window_fast(timeout=window_timeout)

    if auto_fill_mode == AUTO_FILL_TYPING:
        progress("Typing credentials...")
    else:
        progress("Pasting credentials...")
    fill_credentials(
        window,
        username,
        password,
        auto_fill_mode=auto_fill_mode,
        progress=progress,
    )

    progress("Logged in.")
    log.info("switch_account: complete")
