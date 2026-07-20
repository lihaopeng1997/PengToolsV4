# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[('resources\\style.qss', 'resources'), ('resources\\chevron_down.svg', 'resources'), ('resources\\check_white.svg', 'resources'), ('resources\\app.ico', 'resources'), ('resources\\app-icon.png', 'resources'), ('resources\\build_info.json', 'resources'), ('resources\\private_knowledge_seed.txt', 'resources'), ('resources\\private_knowledge_seed_workbooks.json', 'resources'), ('resources\\release_workbook_template.xlsx', 'resources')],
    hiddenimports=['docx', 'openpyxl', 'msoffcrypto'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='PengToolsHub_Private',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['resources\\app.ico'],
)
