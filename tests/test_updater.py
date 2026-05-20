from pathlib import Path

from src.updater import (
    UpdateInfo,
    _best_windows_asset,
    _install_script_text,
    _ps_single_quote,
    format_update_summary,
    is_newer_version,
)


def test_is_newer_version_handles_v_prefix_and_missing_patch():
    assert is_newer_version("v1.1", "1.0.9")
    assert is_newer_version("v1.0.1", "1.0")
    assert not is_newer_version("v1.0.0", "1.0")
    assert not is_newer_version("v0.9.9", "1.0")


def test_best_windows_asset_prefers_named_exe():
    assets = [
        {
            "name": "notes.txt",
            "browser_download_url": "https://example.invalid/notes.txt",
        },
        {
            "name": "Other.exe",
            "browser_download_url": "https://example.invalid/other.exe",
        },
        {
            "name": "RiotAccountSwitcher.exe",
            "browser_download_url": "https://example.invalid/app.exe",
        },
    ]

    assert _best_windows_asset(assets)["name"] == "RiotAccountSwitcher.exe"


def test_best_windows_asset_returns_none_without_exe():
    assert _best_windows_asset([
        {
            "name": "source.zip",
            "browser_download_url": "https://example.invalid/source.zip",
        }
    ]) is None


def test_ps_single_quote_escapes_apostrophes():
    assert _ps_single_quote(Path("C:/Users/O'Brien/app.exe")) == (
        "'C:\\Users\\O''Brien\\app.exe'"
    )


def test_install_script_waits_before_restart():
    script = _install_script_text(
        Path("C:/tmp/update.exe"),
        Path("C:/Program Files/App/app.exe"),
        123,
    )

    assert "Wait-Process -Id $pidToWait" in script
    assert "Start-Sleep -Seconds 2" in script
    assert "[System.IO.File]::Open" in script
    assert "Get-ChildItem Env:_PYI*" in script
    assert "PYINSTALLER_RESET_ENVIRONMENT" in script
    assert "Start-Process -FilePath $target -WorkingDirectory $targetDir" in script


def test_update_summary_includes_short_release_notes():
    info = UpdateInfo(
        current_version="1.1.2",
        latest_version="v1.1.3",
        release_url="https://example.invalid/release",
        asset_name="RiotAccountSwitcher.exe",
        asset_url="https://example.invalid/app.exe",
        body="# v1.1.3\n\n- Fixed paste reliability\n- Added Stop switch",
    )

    summary = format_update_summary(info)

    assert "What's new:" in summary
    assert "Fixed paste reliability" in summary
    assert "Added Stop switch" in summary
