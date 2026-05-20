# Phase 4 smoke tests — launcher, worker, MainWindow integration.

import pytest
from src.riot.launcher import (
    FOCUS_RETRY_SECONDS,
    FOCUS_RETRY_INTERVAL,
    TYPE_FIELD_SETTLE_SECONDS,
    TYPE_FOCUS_SETTLE_SECONDS,
    TYPE_SUBMIT_SETTLE_SECONDS,
    PYWINAUTO_FOCUS_FALLBACK_SECONDS,
    USERNAME_VERIFY_FOCUS_RETRY_SECONDS,
    USERNAME_VERIFY_RETRY_SECONDS,
    USERNAME_VERIFY_TIMEOUT_SECONDS,
    _escape_for_send_keys,
    _last_visible_riot_window,
    find_riot_processes,
    RIOT_PROCESS_NAMES,
    WINDOW_POLL_INTERVAL,
)
from src.ui.switch_worker import SwitchWorker


def test_launcher_module_imports():
    # launcher.py imports and exports all needed symbols.
    from src.riot.launcher import (
        LauncherError,
        RiotClientNotFound,
        RiotWindowNotFound,
        PywinautoUnavailable,
        kill_riot_processes,
        clear_riot_session,
        launch_riot_client,
        wait_for_login_window,
        type_credentials,
        switch_account,
    )
    assert callable(switch_account)


def test_send_keys_escape():
    # Characters that are special in pywinauto.send_keys should be wrapped in braces.
    # Special chars: { ( ) + ^ % ~ [ ]
    assert _escape_for_send_keys("hello") == "hello"
    assert _escape_for_send_keys("a{b") == "a{{}b"
    assert _escape_for_send_keys("a+b") == "a{+}b"
    assert _escape_for_send_keys("a^b") == "a{^}b"
    assert _escape_for_send_keys("a%b") == "a{%}b"
    assert _escape_for_send_keys("a~b") == "a{~}b"
    assert _escape_for_send_keys("a[b") == "a{[}b"
    assert _escape_for_send_keys("a]b") == "a{]}b"
    assert _escape_for_send_keys("a(b") == "a{(}b"
    assert _escape_for_send_keys("a)b") == "a{)}b"
    # Complex: { + ^ % ~ [ ] ( )
    assert _escape_for_send_keys("test{+^%~[]()") == "test{{}{+}{^}{%}{~}{[}{]}{(}{)}"


class _FakeWindow:
    def __init__(self, visible=True, exists=True):
        self._visible = visible
        self._exists = exists

    def exists(self, timeout=0):
        return self._exists

    def is_visible(self):
        return self._visible


class _FakeDesktop:
    def __init__(self, windows, fallback_window=None):
        self._windows = windows
        self._fallback_window = fallback_window

    def windows(self, **_kwargs):
        return self._windows

    def window(self, **_kwargs):
        return self._fallback_window


def test_last_visible_riot_window_prefers_newest_visible(monkeypatch):
    old_window = _FakeWindow(visible=True)
    hidden_window = _FakeWindow(visible=False)
    newest_window = _FakeWindow(visible=True)

    monkeypatch.setattr(
        "src.riot.launcher.pwa_desktop",
        lambda backend: _FakeDesktop([old_window, hidden_window, newest_window]),
    )

    assert _last_visible_riot_window("win32") is newest_window


def test_last_visible_riot_window_falls_back_to_direct_probe(monkeypatch):
    fallback_window = _FakeWindow(visible=True)

    monkeypatch.setattr(
        "src.riot.launcher.pwa_desktop",
        lambda backend: _FakeDesktop([], fallback_window),
    )

    assert _last_visible_riot_window("win32") is fallback_window


def test_autofill_focus_timing_is_not_six_second_pause():
    assert WINDOW_POLL_INTERVAL <= 0.10
    assert FOCUS_RETRY_INTERVAL <= 0.05
    assert FOCUS_RETRY_SECONDS <= 2.0
    assert TYPE_FOCUS_SETTLE_SECONDS <= 0.20
    assert TYPE_FIELD_SETTLE_SECONDS <= 0.10
    assert TYPE_SUBMIT_SETTLE_SECONDS <= 0.05
    assert PYWINAUTO_FOCUS_FALLBACK_SECONDS <= 0.75
    assert USERNAME_VERIFY_FOCUS_RETRY_SECONDS <= 0.4
    assert USERNAME_VERIFY_RETRY_SECONDS <= 0.15
    assert USERNAME_VERIFY_TIMEOUT_SECONDS <= 6.0


def test_username_paste_retry_does_not_tab_away(monkeypatch):
    import src.riot.launcher as launcher

    sent_keys = []

    monkeypatch.setattr(launcher, "USERNAME_VERIFY_TIMEOUT_SECONDS", 0.18)
    monkeypatch.setattr(launcher, "USERNAME_VERIFY_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(launcher, "USERNAME_VERIFY_FOCUS_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(launcher, "_paste_text", lambda _text: None)
    monkeypatch.setattr(launcher, "_focused_text_equals", lambda _text: False)
    monkeypatch.setattr(launcher, "focus_riot_window", lambda window, timeout: window)
    monkeypatch.setattr(
        launcher.pwa_keyboard,
        "send_keys",
        lambda keys, **_kwargs: sent_keys.append(keys),
    )

    with pytest.raises(launcher.LauncherError):
        launcher._paste_username_verified("TestUser", _FakeWindow())

    assert "{TAB}" not in sent_keys
    assert "^a{DELETE}" in sent_keys


def test_switch_worker_constructs():
    # SwitchWorker can be instantiated with typical arguments.
    worker = SwitchWorker(
        username="TestUser",
        password="TestPassword123",
        install_path="C:\\Riot Games\\Riot Client\\RiotClientServices.exe",
        riot_id="Player#NA1",
    )
    assert worker._username == "TestUser"
    assert worker._password == "TestPassword123"
    assert worker._install_path == "C:\\Riot Games\\Riot Client\\RiotClientServices.exe"
    assert worker._riot_id == "Player#NA1"


def test_switch_worker_signals():
    # SwitchWorker has the expected PyQt signals.
    worker = SwitchWorker("test", "test", "C:\\nope")
    assert hasattr(worker, "progress")
    assert hasattr(worker, "finished")
    assert hasattr(worker, "failed")
    assert hasattr(worker, "cancelled")


def test_main_window_switch_plumbing():
    # MainWindow initializes switch-flow attributes to None and won't crash.
    # We can't easily test MainWindow headless without creating a full vault,
    # so we just verify the imports work and SwitchWorker integration is present.
    from src.ui.main_window import MainWindow
    import inspect

    # Check that _on_switch is defined and references SwitchWorker.
    source = inspect.getsource(MainWindow._on_switch)
    assert "SwitchWorker" in source
    assert "_switch_thread" in source
    assert "_switch_worker" in source
    assert "Stop switch" in source
    assert "_on_switch_cancel_requested" in source
    assert "_switch_cancel_requested = True" in inspect.getsource(
        MainWindow._on_switch_finished
    )
