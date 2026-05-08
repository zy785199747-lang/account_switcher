# Local Windows build script.
# Produces dist\RiotAccountSwitcher.exe — a portable single-file binary.
#
# Usage:
#   .\build.ps1
#
# Prerequisites:
#   - .venv exists (run `uv venv --python 3.12 .venv` once if not)
#   - Runtime deps installed (`uv pip install -r requirements.txt --python .venv\Scripts\python.exe`)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path .venv)) {
    Write-Host "ERROR: no .venv found. Set up the environment first:" -ForegroundColor Red
    Write-Host "  uv venv --python 3.12 .venv" -ForegroundColor Yellow
    Write-Host "  uv pip install -r requirements.txt --python .venv\Scripts\python.exe" -ForegroundColor Yellow
    exit 1
}

$python = ".venv\Scripts\python.exe"

# uv-created venvs don't ship with pip. Try `uv` first (fast, doesn't need
# pip in the venv); fall back to bootstrapping pip with ensurepip if uv is
# not on PATH (e.g. friends who installed via `python -m venv`).
$uv = Get-Command uv -ErrorAction SilentlyContinue

Write-Host "==> Installing build dependencies (pyinstaller)..." -ForegroundColor Cyan
if ($uv) {
    & uv pip install --upgrade -r requirements-build.txt --python $python
} else {
    # Make sure pip exists in the venv, then use it.
    & $python -m ensurepip --upgrade --quiet
    & $python -m pip install --quiet --upgrade -r requirements-build.txt
}
if ($LASTEXITCODE -ne 0) { Write-Error "Failed to install build deps."; exit $LASTEXITCODE }

# Clean any previous build artifacts so we don't ship stale binaries.
if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist)  { Remove-Item -Recurse -Force dist }

Write-Host "==> Running PyInstaller..." -ForegroundColor Cyan
& $python -m PyInstaller RiotAccountSwitcher.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller failed."; exit $LASTEXITCODE }

$exe = "dist\RiotAccountSwitcher.exe"
if (-not (Test-Path $exe)) {
    Write-Error "Build did not produce $exe"
    exit 1
}

$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "==> Build OK" -ForegroundColor Green
Write-Host "    $exe ($size MB)" -ForegroundColor Green
Write-Host ""
Write-Host "Smoke test it: .\$exe" -ForegroundColor Yellow
