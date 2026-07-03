# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules


easyofd_hiddenimports = collect_submodules('easyofd', on_error='ignore')


a = Analysis(
    ['invoice_compiler.py'],
    pathex=['tools/easyofd/easyofd-20260427'],
    binaries=[],
    datas=[
        ('tools/ofd2pdf', 'tools/ofd2pdf'),
        ('tools/easyofd', 'tools/easyofd'),
    ],
    hiddenimports=easyofd_hiddenimports,
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
    name='InvoiceCompiler',
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
)
