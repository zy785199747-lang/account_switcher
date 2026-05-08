# Phase 4 smoke tests — launcher, worker, MainWindow integration.

import pytest
from src.riot.launcher import (
    _escape_for_send_keys,
    find_riot_processes,
    RIOT_PROCESS_NAMES,
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


def test_switch_worker_constructs():
    # SwitchWorker can be instantiated with typical arguments.
    worker = SwitchWorker(
        username="TestUser",
        password="TestPassword123",
        install_path="C:\\Riot Games\\Riot Client\\RiotClientServices.exe",
    )
    assert worker._username == "TestUser"
    assert worker._password == "TestPassword123"
    assert worker._install_path == "C:\\Riot Games\\Riot Client\\RiotClientServices.exe"


def test_switch_worker_signals():
    # SwitchWorker has the expected PyQt signals.
    worker = SwitchWorker("test", "test", "C:\\nope")
    assert hasattr(worker, "progress")
    assert hasattr(worker, "finished")
    assert hasattr(worker, "failed")


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
