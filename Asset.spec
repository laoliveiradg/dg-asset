from pathlib import Path


project_root = Path(SPECPATH).resolve()
source_root = project_root / "src"
logo_png = source_root / "image_downloader" / "ui" / "assets" / "asset-logo.png"
app_icon = source_root / "image_downloader" / "ui" / "assets" / "asset.ico"

a = Analysis(
    [str(source_root / "image_downloader" / "__main__.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[(str(logo_png), "image_downloader/ui/assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Asset",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(app_icon)],
    uac_admin=False,
    uac_uiaccess=False,
)
