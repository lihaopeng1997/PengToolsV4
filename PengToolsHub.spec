# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('resources\\style.qss', 'resources'), ('resources\\chevron_down.svg', 'resources'), ('resources\\check_white.svg', 'resources'), ('resources\\app.ico', 'resources'), ('resources\\app-icon.png', 'resources'), ('resources\\brand', 'resources\\brand'), ('resources\\build_info.json', 'resources'), ('resources\\private_knowledge_seed.txt', 'resources'), ('resources\\private_knowledge_seed_workbooks.json', 'resources'), ('resources\\release_workbook_template.xlsx', 'resources'), ('resources\\icons', 'resources\\icons'), ('resources\\help', 'resources\\help'), ('resources\\ai_skills', 'resources\\ai_skills'), ('resources\\harness', 'resources\\harness')]
binaries = []
hiddenimports = ['docx', 'openpyxl', 'xlrd', 'xlwt', 'xlutils', 'msoffcrypto', 'PyQt6.QtSvg', 'websocket', 'websocket._app', 'mitmproxy', 'mitmproxy.tools.dump', 'mitmproxy.certs', 'mitmproxy.options', 'paramiko', 'cryptography', 'cryptography.hazmat.primitives.kdf.pbkdf2', 'cryptography.hazmat.primitives.kdf', 'cryptography.hazmat.primitives.asymmetric.padding', 'cryptography.hazmat.primitives.hashes', 'cryptography.hazmat.primitives.ciphers', 'cryptography.x509', 'encodings.idna', 'nacl', 'tools.intranet_llm', 'tools.ai_harness', 'tools.ptools_harness', 'tools.linux_guard', 'tools.harness_project', 'oracledb', 'pymysql', 'redis', 'pymongo', 'panels.ai_workbench_panel', 'tools.db_connect', 'tools.sql_guard']
tmp_ret = collect_all('cryptography')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('oracledb')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PySide2', 'PySide6', 'tkinter'],
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
    name='PengToolsHub',
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
    icon=['resources\\brand\\pengtools-taskbar-hc.ico'],
)
