# PyInstaller spec for the portable single-file Windows build.
#
# Produces dist/RiotAccountSwitcher.exe — a self-contained ~80 MB binary
# that runs on any Windows 10/11 machine without Python installed.
#
# How to build:
#   .venv\Scripts\python.exe -m PyInstaller RiotAccountSwitcher.spec --noconfirm --clean
#
# Or just run build.ps1 which does the above plus dependency install.

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    # Bundle the assets folder so rank_icon.py finds PNGs at runtime under
    # sys._MEIPASS\assets\ranks\. The trailing 'assets' is the destination
    # subdirectory inside the bundle (mirrors the source layout exactly).
    datas=[('assets', 'assets')],
    # PyInstaller usually finds these via static analysis, but listing them
    # explicitly keeps the build reliable across PyQt / cryptography / pywinauto
    # version bumps that sometimes confuse the auto-detector.
    hiddenimports=[
        'pywinauto.application',
        'cryptography.hazmat.primitives.kdf.pbkdf2',
        'cryptography.hazmat.primitives.hashes',
        'cryptography.hazmat.backends.openssl',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim PyQt6 modules we never use. Saves ~30 MB off the final exe.
    excludes=[
        'PyQt6.QtNetwork',
        'PyQt6.QtMultimedia',
        'PyQt6.QtMultimediaWidgets',
        'PyQt6.QtSql',
        'PyQt6.QtBluetooth',
        'PyQt6.QtPositioning',
        'PyQt6.QtNfc',
        'PyQt6.QtSensors',
        'PyQt6.QtSerialPort',
        'PyQt6.Qt3DCore',
        'PyQt6.Qt3DRender',
        'PyQt6.Qt3DInput',
        'PyQt6.Qt3DAnimation',
        'PyQt6.Qt3DLogic',
        'PyQt6.Qt3DExtras',
        'PyQt6.QtCharts',
        'PyQt6.QtDataVisualization',
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebChannel',
        'PyQt6.QtWebSockets',
        'PyQt6.QtPdf',
        'PyQt6.QtPdfWidgets',
        # Test / dev tooling that PyInstaller sometimes pulls in.
        'pytest',
        'tkinter',
        'unittest',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='RiotAccountSwitcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # GUI app — no console window.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/app_icon.ico',
)
