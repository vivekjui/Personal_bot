# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['pydantic.v1.fields']
hiddenimports += collect_submodules('webview')
hiddenimports += collect_submodules('chromadb')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('D:\\APMD_Eoffice_Bot\\templates_web', 'templates_web'), ('D:\\APMD_Eoffice_Bot\\static', 'static'), ('D:\\APMD_Eoffice_Bot\\knowledge_base', 'knowledge_base'), ('D:\\APMD_Eoffice_Bot\\cases.db', '.'), ('D:\\APMD_Eoffice_Bot\\config.json', '.'), ('D:\\APMD_Eoffice_Bot\\procurement_stages.json', '.'), ('D:\\APMD_Eoffice_Bot\\standard_library.json', '.')],
    hiddenimports=hiddenimports,
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
    [],
    exclude_binaries=True,
    name='Vivek Bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Vivek Bot',
)
